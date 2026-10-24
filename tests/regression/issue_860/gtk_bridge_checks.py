"""GTK bridge contracts using the real GLib main context and a fake collector."""
import threading
import time
import unittest
from unittest.mock import Mock

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib

from auto_cpufreq.gui.refresh_bridge import GtkRefreshBridge


def pump_until(predicate, timeout=3):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for _ in range(20):
            if not context.pending():
                break
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return False


class GtkBridgeTests(unittest.TestCase):
    def make_bridge(self, collector=None, on_report=None, on_error=None,
                    before_ack=None, after_ack=None):
        self.active = True
        self.records = []
        self.errors = []
        self.diagnostics = Mock()
        bridge = GtkRefreshBridge(
            collector or (lambda: "snapshot"),
            component="GTK test",
            diagnostics=self.diagnostics,
            active=lambda: self.active,
            interval=lambda: 2,
            on_report=on_report or self.records.append,
            on_error=on_error or self.errors.append,
            thread_name="gtk-bridge-test",
            before_ack=before_ack,
            after_ack=after_ack,
        )
        self.addCleanup(bridge.coordinator.close)
        return bridge

    def test_worker_dispatch_only_renders_on_main_context(self):
        main_thread = threading.get_ident()
        collected_on = []
        delivered_on = []

        def collect():
            collected_on.append(threading.get_ident())
            return "data"

        bridge = self.make_bridge(
            collector=collect,
            on_report=lambda value: delivered_on.append(
                (threading.get_ident(), value)
            ),
        )
        bridge.coordinator.request()
        self.assertTrue(pump_until(lambda: bool(delivered_on)))
        self.assertNotEqual(collected_on[0], main_thread)
        self.assertEqual(delivered_on, [(main_thread, "data")])
        self.assertFalse(bridge.coordinator.in_progress)

    def test_broken_logging_does_not_prevent_acknowledgement_or_retry(self):
        bridge = self.make_bridge()
        self.diagnostics.record_duration.side_effect = OSError("log unavailable")
        bridge.coordinator.request()
        self.assertTrue(pump_until(lambda: self.records == ["snapshot"]))
        self.assertFalse(bridge.coordinator.in_progress)
        bridge.coordinator.request()
        self.assertTrue(pump_until(
            lambda: self.records == ["snapshot", "snapshot"]
        ))
        self.assertFalse(bridge.coordinator.in_progress)

    def test_faulty_renderer_and_error_widget_do_not_strand_coordinator(self):
        calls = []

        def render(_value):
            calls.append("render")
            raise ValueError("widget failed")

        def error_ui(_message):
            calls.append("error")
            raise OSError("error widget failed")

        bridge = self.make_bridge(on_report=render, on_error=error_ui)
        bridge.coordinator.request()
        self.assertTrue(pump_until(
            lambda: calls.count("error") == 1
            and not bridge.coordinator.in_progress
        ))
        bridge._on_report = self.records.append
        bridge.coordinator.request()
        self.assertTrue(pump_until(lambda: self.records == ["snapshot"]))
        self.assertFalse(bridge.coordinator.in_progress)

    def test_pending_requests_coalesce_and_complete_before_ack(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        acknowledged = []

        def collect():
            calls.append(len(calls))
            if len(calls) == 1:
                entered.set()
                release.wait(timeout=3)
            return len(calls)

        bridge = self.make_bridge(
            collector=collect,
            before_ack=lambda: acknowledged.append("before"),
            after_ack=lambda: acknowledged.append("after"),
        )
        try:
            bridge.coordinator.request()
            self.assertTrue(entered.wait(timeout=2))
            for _ in range(40):
                bridge.coordinator.request()
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(pump_until(lambda: self.records == [1, 2]))
            self.assertEqual(calls, [0, 1])
            self.assertEqual(acknowledged,
                             ["before", "after", "before", "after"])
            self.assertFalse(bridge.coordinator.in_progress)
        finally:
            release.set()

    def test_close_discards_completed_but_undelivered_result(self):
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()

        def collect():
            entered.set()
            release.wait(timeout=3)
            finished.set()
            return "late"

        bridge = self.make_bridge(collector=collect)
        try:
            bridge.coordinator.request()
            self.assertTrue(entered.wait(timeout=2))
            self.active = False
            bridge.coordinator.close()
            release.set()
            self.assertTrue(finished.wait(timeout=2))
            # Flush outstanding GLib idle callbacks, if any.
            pump_until(lambda: False, timeout=0.10)
            self.assertEqual(self.records, [])
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
