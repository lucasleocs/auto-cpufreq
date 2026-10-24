"""Focused regression checks for optional GTK diagnostics and Urwid layout.

These tests do not launch the daemon, open GUI windows, or change power policy.
"""
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class FrontendPolishTests(unittest.TestCase):
    def test_configuration_label_does_not_claim_daemon_configuration(self):
        from auto_cpufreq.gui.app import _configuration_label_text

        namespace = _configuration_label_text.__globals__
        with patch.dict(namespace, {
            "config": SimpleNamespace(
                path="/etc/auto-cpufreq.conf",
                has_config=lambda: True,
            )
        }):
            self.assertEqual(
                _configuration_label_text(),
                "Configuration file: /etc/auto-cpufreq.conf",
            )

        with patch.dict(namespace, {
            "config": SimpleNamespace(
                path="/missing/auto-cpufreq.conf",
                has_config=lambda: False,
            )
        }):
            self.assertEqual(
                _configuration_label_text(),
                "Configuration file (not found): /missing/auto-cpufreq.conf",
            )

    def test_debug_log_dialog_can_copy_full_path(self):
        from gi.repository import Gdk, Gtk
        from auto_cpufreq.gui import objects

        path = Path("/tmp/auto-cpufreq/state/gtk-session.log")
        with patch.object(objects, "diagnostics",
                          SimpleNamespace(log_path=path, enabled=False)):
            with patch.object(objects.Gtk, "MessageDialog") as dialog_class:
                dialog = dialog_class.return_value
                dialog.run.return_value = Gtk.ResponseType.APPLY
                objects.DropDownMenu._show_debug_log_location(
                    None, None, None
                )
                detail = dialog.format_secondary_text.call_args.args[0]
                self.assertIn("File: gtk-session.log", detail)
                self.assertIn("Directory: /tmp/auto-cpufreq/state", detail)
                dialog.add_button.assert_any_call(
                    "Copy full path", Gtk.ResponseType.APPLY
                )
                dialog.add_button.assert_any_call(
                    "Open folder", Gtk.ResponseType.YES
                )
                self.assertEqual(dialog.add_button.call_count, 2)
                dialog.get_clipboard.assert_called_once_with(
                    Gdk.SELECTION_CLIPBOARD
                )
                dialog.get_clipboard.return_value.set_text.assert_called_once_with(
                    str(path), -1
                )
                dialog.destroy.assert_called_once()

    def test_log_dialog_open_folder_action_uses_log_parent(self):
        from gi.repository import Gtk
        from auto_cpufreq.gui import objects

        window = object()
        path = Path("/tmp/logs with spaces/session.log")
        with patch.object(objects, "diagnostics",
                          SimpleNamespace(log_path=path, enabled=False)):
            with patch.object(objects.Gtk, "MessageDialog") as dialog_class:
                with patch.object(objects, "open_containing_folder") as opener:
                    dialog = dialog_class.return_value
                    dialog.run.return_value = Gtk.ResponseType.YES
                    objects.DropDownMenu._show_debug_log_location(
                        None, None, window
                    )
                    opener.assert_called_once_with(
                        window, path,
                        missing_hint=(
                            "Enable debug logging first to create "
                            "the log directory."
                        ),
                    )
                    dialog.get_clipboard.assert_not_called()
                    dialog.destroy.assert_called_once()

    def test_log_dialog_explains_uncreated_session(self):
        from tempfile import TemporaryDirectory
        from gi.repository import Gtk
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-created" / "future.log"
            with patch.object(objects, "diagnostics",
                              SimpleNamespace(log_path=path, enabled=False)):
                with patch.object(
                    objects.Gtk, "MessageDialog"
                ) as dialog_class:
                    dialog_class.return_value.run.return_value = (
                        Gtk.ResponseType.CLOSE
                    )
                    objects.DropDownMenu._show_debug_log_location(
                        None, None, None
                    )
                    detail = dialog_class.return_value.format_secondary_text
                    text = detail.call_args.args[0]
                    self.assertIn("Directory: ", text)
                    self.assertIn("No log file yet.", text)
                    self.assertIn("Enable debug logging", text)
                    self.assertFalse(path.parent.exists())

    def test_log_dialog_identifies_active_and_finished_sessions(self):
        from tempfile import TemporaryDirectory
        from gi.repository import Gtk
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.log"
            path.write_text("diagnostics")
            for enabled, status in (
                (True, "Debug logging is enabled."),
                (False, "Showing the last session path."),
            ):
                with self.subTest(enabled=enabled):
                    with patch.object(
                        objects, "diagnostics",
                        SimpleNamespace(log_path=path, enabled=enabled)
                    ):
                        with patch.object(
                            objects.Gtk, "MessageDialog"
                        ) as dialog_class:
                            dialog_class.return_value.run.return_value = (
                                Gtk.ResponseType.CLOSE
                            )
                            objects.DropDownMenu._show_debug_log_location(
                                None, None, None
                            )
                            text = (
                                dialog_class.return_value
                                .format_secondary_text.call_args.args[0]
                            )
                            self.assertIn(status, text)

    def test_log_dialog_reports_missing_file_while_enabled(self):
        from tempfile import TemporaryDirectory
        from gi.repository import Gtk
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "session-not-created.log"
            with patch.object(
                objects, "diagnostics",
                SimpleNamespace(log_path=path, enabled=True)
            ):
                with patch.object(
                    objects.Gtk, "MessageDialog"
                ) as dialog_class:
                    dialog_class.return_value.run.return_value = (
                        Gtk.ResponseType.CLOSE
                    )
                    objects.DropDownMenu._show_debug_log_location(
                        None, None, None
                    )
                    detail = (
                        dialog_class.return_value
                        .format_secondary_text.call_args.args[0]
                    )
                    self.assertIn(
                        "Log file unavailable. Check for logging errors.",
                        detail,
                    )

    def test_log_folder_missing_hint_explains_how_to_create_it(self):
        from tempfile import TemporaryDirectory
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "not-created" / "session.log"
            with patch.object(objects.Gtk, "MessageDialog") as dialog_class:
                with patch.object(objects.Gtk, "show_uri_on_window") as show:
                    self.assertFalse(objects.open_containing_folder(
                        None, log_path,
                        missing_hint="Enable debug logging first.",
                    ))
                show.assert_not_called()
                text = (
                    dialog_class.return_value
                    .format_secondary_text.call_args.args[0]
                )
                self.assertIn(str(log_path.parent), text)
                self.assertIn("Enable debug logging first.", text)

    def test_open_containing_folder_uses_desktop_uri_handler(self):
        from tempfile import TemporaryDirectory
        from gi.repository import Gdk
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "folder with spaces"
            directory.mkdir()
            file_path = directory / "example.log"
            with patch.object(
                objects.Gtk, "show_uri_on_window", return_value=True
            ) as show:
                self.assertTrue(objects.open_containing_folder(None, file_path))
            show.assert_called_once_with(
                None, directory.as_uri(), Gdk.CURRENT_TIME
            )

    def test_open_containing_folder_reports_missing_directory(self):
        from tempfile import TemporaryDirectory
        from auto_cpufreq.gui import objects

        with TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "missing-directory" / "file.log"
            with patch.object(objects.Gtk, "MessageDialog") as dialog_class:
                with patch.object(objects.Gtk, "show_uri_on_window") as show:
                    self.assertFalse(
                        objects.open_containing_folder(None, file_path)
                    )
                show.assert_not_called()
                dialog = dialog_class.return_value
                self.assertIn(
                    "Directory does not exist",
                    dialog.format_secondary_text.call_args.args[0],
                )
                dialog.show_all.assert_called_once()
                dialog.run.assert_not_called()

    def test_config_folder_action_uses_selected_path(self):
        from auto_cpufreq.gui import app

        window = object()
        with patch.object(app, "open_containing_folder") as opener:
            with patch.object(app.config, "path", "/etc/auto-cpufreq.conf"):
                app.ToolWindow._open_config_folder(window, None)
        opener.assert_called_once_with(window, "/etc/auto-cpufreq.conf")

    def test_urwid_cpu_table_does_not_wrap_in_standard_split_pane(self):
        import urwid
        from auto_cpufreq.modules import system_monitor

        report = SimpleNamespace(
            distro_name="TestLinux",
            distro_ver="1",
            kernel_version="test",
            processor_model="Synthetic CPU",
            total_core=16,
            arch="x86_64",
            cpu_driver="intel_pstate",
            cpu_max_freq=4500.0,
            cpu_min_freq=400.0,
            cores_info=[
                SimpleNamespace(
                    id=i,
                    usage=99.9 if i == 0 else 4.0,
                    temperature=123.0 if i == 0 else 0.0,
                    frequency=4500.0 if i == 0 else 0.0,
                ) for i in range(16)
            ],
            cpu_fan_speed=None,
            battery_info=None,
            platform_profile=SimpleNamespace(),
            current_gov="powersave",
            current_epp="balance_performance",
            current_epb="balance_performance",
            current_hwp_dynamic_boost=True,
            is_turbo_on=(True, None),
            cpu_usage=10.0,
            load=1.0,
            avg_load=(1.0, 1.0, 1.0),
            cpu_avg_temp=50.0,
        )

        with patch.object(system_monitor,
                          "format_platform_profile_summary",
                          return_value=[]):
            with patch.object(system_monitor.config,
                              "has_config", return_value=False):
                monitor = system_monitor.SystemMonitor(
                    type=system_monitor.ViewType.STATS
                )
                monitor.format_system_info(report)

        cpu_rows = [
            widget for widget in monitor.left_content
            if isinstance(widget, urwid.Text)
            and re.match(r"^CPU\d+\s", widget.text)
        ]
        self.assertEqual(len(cpu_rows), 16)
        for width in (32, 39, 49, 59):
            for widget in cpu_rows:
                self.assertEqual(widget.rows((width,)), 1)
        self.assertIn("4500", cpu_rows[0].text)
        self.assertIn("123°C", cpu_rows[0].text)
        self.assertIn("—", cpu_rows[1].text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
