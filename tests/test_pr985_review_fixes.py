"""Isolated regressions for PR #985 review fixes.

Run with: python -m unittest discover -s tests -p test_pr985_review_fixes.py -v
Uses only temporary config files and mocked GUI thread creation.
"""
import ast
import importlib
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest import mock


class ConfigReloadRegression(unittest.TestCase):
    def test_delete_between_parse_and_stat_publishes_empty_parser(self):
        module = importlib.import_module("auto_cpufreq.config.config")
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / "auto-cpufreq.conf"
            filename.write_text("[battery]\nstop_threshold = 80\n")
            config = module._Config()
            config.set_path(str(filename))
            self.assertTrue(config.get_config().has_section("battery"))
            filename.write_text("[battery]\nstop_threshold = 70\n")

            def remove_before_stat(_path):
                filename.unlink()
                raise FileNotFoundError(str(filename))

            with mock.patch.object(module.os, "stat", side_effect=remove_before_stat):
                self.assertTrue(config.update_config())

            self.assertFalse(config.get_config().has_section("battery"))
            self.assertEqual(config.get_config().sections(), [])


class InotifyRegression(unittest.TestCase):
    @staticmethod
    def _until(predicate, seconds=5.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.025)
        return bool(predicate())

    def test_symlink_target_move_does_not_leak_inotify_watches(self):
        module = importlib.import_module("auto_cpufreq.config.file_watcher")
        real_inotify = module.INotify
        instances = []

        class TrackingINotify(real_inotify):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.adds = []
                self.removes = []
                instances.append(self)

            def add_watch(self, path, mask):
                wd = super().add_watch(path, mask)
                self.adds.append((str(path), wd))
                return wd

            def rm_watch(self, wd):
                self.removes.append(wd)
                return super().rm_watch(wd)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target_dir = root / "elsewhere"
            target_dir.mkdir()
            target = target_dir / "settings.ini"
            target.write_text("[profile]\nvalue = 0\n")
            watched = root / "selected.conf"
            watched.symlink_to(target)

            def count_kernel_watches(instance):
                try:
                    fdinfo = Path("/proc/self/fdinfo") / str(instance.fileno())
                    return sum(
                        line.startswith("inotify wd:")
                        for line in fdinfo.read_text().splitlines()
                    )
                except OSError:
                    return -1

            watcher = module.ConfigFileWatcher(
                str(watched), lambda: None,
                retry_interval=0.05, delete_grace=0.0,
            )
            with mock.patch.object(module, "INotify", TrackingINotify):
                watcher.start()
                try:
                    self.assertTrue(self._until(
                        lambda: instances
                        and count_kernel_watches(instances[-1]) == 2
                        and len(instances[-1].adds) >= 2,
                    ))
                    for sequence in range(1, 5):
                        prior_adds = len(instances[-1].adds)
                        target.rename(target_dir / f"backup-{sequence}.ini")
                        target.write_text(f"[profile]\nvalue = {sequence}\n")
                        self.assertTrue(self._until(
                            lambda: len(instances[-1].adds) > prior_adds
                            and count_kernel_watches(instances[-1]) == 2,
                        ), f"Watch leak or missing rearm at replacement {sequence}")
                    self.assertGreaterEqual(len(instances[-1].removes), 4)
                finally:
                    watcher.stop()
                    self.assertFalse(watcher.is_alive())


class GtkUpdateRegression(unittest.TestCase):
    def setUp(self):
        source = Path("auto_cpufreq/gui/app.py").read_text()
        parsed = ast.parse(source)
        target = next(
            node for node in parsed.body
            if isinstance(node, ast.ClassDef) and node.name == "ToolWindow"
        )
        self.dialog_errors = []
        self.sensitivity = []
        self.warning_calls = []

        class FailingThread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("cannot start new thread")

        errors = self.dialog_errors

        class ErrorDialog:
            def __init__(self, *args, **kwargs):
                pass

            def format_secondary_text(self, text):
                errors.append(text)

            def run(self):
                return 0

            def destroy(self):
                pass

        class ChoiceDialog:
            def __init__(self, *args, **kwargs):
                pass

            def run(self):
                return 1

            def destroy(self):
                pass

        gtk = types.SimpleNamespace(
            Window=object,
            ResponseType=types.SimpleNamespace(YES=1),
            MessageType=types.SimpleNamespace(ERROR=0),
            ButtonsType=types.SimpleNamespace(OK=0),
            MessageDialog=ErrorDialog,
        )
        namespace = {
            "Gtk": gtk,
            "Thread": FailingThread,
            "UpdateDialog": ChoiceDialog,
            "package_version": lambda name: "3.2.0",
            "PackageNotFoundError": Exception,
            "diagnostics": types.SimpleNamespace(
                warning=lambda *args: self.warning_calls.append(args)
            ),
        }
        isolated = ast.fix_missing_locations(
            ast.Module(body=[target], type_ignores=[])
        )
        exec(compile(isolated, "auto_cpufreq/gui/app.py", "exec"), namespace)
        self.window = namespace["ToolWindow"]()
        self.window.destroyed = False
        self.window._update_check_pending = False
        self.window.set_sensitive = self.sensitivity.append

    def test_startup_update_check_failure_is_nonfatal(self):
        self.assertFalse(self.window.handle_update())
        self.assertFalse(self.window._update_check_pending)
        self.assertTrue(self.warning_calls)

    def test_update_worker_failure_restores_window(self):
        self.assertFalse(self.window._finish_update_check("v3.3.0", None))
        self.assertEqual(self.sensitivity, [False, True])
        self.assertTrue(any("cannot start new thread" in s for s in self.dialog_errors))

    def test_destroyed_window_ignores_queued_update_completion(self):
        self.window.destroyed = True
        self.assertFalse(self.window._finish_update(None, RuntimeError("failed")))
        self.assertEqual(self.sensitivity, [])
        self.assertEqual(self.dialog_errors, [])


if __name__ == "__main__":
    unittest.main()
