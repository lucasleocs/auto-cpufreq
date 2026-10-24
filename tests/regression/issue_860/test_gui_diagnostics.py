import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
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


class GuiDiagnosticsTests(unittest.TestCase):
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
        self.assertIn(
            "instrumentation sample", diagnostics.log_path.read_text()
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
        diagnostics.shutdown()
        files = sorted(diagnostics.log_path.parent.glob("gtk.log*"))
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
        content = diagnostics.log_path.read_text()
        self.assertEqual(content.count("unresponsive for at least"), 1)
        self.assertTrue(glib.callback())
        self.assertFalse(diagnostics._stall_reported)
        diagnostics.shutdown()
        self.assertEqual(glib.removed, [134])

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
