"""Regression checks for optional GTK thread-start failures and late callbacks.

All actions use mocks; no privileged commands or desktop windows are started.
These tests belong to the experimental validation branch, not the final PR.
"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_cpufreq.gui import diagnostics as diagnostic_module
from auto_cpufreq.gui import objects


class FrontendFailureHardeningTests(unittest.TestCase):
    def test_privileged_worker_start_failure_uses_normal_gui_callback(self):
        callback = Mock(return_value=False)
        with patch.object(objects, "Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no threads")
            with patch.object(objects.GLib, "idle_add") as idle:
                objects._run_privileged_async(["--force=powersave"], callback)
                idle.assert_called_once()
                args = idle.call_args.args
                self.assertIs(args[0], callback)
                self.assertIsNone(args[1])
                self.assertIsInstance(args[2], RuntimeError)
        callback.assert_not_called()  # Main-loop delivery remains asynchronous.

    def test_power_control_late_callback_does_not_access_closed_window(self):
        parent = SimpleNamespace(
            destroyed=False,
            begin_power_state_apply=Mock(return_value=True),
            finish_power_state_apply=Mock(),
        )
        widget = Mock()
        widget.get_toplevel.return_value = parent
        callback = Mock()
        with patch.object(objects, "_run_privileged_async") as dispatch:
            objects._run_power_state_command(
                widget, "governor", ["--force=powersave"], callback
            )
        parent.begin_power_state_apply.assert_called_once_with("governor")
        complete = dispatch.call_args.args[1]
        parent.destroyed = True
        self.assertIs(complete(None, RuntimeError("late")), False)
        parent.finish_power_state_apply.assert_not_called()
        callback.assert_not_called()

    def test_power_control_startup_error_releases_apply_state(self):
        parent = SimpleNamespace(
            destroyed=False,
            begin_power_state_apply=Mock(return_value=True),
            finish_power_state_apply=Mock(),
        )
        widget = Mock()
        widget.get_toplevel.return_value = parent
        callback = Mock(return_value=False)
        with patch.object(objects, "_run_privileged_async") as dispatch:
            objects._run_power_state_command(
                widget, "turbo", ["--turbo=always"], callback
            )
        complete = dispatch.call_args.args[1]
        error = RuntimeError("cannot start thread")
        complete(None, error)
        parent.finish_power_state_apply.assert_called_once_with("turbo", False)
        callback.assert_called_once_with(None, error)

    def test_saved_logging_thread_failure_does_not_interrupt_gui_start(self):
        with TemporaryDirectory() as temp:
            settings = Path(temp) / "config" / "auto-cpufreq" / "gtk-debug.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"debug_logging": True}))
            with patch.dict(os.environ, {
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
            }):
                instance = diagnostic_module.GuiDiagnostics()
                with patch.object(
                    instance, "_activate", side_effect=RuntimeError("no threads")
                ):
                    with patch.object(instance, "_report_error") as report:
                        instance.attach_mainloop(Mock())
                        report.assert_called_once()
                        self.assertIsInstance(report.call_args.args[0], RuntimeError)
                self.assertFalse(instance.enabled)

    def test_partial_diagnostic_activation_cleans_up_on_watchdog_failure(self):
        instance = diagnostic_module.GuiDiagnostics()
        def pretend_open_log():
            instance.enabled = True
        with patch.object(
            diagnostic_module.SessionDiagnostics,
            "_activate", side_effect=pretend_open_log,
        ):
            with patch.object(instance, "_start_heartbeat"):
                with patch.object(diagnostic_module, "Thread") as thread:
                    thread.return_value.start.side_effect = RuntimeError("watchdog")
                    with patch.object(instance, "_deactivate") as deactivate:
                        with self.assertRaisesRegex(RuntimeError, "watchdog"):
                            instance._activate()
                        deactivate.assert_called_once_with(wait=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
