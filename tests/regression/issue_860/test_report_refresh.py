import threading
import time
import unittest

from auto_cpufreq.modules.report_refresh import ReportRefreshCoordinator


def wait_until(predicate, timeout=3):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class RefreshCoordinatorTests(unittest.TestCase):
    def test_coalesces_many_requests_into_one_followup(self):
        entered = threading.Event()
        release = threading.Event()
        seen = []
        dispatched = []

        def collect():
            seen.append(1)
            if len(seen) == 1:
                entered.set()
                release.wait(2)
            return object()

        coordinator = ReportRefreshCoordinator(collect, dispatched.append)
        self.addCleanup(coordinator.close)
        self.assertTrue(coordinator.request())
        self.assertTrue(entered.wait(1))
        for _ in range(50):
            self.assertFalse(coordinator.request())
        self.assertEqual(len(seen), 1)
        release.set()
        self.assertTrue(wait_until(lambda: len(dispatched) == 1))
        self.assertTrue(coordinator.in_progress)
        self.assertTrue(coordinator.acknowledge(dispatched[0].generation))
        self.assertTrue(wait_until(lambda: len(dispatched) == 2))
        self.assertEqual(len(seen), 2)
        self.assertTrue(dispatched[1].succeeded)
        self.assertTrue(coordinator.acknowledge(dispatched[1].generation))
        self.assertFalse(coordinator.in_progress)

    def test_temporary_error_does_not_poison_next_refresh(self):
        calls = []
        results = []

        def collect():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("sensor temporarily absent")
            return "recovered"

        coordinator = ReportRefreshCoordinator(collect, results.append)
        self.addCleanup(coordinator.close)
        self.assertTrue(coordinator.request())
        self.assertTrue(wait_until(lambda: len(results) == 1))
        self.assertFalse(results[0].succeeded)
        self.assertIn("sensor temporarily absent", str(results[0].error))
        coordinator.acknowledge(results[0].generation)
        self.assertTrue(coordinator.request())
        self.assertTrue(wait_until(lambda: len(results) == 2))
        self.assertEqual(results[1].report, "recovered")
        self.assertTrue(results[1].succeeded)
        coordinator.acknowledge(results[1].generation)

    def test_close_during_collection_discards_result(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        received = []

        def collect():
            started.set()
            release.wait(2)
            finished.set()
            return "late report"

        coordinator = ReportRefreshCoordinator(
            collect, received.append, thread_name="test-discard-late-report"
        )
        self.assertTrue(coordinator.request())
        self.assertTrue(started.wait(1))
        coordinator.close()
        release.set()
        self.assertTrue(finished.wait(2))
        # The collector signals 'finished' before the coordinator has
        # necessarily evaluated its closed-generation gate. Wait for
        # its worker to exit before asserting no callback was delivered.
        self.assertTrue(wait_until(
            lambda: not any(
                t.name == "test-discard-late-report" and t.is_alive()
                for t in threading.enumerate()
            )
        ))
        self.assertFalse(coordinator.request())
        self.assertFalse(coordinator.in_progress)
        self.assertFalse(received)

    def test_no_request_overlapping_undelivered_result(self):
        received = []
        count = []
        def collect():
            count.append(1)
            return "result"
        coordinator = ReportRefreshCoordinator(collect, received.append)
        self.addCleanup(coordinator.close)
        self.assertTrue(coordinator.request())
        self.assertTrue(wait_until(lambda: len(received) == 1))
        self.assertFalse(coordinator.request())
        self.assertFalse(coordinator.request())
        self.assertEqual(len(count), 1)
        self.assertTrue(coordinator.is_current(received[0].generation))
        self.assertTrue(coordinator.acknowledge(received[0].generation))
        self.assertTrue(wait_until(lambda: len(received) == 2))
        self.assertEqual(len(count), 2)
        self.assertFalse(coordinator.acknowledge(received[0].generation))
        coordinator.acknowledge(received[1].generation)

    def test_dispatch_error_does_not_strand_coordinator(self):
        called = []
        def notify(_result):
            called.append(1)
            raise RuntimeError("display closed")
        coordinator = ReportRefreshCoordinator(lambda: 1, notify)
        self.addCleanup(coordinator.close)
        self.assertTrue(coordinator.request())
        self.assertTrue(wait_until(lambda: called))
        self.assertTrue(wait_until(lambda: not coordinator.in_progress))
        self.assertTrue(coordinator.request())

    def test_only_one_process_collector_across_two_views(self):
        first_entered = threading.Event()
        first_release = threading.Event()
        second_entered = threading.Event()
        first_results, second_results = [], []
        concurrent = []
        active = []
        lock = threading.Lock()

        def collect_one():
            with lock:
                active.append(1)
                concurrent.append(len(active))
            first_entered.set()
            first_release.wait(timeout=3)
            with lock:
                active.pop()
            return "one"

        def collect_two():
            with lock:
                active.append(2)
                concurrent.append(len(active))
            second_entered.set()
            with lock:
                active.pop()
            return "two"

        one = ReportRefreshCoordinator(collect_one, first_results.append)
        two = ReportRefreshCoordinator(collect_two, second_results.append)
        self.addCleanup(one.close)
        self.addCleanup(two.close)
        try:
            self.assertTrue(one.request())
            self.assertTrue(first_entered.wait(timeout=1))
            self.assertTrue(two.request())
            self.assertFalse(second_entered.wait(timeout=0.1))
            self.assertEqual(max(concurrent), 1)
        finally:
            first_release.set()
        self.assertTrue(wait_until(lambda: len(second_results) == 1))
        self.assertEqual(max(concurrent), 1)
        one.acknowledge(first_results[0].generation)
        two.acknowledge(second_results[0].generation)

    def test_close_queued_view_never_starts_collection(self):
        entered, release = threading.Event(), threading.Event()
        queued_calls = []
        primary = ReportRefreshCoordinator(
            lambda: (entered.set(), release.wait(2), "done")[-1],
            lambda _result: None,
        )
        obsolete = ReportRefreshCoordinator(
            lambda: queued_calls.append(1),
            lambda _result: None,
        )
        self.addCleanup(primary.close)
        self.addCleanup(obsolete.close)
        try:
            primary.request()
            self.assertTrue(entered.wait(1))
            obsolete.request()
            obsolete.close()
        finally:
            release.set()
        primary.close()
        from auto_cpufreq.modules import report_refresh
        self.assertTrue(wait_until(
            lambda: report_refresh._PROCESS_LANE._worker is None,
            timeout=2,
        ))
        self.assertEqual(queued_calls, [])

    def test_dispatch_errors_are_reported_to_callback(self):
        reported = []
        def fail(_result):
            raise OSError("closed display pipe")
        coordinator = ReportRefreshCoordinator(
            lambda: "data", fail, on_dispatch_error=reported.append
        )
        self.addCleanup(coordinator.close)
        self.assertTrue(coordinator.request())
        self.assertTrue(wait_until(lambda: len(reported) == 1))
        self.assertIn("closed display pipe", str(reported[0]))
        self.assertTrue(wait_until(lambda: not coordinator.in_progress))

    def test_no_gui_or_policy_runtime_imports(self):
        from pathlib import Path
        from auto_cpufreq.modules import report_refresh
        src = Path(report_refresh.__file__).read_text()
        for forbidden in ("import gi", "import urwid", "set_autofreq",
                          "cpufreqctl", "power_helper"):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
