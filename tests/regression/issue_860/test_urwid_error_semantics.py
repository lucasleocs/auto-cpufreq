"""Urwid errors and battery semantics without a real terminal or power writes."""
import threading
import time
from queue import SimpleQueue
from types import SimpleNamespace
import unittest

from bench_urwid_render import load_monitor, report
from auto_cpufreq.modules.report_refresh import RefreshResult, ReportRefreshCoordinator


SystemMonitor, ViewType = load_monitor()


class FakeCoordinator:
    def __init__(self):
        self.acknowledged = []

    def is_current(self, generation):
        return True

    def acknowledge(self, generation):
        self.acknowledged.append(generation)


class FakeDiagnostics:
    def __init__(self):
        self.info_calls = []
        self.warning_calls = []
        self.error = (0, None)

    def info(self, *args):
        self.info_calls.append(args)

    def warning(self, *args):
        self.warning_calls.append(args)

    def error_status(self):
        return self.error


class UrwidConsistencyTests(unittest.TestCase):
    def make(self):
        monitor = SystemMonitor(type=ViewType.STATS)
        monitor.running = True
        monitor._refresh_coordinator = FakeCoordinator()
        monitor._refresh_results = SimpleQueue()
        return monitor

    def test_periodic_urwid_ticks_do_not_queue_busy_successors(self):
        monitor = self.make()
        started = threading.Event()
        release = threading.Event()
        collected, results, alarms = [], [], []
        monitor.loop = SimpleNamespace(
            set_alarm_in=lambda interval, callback: alarms.append(interval)
        )
        monitor._refresh_ready = lambda _data: True

        def collect():
            collected.append(1)
            started.set()
            release.wait(timeout=3)
            return "sample"

        coordinator = ReportRefreshCoordinator(collect, results.append)
        monitor._refresh_coordinator = coordinator
        self.addCleanup(coordinator.close)
        try:
            monitor.update(None, {})
            self.assertTrue(started.wait(timeout=2))
            for _ in range(20):
                monitor.update(None, {})
            self.assertEqual(collected, [1])
            release.set()
            end = time.monotonic() + 2
            while not results and time.monotonic() < end:
                time.sleep(0.01)
            self.assertEqual(len(results), 1)
            self.assertTrue(coordinator.acknowledge(results[0].generation))
            self.assertFalse(coordinator.in_progress)
            self.assertEqual(len(collected), 1)
            self.assertTrue(alarms)
        finally:
            release.set()

    def test_battery_uses_same_unavailable_unknown_semantics_as_gtk(self):
        monitor = self.make()
        sample = report(4)
        sample.battery_info = SimpleNamespace(
            is_charging=None,
            is_ac_plugged=None,
            battery_level=None,
            charging_start_threshold=None,
            charging_stop_threshold=None,
            power_watts=None,
        )
        monitor.format_system_info(sample)
        texts = [
            widget.get_text()[0] for widget in monitor.right_content
            if hasattr(widget, "get_text")
        ]
        self.assertIn("Battery percentage: Unavailable", texts)
        self.assertIn("AC plugged: Unknown", texts)
        self.assertIn("Charging start threshold: Unavailable", texts)
        self.assertIn("Charging stop threshold: Unavailable", texts)

    def test_display_failure_is_recoverable_and_schedules_retry(self):
        monitor = self.make()
        diag = FakeDiagnostics()
        monitor._diagnostics = diag
        monitor._display_report = lambda *_: (_ for _ in ()).throw(
            ValueError("broken terminal widget")
        )
        monitor._refresh_results.put(RefreshResult(
            generation=3,
            report=("report", None, None),
            error=None,
            duration=0.1,
        ))
        try:
            self.assertTrue(monitor._refresh_ready(b"1"))
            self.assertIn("display error", monitor.title_header.text)
            self.assertEqual(
                monitor._refresh_coordinator.acknowledged, [3]
            )
            self.assertTrue(diag.warning_calls)
            self.assertIsNotNone(monitor._alarm_handle)
        finally:
            if monitor._alarm_handle is not None:
                monitor.loop.remove_alarm(monitor._alarm_handle)

    def test_diagnostic_failure_before_render_still_acknowledges(self):
        monitor = self.make()
        diag = FakeDiagnostics()
        diag.info = lambda *args: (_ for _ in ()).throw(
            OSError("diagnostic sink unavailable")
        )
        monitor._diagnostics = diag
        monitor._refresh_results.put(RefreshResult(
            generation=28,
            report=("report", None, None),
            error=None,
            duration=0.01,
        ))
        try:
            self.assertTrue(monitor._refresh_ready(b"1"))
            self.assertEqual(
                monitor._refresh_coordinator.acknowledged, [28]
            )
            self.assertIsNotNone(monitor._alarm_handle)
        finally:
            if monitor._alarm_handle is not None:
                monitor.loop.remove_alarm(monitor._alarm_handle)

    def test_diagnostic_status_failure_still_acknowledges(self):
        monitor = self.make()
        diag = FakeDiagnostics()
        diag.error_status = lambda: (_ for _ in ()).throw(
            ValueError("unexpected diagnostic state")
        )
        monitor._diagnostics = diag
        monitor._refresh_results.put(RefreshResult(
            generation=29,
            report=("report", None, None),
            error=None,
            duration=0.01,
        ))
        try:
            self.assertTrue(monitor._refresh_ready(b"1"))
            self.assertEqual(
                monitor._refresh_coordinator.acknowledged, [29]
            )
        finally:
            if monitor._alarm_handle is not None:
                monitor.loop.remove_alarm(monitor._alarm_handle)

    def test_pipe_write_failure_falls_back_to_periodic_delivery(self):
        from unittest.mock import patch
        monitor = self.make()
        delivered = []
        monitor._display_report = lambda *args: delivered.append(args)
        monitor._refresh_pipe_fd = 123456
        result = RefreshResult(
            generation=19,
            report=("available report", None, None),
            error=None,
            duration=0.01,
        )
        with patch("auto_cpufreq.modules.system_monitor.os.write",
                   side_effect=BrokenPipeError("broken pipe")):
            monitor._dispatch_report_result(result)
        self.assertEqual(delivered, [])
        self.assertIn("broken pipe", monitor._pipe_delivery_error)
        # Simulate Urwid's periodic tick even though the watch pipe failed.
        # Result delivery is still done on the UI thread.
        monitor._refresh_ready(b"")
        self.assertEqual(len(delivered), 1)
        self.assertEqual(monitor._refresh_coordinator.acknowledged, [19])
        self.assertIn("periodic refresh", monitor.footer_text.text)
        if monitor._alarm_handle is not None:
            monitor.loop.remove_alarm(monitor._alarm_handle)

    def test_diagnostic_io_error_is_visible_in_footer(self):
        monitor = self.make()
        diag = FakeDiagnostics()
        diag.error = (1, "Unable to write diagnostic log: full disk")
        monitor._diagnostics = diag
        monitor._surface_diagnostic_error()
        self.assertIn("full disk", monitor.footer_text.text)
        self.assertEqual(monitor._diagnostic_error_shown, 1)
        # A repeat read must not present the same notification again.
        monitor._surface_diagnostic_error()
        self.assertEqual(monitor._diagnostic_error_shown, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
