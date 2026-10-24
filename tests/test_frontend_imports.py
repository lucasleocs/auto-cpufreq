"""Smoke tests for the maintained GTK module boundaries.

Imports and identity checks only: no window, daemon, or privileged actions.
"""
import unittest


class FrontendImportTests(unittest.TestCase):
    def test_monitor_import_points_to_the_implementation(self):
        from auto_cpufreq.gui import app, monitor_view, objects

        self.assertIs(app.MonitorModeView, monitor_view.MonitorModeView)
        # Preserve the old import path for downstream callers.
        self.assertIs(objects.MonitorModeView, monitor_view.MonitorModeView)

    def test_desktop_folder_handler_has_one_implementation(self):
        from auto_cpufreq.gui import app, desktop, objects

        self.assertIs(
            app.open_containing_folder, desktop.open_containing_folder
        )
        self.assertIs(
            objects.open_containing_folder, desktop.open_containing_folder
        )

    def test_active_control_classes_are_still_available(self):
        from auto_cpufreq.gui import objects

        for name in (
            "RadioButtonView", "CPUTurboOverride",
            "BluetoothBootControl", "DropDownMenu",
            "DaemonNotRunningView", "UpdateDialog",
        ):
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(objects, name)))

if __name__ == "__main__":
    unittest.main(verbosity=2)
