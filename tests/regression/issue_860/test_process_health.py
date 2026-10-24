"""Deterministic read-only Linux process health sampler regression tests."""
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
import tempfile
import time
import unittest

from auto_cpufreq.modules import process_health as h


class FakeLog:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def warning(self, text, *args):
        self.warnings.append(text % args)

    def info(self, text, *args):
        self.infos.append(text % args)


def snap(seconds, process_ticks, thread_ticks=None, starttime=123,
         rss=12.5, threads=3, fds=8):
    return h.Snapshot(
        monotonic=seconds, ticks=process_ticks, starttime=starttime,
        task_ticks={tid: (ticks, starttime + tid)
                    for tid, ticks in (thread_ticks or {}).items()},
        rss_mib=rss, threads=threads, fds=fds,
    )


class HealthSamplerTests(unittest.TestCase):
    def test_cpu_fraction_is_percent_of_one_logical_core(self):
        first = snap(0, 100, {100: 70, 101: 30})
        last = snap(5, 600, {100: 470, 101: 130})
        result = h.compute_health(first, last, ticks_per_second=100)
        self.assertAlmostEqual(result.cpu_percent, 100.0)
        self.assertEqual(result.hottest_tid, 100)
        self.assertAlmostEqual(result.hottest_thread_percent, 80.0)
        self.assertEqual(result.rss_mib, 12.5)
        self.assertEqual(result.fds, 8)

    def test_rejects_same_pid_with_new_starttime(self):
        self.assertIsNone(h.compute_health(
            snap(0, 100, starttime=1),
            snap(5, 700, starttime=2),
        ))

    def test_ignores_recycled_thread_id(self):
        old = snap(0, 100, {123: 80})
        new = h.Snapshot(
            monotonic=5, ticks=600, starttime=123,
            task_ticks={123: (480, 1000)},
            rss_mib=5.0, threads=1, fds=3,
        )
        result = h.compute_health(old, new, ticks_per_second=100)
        self.assertIsNone(result.hottest_tid)
        self.assertEqual(result.hottest_thread_percent, 0)

    def test_prime_first_sample_then_high_cpu_episode(self):
        logger = FakeLog()
        sampler = h.ProcessHealthSampler(
            logger, high_threshold=60, consecutive=3,
            summary_interval=999,
        )
        self.assertIsNone(sampler.observe(snap(0, 0, {123: 0})))
        for sec in range(1, 5):
            sampler.observe(snap(
                sec, sec * h.CLOCK_TICKS,
                {123: sec * h.CLOCK_TICKS},
            ))
        self.assertEqual(len(logger.warnings), 1)
        self.assertIn("hottest_tid=123", logger.warnings[0])
        # Quiet CPU ends the same episode, without repeated high alerts.
        sampler.observe(snap(5, 4 * h.CLOCK_TICKS,
                             {123: 4 * h.CLOCK_TICKS}))
        self.assertEqual(len(logger.infos), 1)
        self.assertIn("episode ended", logger.infos[0])

    def test_summary_throttles_healthy_samples(self):
        logger = FakeLog()
        sampler = h.ProcessHealthSampler(
            logger, consecutive=2, summary_interval=4,
        )
        for sec in range(7):
            sampler.observe(snap(sec, sec * h.CLOCK_TICKS // 10))
        self.assertFalse(logger.warnings)
        self.assertEqual(len(logger.infos), 1)
        self.assertIn("Process health: 4 samples", logger.infos[0])

    def test_invalid_intervals_and_negative_delta(self):
        self.assertIsNone(h.compute_health(snap(2, 10), snap(2, 20)))
        self.assertIsNone(h.compute_health(snap(2, 10), snap(3, 5)))

    def test_parse_proc_comm_with_spaces_parenthesis(self):
        # /proc stat starts with pid, "(comm)" followed by field 3.
        fields = ["S"] + ["0"] * 48
        fields[11], fields[12], fields[19] = "100", "20", "1234"
        raw = f"42 (a silly ) worker) {' '.join(fields)}"
        self.assertEqual(h.parse_task_stat(raw), (120, 1234))

    def test_process_root_snapshot_handles_thread_disappearance(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "4321"
            task = root / "task" / "4321"
            task.mkdir(parents=True)
            fields = ["S"] + ["0"] * 48
            fields[11], fields[12], fields[19] = "42", "8", "300"
            (root / "stat").write_text(f"4321 (main) {' '.join(fields)}")
            (task / "stat").write_text(f"4321 (main) {' '.join(fields)}")
            (root / "status").write_text("VmRSS:\t8192 kB\nThreads:\t1\n")
            (root / "fd").mkdir()
            (root / "fd" / "0").write_text("")
            result = h.read_snapshot(
                pid=4321, proc_root=Path(d), clock=lambda: 8.0
            )
            self.assertEqual(result.ticks, 50)
            self.assertEqual(result.starttime, 300)
            self.assertEqual(result.task_ticks[4321], (50, 300))
            self.assertEqual(result.rss_mib, 8.0)
            self.assertEqual(result.threads, 1)
            self.assertEqual(result.fds, 1)
            self.assertEqual(result.monotonic, 8.0)

    def test_high_cpu_alert_captures_bounded_python_thread_stack(self):
        logger = FakeLog()
        entered, release = Event(), Event()

        def worker_body():
            entered.set()
            release.wait(timeout=2)

        worker = Thread(target=worker_body, name="debug-trace-target")
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            tid = worker.native_id
            sampler = h.ProcessHealthSampler(
                logger, high_threshold=10, consecutive=2,
                summary_interval=999,
            )
            for moment in range(3):
                sampler.observe(snap(
                    moment, moment * h.CLOCK_TICKS,
                    {tid: moment * h.CLOCK_TICKS},
                ))
            self.assertTrue(any(
                "High CPU Python thread stack" in line and
                "debug-trace-target" in line
                for line in logger.warnings
            ))
        finally:
            release.set()
            worker.join(timeout=2)

    def test_real_busy_python_thread_appears_in_procfs_samples(self):
        started, stop = Event(), Event()

        def burn_cpu():
            started.set()
            acc = 1
            while not stop.is_set():
                acc = (acc * 37 + 11) % 1000003

        worker = Thread(target=burn_cpu, name="synthetic-busy-test")
        worker.start()
        try:
            self.assertTrue(started.wait(timeout=1))
            previous = h.read_snapshot()
            time.sleep(0.25)
            current = h.read_snapshot()
            result = h.compute_health(previous, current)
            self.assertIsNotNone(result)
            self.assertGreater(
                result.cpu_percent, 10.0,
                "Synthetic busy thread did not receive enough CPU to measure",
            )
            self.assertEqual(
                result.hottest_tid, worker.native_id,
                "Expected the synthetic Python worker to be the hottest TID",
            )
        finally:
            stop.set()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())

    def test_stop_removes_sampling_thread(self):
        logger = FakeLog()
        calls = []
        def read():
            calls.append(1)
            return snap(float(len(calls)), len(calls) * 5)
        sampler = h.ProcessHealthSampler(
            logger, read=read, interval=0.02
        )
        sampler.start()
        deadline = time.monotonic() + 1.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(calls)
        sampler.stop()
        self.assertFalse(any(
            thread.name == "process-health-debug" and thread.is_alive()
            for thread in __import__("threading").enumerate()
        ))

    def test_unexpected_sampler_failures_terminate_after_bounded_retries(self):
        logger = FakeLog()
        failures = []
        def broken_read():
            failures.append(1)
            raise RuntimeError("synthetic unexpected sampler error")
        sampler = h.ProcessHealthSampler(
            logger, read=broken_read, interval=0.005
        )
        sampler.start()
        deadline = time.monotonic() + 2
        while sampler.is_alive() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertFalse(sampler.is_alive())
        self.assertEqual(len(failures), 3)
        self.assertEqual(len(logger.warnings), 2)
        self.assertIn("stopped after 3", logger.warnings[-1])
        sampler.stop()

    def test_unexpected_failure_streak_resets_after_success(self):
        logger = FakeLog()
        events = [
            RuntimeError("fault 1"), snap(1, 10),
            RuntimeError("fault 2"), snap(2, 20),
            RuntimeError("fault 3"), snap(3, 30),
            RuntimeError("fault 4"), snap(4, 40),
        ]

        class FakeStop:
            def __init__(self):
                self.count = 0

            def is_set(self):
                return self.count >= 8

            def wait(self, _interval):
                self.count += 1
                return self.is_set()

        def read():
            result = events.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        # Counter tracks consecutive unexpected failures, not total
        # failures accumulated throughout a long running GUI session.
        stop = FakeStop()
        sampler = h.ProcessHealthSampler(logger, read=read)
        sampler._run(stop)
        self.assertEqual(stop.count, 8)
        self.assertEqual(sampler._unexpected_errors_total, 4)
        self.assertEqual(sampler._fatal_errors, 0)
        self.assertFalse(any("stopped after" in w for w in logger.warnings))
        self.assertEqual(sum("recovered after" in i for i in logger.infos), 4)

    def test_sampling_gap_rearms_high_cpu_warning(self):
        logger = FakeLog()
        sampler = h.ProcessHealthSampler(
            logger, high_threshold=40, consecutive=2,
            summary_interval=999,
        )
        for sec in range(3):
            sampler.observe(snap(sec, sec * h.CLOCK_TICKS))
        self.assertTrue(sampler._in_episode)
        self.assertEqual(sum("Sustained process CPU:" in w
                             for w in logger.warnings), 1)
        # Bad/duplicate timestamps break the measurement chain.
        self.assertIsNone(sampler.observe(snap(2, 2 * h.CLOCK_TICKS)))
        self.assertFalse(sampler._in_episode)
        self.assertEqual(sampler._above, 0)
        self.assertIsNone(sampler._previous)
        for sec in range(3, 6):
            sampler.observe(snap(sec, sec * h.CLOCK_TICKS))
        self.assertEqual(sum("Sustained process CPU:" in w
                             for w in logger.warnings), 2)

    def test_procfs_error_invalidates_existing_cpu_episode(self):
        logger = FakeLog()
        results = [
            snap(0, 0), snap(1, h.CLOCK_TICKS),
            snap(2, 2 * h.CLOCK_TICKS), OSError("proc disappeared"),
            snap(3, 3 * h.CLOCK_TICKS),
            snap(4, 4 * h.CLOCK_TICKS), snap(5, 5 * h.CLOCK_TICKS),
        ]

        class Stop:
            count = 0

            def is_set(self):
                return self.count >= 7

            def wait(self, _):
                self.count += 1
                return self.is_set()

        def read():
            value = results.pop(0)
            if isinstance(value, Exception):
                raise value
            return value

        sampler = h.ProcessHealthSampler(
            logger, read=read, high_threshold=40, consecutive=2,
            summary_interval=999,
        )
        sampler._run(Stop())
        self.assertEqual(sum("Sustained process CPU:" in w
                             for w in logger.warnings), 2)
        self.assertTrue(any("episode interrupted" in x
                            for x in logger.infos))

    def test_blocked_sampler_cannot_be_restarted_until_old_worker_exits(self):
        logger = FakeLog()
        entered, release = Event(), Event()

        def blocking_read():
            entered.set()
            release.wait(timeout=3)
            return snap(1.0, 0)

        sampler = h.ProcessHealthSampler(
            logger, read=blocking_read, interval=0.01
        )
        sampler.start()
        try:
            self.assertTrue(entered.wait(timeout=1))
            self.assertFalse(sampler.stop())
            self.assertTrue(sampler.is_alive())
            with self.assertRaisesRegex(OSError, "still stopping"):
                sampler.start()
        finally:
            release.set()
        deadline = time.monotonic() + 2
        while sampler.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(sampler.is_alive())
        sampler.start()
        sampler.stop()

    def test_repeated_procfs_failures_are_throttled_and_recovery_logged(self):
        logger = FakeLog()
        failures = [OSError("procfs hidden")] * 4
        failures.append(snap(1.0, 0))

        def read():
            if not failures:
                return snap(time.monotonic(), 0)
            result = failures.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        sampler = h.ProcessHealthSampler(
            logger, read=read, interval=0.005
        )
        sampler.start()
        try:
            deadline = time.monotonic() + 2
            while (
                (failures or not logger.infos)
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
        finally:
            sampler.stop()
        self.assertEqual(len(logger.warnings), 1)
        self.assertIn("4 failed reads", logger.infos[0])

    def test_sampling_thread_not_created_until_enabled(self):
        sampler = h.ProcessHealthSampler(FakeLog())
        self.assertIsNone(sampler._worker)
        self.assertIsNone(sampler._stop)
