"""Deterministic races when a refresh worker cannot be scheduled.

The tests simulate a failing queue submission and a concurrent request.
They never perform hardware reads or start a GTK event loop.
"""
import threading
import unittest
from unittest.mock import patch

from auto_cpufreq.modules import report_refresh


class WorkerStartRaceTests(unittest.TestCase):
    def _coordinator(self):
        self.received = []
        self.completed = threading.Event()

        def dispatch(result):
            self.received.append(result)
            coordinator.acknowledge(result.generation)
            self.completed.set()

        coordinator = report_refresh.ReportRefreshCoordinator(
            lambda: "snapshot", dispatch
        )
        return coordinator

    def test_initial_worker_failure_clears_concurrent_pending_request(self):
        coordinator = self._coordinator()
        submitted = threading.Event()
        release = threading.Event()
        failures = []

        def enqueue(*_args):
            submitted.set()
            self.assertTrue(release.wait(2), "Worker start was not released")
            raise RuntimeError("simulated thread start failure")

        def first_request():
            try:
                coordinator.request()
            except RuntimeError as error:
                failures.append(str(error))

        try:
            with patch.object(report_refresh._PROCESS_LANE, "enqueue", enqueue):
                worker = threading.Thread(target=first_request)
                worker.start()
                self.assertTrue(submitted.wait(2))
                # Arrive after the reservation but before enqueue() fails.
                self.assertFalse(coordinator.request())
                self.assertTrue(coordinator._pending)
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())

            self.assertEqual(failures, ["simulated thread start failure"])
            self.assertFalse(coordinator._pending)
            self.assertFalse(coordinator.in_progress)
            self.assertTrue(coordinator.request())
            self.assertTrue(self.completed.wait(2))
            self.assertEqual(len(self.received), 1)
            self.assertFalse(coordinator.in_progress)
        finally:
            release.set()
            coordinator.close()

    def test_successor_worker_failure_clears_concurrent_pending_request(self):
        ready = threading.Event()
        errors = []
        deliveries = []

        def dispatch(result):
            deliveries.append(result)
            ready.set()

        coordinator = report_refresh.ReportRefreshCoordinator(
            lambda: "snapshot", dispatch,
            on_dispatch_error=lambda error: errors.append(str(error)),
        )
        submitted = threading.Event()
        release = threading.Event()

        def enqueue(*_args):
            submitted.set()
            self.assertTrue(release.wait(2), "Successor was not released")
            raise RuntimeError("simulated successor thread failure")

        try:
            self.assertTrue(coordinator.request())
            self.assertTrue(ready.wait(2))
            self.assertFalse(coordinator.request())  # Reserve a successor.
            previous_generation = deliveries[0].generation

            with patch.object(report_refresh._PROCESS_LANE, "enqueue", enqueue):
                ack = threading.Thread(
                    target=lambda: coordinator.acknowledge(previous_generation)
                )
                ack.start()
                self.assertTrue(submitted.wait(2))
                # A second explicit request races with successor start.
                self.assertFalse(coordinator.request())
                self.assertTrue(coordinator._pending)
                release.set()
                ack.join(2)
                self.assertFalse(ack.is_alive())

            self.assertEqual(errors, ["simulated successor thread failure"])
            self.assertFalse(coordinator._pending)
            self.assertFalse(coordinator.in_progress)

            ready.clear()
            self.assertTrue(coordinator.request())
            self.assertTrue(ready.wait(2))
            self.assertEqual(len(deliveries), 2)
            self.assertTrue(
                coordinator.acknowledge(deliveries[-1].generation)
            )
            self.assertFalse(coordinator.in_progress)
        finally:
            release.set()
            coordinator.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
