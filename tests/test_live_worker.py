import sys
import unittest
from queue import SimpleQueue
from unittest.mock import patch

from auto_cpufreq.bin import auto_cpufreq
from auto_cpufreq.modules.system_monitor import SystemMonitor


class LiveWorkerTests(unittest.TestCase):
    def test_worker_surfaces_failure_and_restores_stdout(self):
        class Monitor:
            def __init__(self):
                self.errors = []

            def report_error(self, error):
                self.errors.append(error)

        monitor = Monitor()
        original_stdout = sys.stdout

        with (
            patch.object(auto_cpufreq.time, "sleep", return_value=None),
            patch.object(
                auto_cpufreq,
                "set_autofreq",
                side_effect=RuntimeError("live update failed"),
            ),
        ):
            auto_cpufreq._run_live_daemon(monitor)

        self.assertIs(sys.stdout, original_stdout)
        self.assertEqual(len(monitor.errors), 1)
        self.assertIsInstance(monitor.errors[0], RuntimeError)

    def test_monitor_queues_external_worker_error(self):
        monitor = object.__new__(SystemMonitor)
        monitor._refresh_results = SimpleQueue()
        monitor._refresh_pipe_fd = None
        error = RuntimeError("worker failed")

        monitor.report_error(error)
        report, governor, turbo, queued_error = monitor._refresh_results.get_nowait()

        self.assertIsNone(report)
        self.assertIsNone(governor)
        self.assertIsNone(turbo)
        self.assertIs(queued_error, error)


if __name__ == "__main__":
    unittest.main()
