"""Phase 2E: stress real GTK Monitor create/destroy and source ownership.

Runs under Xvfb with synthetic reports. No service or sysfs writes.
"""
import threading
import time
import unittest
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from gtk_logging_ui_checks import WIDGETS
from auto_cpufreq.gui import monitor_view as MONITOR_VIEW
from auto_cpufreq.modules.report_recommendations import RecommendationStatus as State


class FakeReportView(Gtk.Box):
    def __init__(self):
        super().__init__()
        self.reports = []

    def apply_report(self, report):
        self.reports.append(report)

    def prepend_right(self, widget):
        self.pack_start(widget, False, False, 0)


class GtkLifecycleAuditTests(unittest.TestCase):
    @staticmethod
    def report():
        return SimpleNamespace(
            current_gov="powersave", is_turbo_on=(True, None)
        )

    def test_thirty_two_monitor_reopens_do_not_leak_timer_sources(self):
        parent = Gtk.Window()
        original = WIDGETS.system_info
        original_monitor = MONITOR_VIEW.system_info
        MONITOR_VIEW.system_info = WIDGETS.system_info = SimpleNamespace(
            generate_system_report=self.report,
            monitor_recommendations=lambda report: SimpleNamespace(
                governor=SimpleNamespace(suggested="powersave", status=State.NO_CHANGE),
                turbo=SimpleNamespace(suggested=True, status=State.NO_CHANGE),
            ),
        )
        context = GLib.MainContext.default()
        sources = []
        try:
            for _ in range(32):
                view = WIDGETS.MonitorModeView(
                    parent, FakeReportView(), refresh_interval=1
                )
                parent.add(view)
                source = view.refresh_id
                self.assertIsNotNone(source)
                source_object = context.find_source_by_id(source)
                self.assertIsNotNone(source_object)
                sources.append(source_object)
                # Destroy invokes the view's cleanup without requiring Back.
                view.destroy()
                self.assertTrue(view._refresh_coordinator.closed)
                self.assertIsNone(view.refresh_id)
                self.assertTrue(source_object.is_destroyed())
                # An explicit cleanup after destroy is safe.
                view.cleanup()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                while context.pending():
                    context.iteration(False)
                if not any(
                    thread.is_alive() and
                    thread.name == "gtk-monitor-refresh"
                    for thread in threading.enumerate()
                ):
                    break
                time.sleep(0.01)
            self.assertFalse([
                t.name for t in threading.enumerate()
                if t.is_alive() and t.name == "gtk-monitor-refresh"
            ])
            self.assertEqual(len(sources), 32)
            for source_object in sources:
                self.assertTrue(source_object.is_destroyed())
        finally:
            parent.destroy()
            WIDGETS.system_info = original
            MONITOR_VIEW.system_info = original_monitor

    def test_broken_diagnostics_do_not_start_extra_polling_timer(self):
        # The GUI periodically checks health via the same refresh source.
        # Diagnostic failures cannot spawn an independent timer.
        from unittest.mock import patch
        from auto_cpufreq.gui.refresh_timer import GtkRefreshTimer

        state = SimpleNamespace(in_progress=True)
        state.request_age_seconds = lambda: 50
        logging = SimpleNamespace(
            warning=lambda *args: (_ for _ in ()).throw(
                OSError("log unavailable")
            ),
            info=lambda *args: None,
        )
        collected = []
        timer = GtkRefreshTimer(
            coordinator=state,
            request=lambda: collected.append(1),
            active=lambda: True,
            diagnostics=logging,
            component="GTK stress",
            interval=1,
        )
        timer.start()
        source = timer.source_id
        source_object = GLib.MainContext.default().find_source_by_id(source)
        self.assertIsNotNone(source_object)
        try:
            for _ in range(100):
                self.assertTrue(timer._tick())
            self.assertEqual(collected, [])
            self.assertEqual(timer.source_id, source)
            self.assertIsNotNone(
                GLib.MainContext.default().find_source_by_id(source)
            )
        finally:
            timer.stop()
        self.assertTrue(source_object.is_destroyed())


if __name__ == "__main__":
    unittest.main(verbosity=2)
