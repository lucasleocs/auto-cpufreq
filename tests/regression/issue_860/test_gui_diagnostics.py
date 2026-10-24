import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import logging
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "auto_cpufreq/gui/diagnostics.py"
)
SPEC = importlib.util.spec_from_file_location(
    "_issue860_gui_diagnostics", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MockGLib:
    def __init__(self):
        self.created = []
        self.removed = []
        self.callback = None

    def timeout_add_seconds(self, seconds, callback):
        self.created.append(seconds)
        self.callback = callback
        return 134

    def source_remove(self, source_id):
        self.removed.append(source_id)

    def idle_add(self, callback, *args):
        callback(*args)
        return 135


class GuiDiagnosticsTests(unittest.TestCase):
    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.assertTrue(predicate(), "Async logger did not complete in time")

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(Path(self.root.name) / "config"),
                "XDG_STATE_HOME": str(Path(self.root.name) / "state"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.diagnostics = MODULE.GuiDiagnostics()
        self.addCleanup(self.diagnostics.shutdown)

    def test_default_off_creates_no_files(self):
        self.assertFalse(self.diagnostics.enabled)
        self.assertFalse(self.diagnostics.log_path.exists())
        self.assertFalse(self.diagnostics.settings_path.exists())

    def test_enable_disable_and_persist_choice(self):
        diagnostics = self.diagnostics
        glib = MockGLib()
        diagnostics.attach_mainloop(glib)
        diagnostics.set_enabled(True)
        self.assertTrue(diagnostics.enabled)
        self.assertEqual(glib.created, [MODULE.HEARTBEAT_SECONDS])
        self.assertTrue(diagnostics.log_path.exists())
        self.assertTrue(json.loads(
            diagnostics.settings_path.read_text()
        )["debug_logging"])
        self.assertEqual(
            diagnostics.log_path.stat().st_mode & 0o777, 0o600
        )
        diagnostics.debug("instrumentation sample")
        self.wait_for(
            lambda: "instrumentation sample" in diagnostics.log_path.read_text()
        )

        diagnostics.shutdown()
        self.assertEqual(glib.removed, [134])
        self.assertFalse(diagnostics.enabled)
        self.assertTrue(
            json.loads(
                diagnostics.settings_path.read_text()
            )["debug_logging"]
        )

        next_session = MODULE.GuiDiagnostics()
        self.addCleanup(next_session.shutdown)
        self.assertTrue(next_session.enabled)
        next_session.set_enabled(False)
        self.assertFalse(json.loads(
            next_session.settings_path.read_text()
        )["debug_logging"])

    def test_rotation_is_bounded(self):
        diagnostics = self.diagnostics
        diagnostics.set_enabled(True)
        message = "sample " + ("x" * 18000)
        for _ in range(240):
            diagnostics.info("%s", message)
        self.wait_for(
            lambda: len(list(diagnostics.log_path.parent.glob(
                diagnostics.log_path.name + "*"
            ))) > 1
        )
        diagnostics.shutdown()
        files = sorted(diagnostics.log_path.parent.glob(
            diagnostics.log_path.name + "*"
        ))
        self.assertLessEqual(len(files), MODULE.LOG_BACKUPS + 1)
        self.assertGreater(len(files), 1)
        for filename in files:
            self.assertLessEqual(
                filename.stat().st_size, MODULE.MAX_LOG_BYTES
            )
            self.assertEqual(filename.stat().st_mode & 0o777, 0o600)

    def test_main_loop_stall_is_logged_once_until_heartbeat(self):
        diagnostics = self.diagnostics
        glib = MockGLib()
        diagnostics.attach_mainloop(glib)
        diagnostics.set_enabled(True)
        diagnostics._last_heartbeat = (
            time.monotonic() - MODULE.STALL_SECONDS - 2
        )
        diagnostics._check_stall()
        diagnostics._check_stall()
        self.wait_for(
            lambda: "unresponsive for at least" in diagnostics.log_path.read_text()
        )
        content = diagnostics.log_path.read_text()
        self.assertEqual(content.count("unresponsive for at least"), 1)
        self.assertTrue(glib.callback())
        self.assertFalse(diagnostics._stall_reported)
        diagnostics.shutdown()
        self.assertEqual(glib.removed, [134])

    def test_suspend_gap_is_distinguished_from_main_loop_delay(self):
        diagnostics = self.diagnostics
        glib = MockGLib()
        diagnostics.attach_mainloop(glib)
        diagnostics.set_enabled(True)
        diagnostics._last_boottime = 100.0
        diagnostics._last_heartbeat = time.monotonic() - 5.0
        with patch.object(MODULE, "_boottime", return_value=150.0):
            self.assertTrue(glib.callback())
        self.wait_for(
            lambda: "System suspend/resume detected" in diagnostics.log_path.read_text()
        )
        content = diagnostics.log_path.read_text()
        self.assertIn("System suspend/resume detected", content)
        self.assertNotIn("unresponsive for at least", content)

    def test_watchdog_thread_detects_blocked_main_thread(self):
        diagnostics = self.diagnostics
        diagnostics.attach_mainloop(MockGLib())
        with patch.object(MODULE, "HEARTBEAT_SECONDS", 0.05), patch.object(
            MODULE, "STALL_SECONDS", 0.2
        ):
            diagnostics.set_enabled(True)
            deadline = time.monotonic() + 2
            found = False
            while time.monotonic() < deadline:
                time.sleep(0.05)
                if "unresponsive for at least" in diagnostics.log_path.read_text():
                    found = True
                    break
            self.assertTrue(found, "Watchdog did not detect blocked GTK loop")
            diagnostics.shutdown()

    def test_queue_is_bounded_and_nonblocking_under_slow_disk(self):
        diagnostics = self.diagnostics
        with patch.object(
            MODULE._PrivateRotatingFileHandler,
            "emit",
            autospec=True,
            side_effect=lambda *args: time.sleep(0.008),
        ):
            diagnostics.set_enabled(True)
            started = time.monotonic()
            for index in range(1500):
                diagnostics.debug("saturated queue %d", index)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 1.5)
            self.assertLessEqual(diagnostics._events.qsize(), MODULE.QUEUE_SIZE)
            self.assertGreater(diagnostics._dropped, 0)
            diagnostics.shutdown()

    def test_repeated_toggle_uses_distinct_session_logs(self):
        diagnostics = self.diagnostics
        paths = []
        for index in range(4):
            diagnostics.set_enabled(True)
            diagnostics.info("session %d", index)
            paths.append(diagnostics.log_path)
            diagnostics.set_enabled(False)
        self.assertEqual(len(set(paths)), len(paths))
        for path in paths:
            self.assertTrue(path.exists())
        self.assertFalse(diagnostics.enabled)

    def test_multiple_instances_do_not_share_log_files(self):
        second = MODULE.GuiDiagnostics()
        self.addCleanup(second.shutdown)
        self.assertNotEqual(self.diagnostics.log_path, second.log_path)
        self.diagnostics.set_enabled(True)
        second.set_enabled(True)
        self.diagnostics.info("first-instance")
        second.info("second-instance")
        self.wait_for(
            lambda: "first-instance" in self.diagnostics.log_path.read_text()
            and "second-instance" in second.log_path.read_text()
        )
        self.assertNotIn("second-instance", self.diagnostics.log_path.read_text())
        self.assertNotIn("first-instance", second.log_path.read_text())

    def test_async_file_failure_is_exposed_to_ui(self):
        diagnostics = self.diagnostics
        ui_errors = []
        diagnostics.attach_mainloop(MockGLib())
        diagnostics.set_error_callback(ui_errors.append)
        with patch.object(
            MODULE._PrivateRotatingFileHandler,
            "emit",
            side_effect=OSError("simulated disk failure"),
        ):
            diagnostics.set_enabled(True)
            diagnostics.info("trigger writer failure")
            self.wait_for(lambda: bool(ui_errors))
        self.assertIn("simulated disk failure", ui_errors[0])
        diagnostics.shutdown()

    def test_enable_rolls_back_when_preference_write_fails(self):
        diagnostics = self.diagnostics
        with patch.object(
            diagnostics, "_save_preference", side_effect=OSError("readonly")
        ):
            with self.assertRaisesRegex(OSError, "readonly"):
                diagnostics.set_enabled(True)
        self.assertFalse(diagnostics.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
