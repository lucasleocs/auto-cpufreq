"""GTK Monitor integration against a virtual X server: no policy writes."""
import threading
import time
import unittest
from unittest.mock import patch, PropertyMock
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from gtk_logging_ui_checks import WIDGETS
from auto_cpufreq.gui import monitor_view as MONITOR_VIEW


def pump_until(predicate, timeout=4):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return False


class ReportView(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.applied = []

    def apply_report(self, report):
        self.applied.append(report)

    def prepend_right(self, widget):
        self.pack_start(widget, False, False, 0)


class EntryPointWindow(Gtk.Window):
    """Exercise real GTK navigation widgets, with only sysfs reads mocked."""
    def __init__(self):
        super().__init__()
        self.report_views = []
        self.daemon_not_running()

    def daemon_not_running(self):
        self.box = WIDGETS.DaemonNotRunningView(self)
        self.add(self.box)

    def monitor_mode(self):
        report_view = ReportView()
        self.report_views.append(report_view)
        self.monitor_view = WIDGETS.MonitorModeView(
            self, report_view, refresh_interval=1
        )
        self.add(self.monitor_view)


class MonitorRefreshTests(unittest.TestCase):
    def setUp(self):
        self.parent = Gtk.Window()
        self.addCleanup(self.parent.destroy)
        self.views = []
        self.original_info = WIDGETS.system_info
        self.original_monitor_info = MONITOR_VIEW.system_info
        self.addCleanup(self._restore_info)

    def _restore_info(self):
        for view in self.views:
            view.cleanup()
        WIDGETS.system_info = self.original_info
        MONITOR_VIEW.system_info = self.original_monitor_info

    def make(self, collector):
        MONITOR_VIEW.system_info = WIDGETS.system_info = SimpleNamespace(
            generate_system_report=collector,
            monitor_recommendations=lambda report: SimpleNamespace(
                governor=SimpleNamespace(suggested="powersave"),
                turbo=SimpleNamespace(
                    suggested=True if report.is_turbo_on[0] is not None else None
                ),
            ),
        )
        report_view = ReportView()
        view = WIDGETS.MonitorModeView(
            self.parent, report_view, refresh_interval=1
        )
        self.views.append(view)
        return view, report_view

    @staticmethod
    def report():
        return SimpleNamespace(current_gov="powersave", is_turbo_on=(True, None))

    def test_actual_daemon_absent_buttons_navigate_monitor_and_back(self):
        MONITOR_VIEW.system_info = WIDGETS.system_info = SimpleNamespace(
            generate_system_report=self.report,
            monitor_recommendations=lambda report: SimpleNamespace(
                governor=SimpleNamespace(suggested="powersave"),
                turbo=SimpleNamespace(
                    suggested=True if report.is_turbo_on[0] is not None else None
                ),
            ),
        )
        root = EntryPointWindow()
        self.addCleanup(root.destroy)
        for _ in range(3):
            root.box.monitor_button.clicked()
            self.assertTrue(pump_until(
                lambda: bool(root.report_views[-1].applied)
            ))
            monitor = root.monitor_view
            monitor.back_button.clicked()
            self.assertTrue(monitor._refresh_coordinator.closed)
            self.assertIsInstance(root.box, WIDGETS.DaemonNotRunningView)

    def test_monitor_uses_same_timer_for_refresh_and_stall_health(self):
        view, renderer = self.make(lambda: self.report())
        self.assertIsNotNone(view.refresh_id)
        with patch.object(type(view._refresh_coordinator), "in_progress",
                          new_callable=PropertyMock, return_value=True), \
             patch.object(view._refresh_coordinator,
                          "request_age_seconds", return_value=25), \
             patch.object(WIDGETS.diagnostics, "warning") as warning:
            self.assertTrue(view._refresh_timer._tick())
            self.assertTrue(view._refresh_timer._tick())
            warning.assert_called_once()
        view.cleanup()
        self.assertIsNone(view.refresh_id)
        self.assertFalse(view._refresh_timer._tick())

    def test_destroy_window_closes_monitor_and_cancels_timer(self):
        view, renderer = self.make(lambda: self.report())
        self.parent.add(view)
        self.assertTrue(pump_until(lambda: len(renderer.applied) >= 1))
        timer = view.refresh_id
        self.assertIsNotNone(timer)
        self.parent.destroy()
        self.assertFalse(view.running)
        self.assertTrue(view._refresh_coordinator.closed)
        self.assertIsNone(view.refresh_id)
        self.assertIsNone(GLib.MainContext.default().find_source_by_id(timer))
        view.cleanup()  # Both cleanup paths are allowed.

    def test_destroy_window_during_blocked_collection_discards_result(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def collect():
            started.set()
            release.wait(timeout=3)
            finished.set()
            return self.report()

        view, renderer = self.make(collect)
        self.parent.add(view)
        try:
            self.assertTrue(started.wait(timeout=2))
            self.parent.destroy()
            self.assertFalse(view.running)
            self.assertTrue(view._refresh_coordinator.closed)
            self.assertIsNone(view.refresh_id)
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            pump_until(lambda: False, timeout=0.1)
            self.assertEqual(renderer.applied, [])
        finally:
            release.set()

    def test_monitor_diagnostic_failure_does_not_strand_delivery(self):
        with patch.object(WIDGETS.diagnostics, "record_duration",
                          side_effect=OSError("simulated logging failure")):
            view, renderer = self.make(lambda: self.report())
            self.assertTrue(pump_until(
                lambda: not view._refresh_coordinator.in_progress,
                timeout=2,
            ), "Diagnostics error stranded GTK Monitor delivery")
        view._refresh_timer.stop()
        view.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(renderer.applied) >= 1))

    def test_periodic_monitor_recovers_after_renderer_and_error_ui_fail(self):
        view, renderer = self.make(lambda: self.report())
        self.assertTrue(pump_until(lambda: len(renderer.applied) >= 1))
        before = len(renderer.applied)
        # The collector succeeds, but both presentation paths fail in
        # this cycle. The periodic timer must remain the source of retries.
        with patch.object(view._refresh_bridge, "_on_report",
                          side_effect=ValueError("synthetic renderer fault")), \
             patch.object(view._refresh_bridge, "_on_error",
                          side_effect=OSError("synthetic error widget fault")):
            self.assertTrue(view._refresh_timer._tick())
            self.assertTrue(pump_until(
                lambda: not view._refresh_coordinator.in_progress
            ))
        self.assertIsNotNone(view.refresh_id)
        self.assertTrue(view._refresh_timer._tick())
        self.assertTrue(pump_until(
            lambda: len(renderer.applied) == before + 1
        ))
        self.assertFalse(view._refresh_coordinator.in_progress)

    def test_refresh_recovers_after_temporary_error(self):
        attempts = []
        def collect():
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("synthetic monitor sensor error")
            return self.report()

        view, renderer = self.make(collect)
        self.assertTrue(pump_until(lambda: view.error_label.get_visible()))
        self.assertIn("synthetic monitor sensor error",
                      view.error_label.get_text())
        self.assertFalse(view._refresh_coordinator.in_progress)
        # Drive the next tick, removing the real timer so it does not leak.
        view._refresh_timer.stop()
        view.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(renderer.applied) >= 1))
        self.assertFalse(view.error_label.get_visible())
        self.assertEqual(len(attempts), 2)
        view.cleanup()
        self.assertTrue(view._refresh_coordinator.closed)
        self.assertIsNone(view.refresh_id)

    def test_repeated_monitor_open_close_ends_old_collectors(self):
        started = threading.Event()
        release = threading.Event()
        views = []

        calls = []
        def slow_collect():
            calls.append(1)
            started.set()
            release.wait(timeout=3)
            return self.report()

        try:
            for index in range(5):
                view, renderer = self.make(slow_collect)
                views.append((view, renderer))
                if index == 0:
                    self.assertTrue(started.wait(timeout=1))
                view.cleanup()
            # Four more views were queued then closed. None may begin a
            # concurrent collection while the original is blocked.
            self.assertEqual(len(calls), 1)
            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                while GLib.MainContext.default().pending():
                    GLib.MainContext.default().iteration(False)
                alive = [
                    t for t in threading.enumerate()
                    if t.name == "gtk-monitor-refresh" and t.is_alive()
                ]
                if not alive:
                    break
                time.sleep(0.02)
            self.assertFalse(
                [t for t in threading.enumerate()
                 if t.name == "gtk-monitor-refresh" and t.is_alive()]
            )
            for view, renderer in views:
                self.assertTrue(view._refresh_coordinator.closed)
                self.assertFalse(renderer.applied)
        finally:
            release.set()

    def test_close_during_collection_drops_late_results(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def collect():
            started.set()
            release.wait(timeout=3)
            finished.set()
            return self.report()

        view, renderer = self.make(collect)
        try:
            self.assertTrue(started.wait(timeout=2))
            view.cleanup()
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            pump_until(lambda: True, timeout=0.2)
            self.assertEqual(renderer.applied, [])
            self.assertTrue(view._refresh_coordinator.closed)
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
