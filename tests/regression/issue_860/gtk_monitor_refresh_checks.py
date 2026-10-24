"""GTK Monitor integration against a virtual X server: no policy writes."""
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from gtk_logging_ui_checks import WIDGETS


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


class MonitorRefreshTests(unittest.TestCase):
    def setUp(self):
        self.parent = Gtk.Window()
        self.addCleanup(self.parent.destroy)
        self.views = []
        self.original_info = WIDGETS.system_info
        self.addCleanup(self._restore_info)

    def _restore_info(self):
        for view in self.views:
            view.cleanup()
        WIDGETS.system_info = self.original_info

    def make(self, collector):
        WIDGETS.system_info = SimpleNamespace(
            generate_system_report=collector,
            governor_suggestion=lambda report: "powersave",
            turbo_on_suggestion=lambda report: True,
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
        self.assertTrue(view._refresh_coordinator.in_progress is False)
        # Drive the scheduled next tick without waiting for its timer.
        view.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(renderer.applied) >= 1))
        self.assertFalse(view.error_label.get_visible())
        self.assertEqual(len(attempts), 2)
        view.cleanup()
        self.assertTrue(view._refresh_coordinator.closed)
        self.assertIsNone(view.refresh_id)

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
