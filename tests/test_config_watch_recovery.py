"""Regression tests for config reloads across inotify directory recovery.

Run with: python -m unittest discover -s tests -p 'test_config_watch_recovery.py' -v
Uses temporary files and does not modify system power settings.
"""
import time
import tempfile
import unittest
from pathlib import Path

from auto_cpufreq.config.config import _Config
from auto_cpufreq.config.file_watcher import ConfigFileWatcher


class ConfigWatchRecoveryTests(unittest.TestCase):
    @staticmethod
    def until(predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.025)
        return bool(predicate())

    @staticmethod
    def value(config):
        return config.get_config().get("profile", "value", fallback=None)

    @staticmethod
    def create_watcher(path):
        config = _Config()
        config.set_path(str(path))
        watcher = ConfigFileWatcher(
            str(path), config.update_config,
            retry_interval=0.1, delete_grace=0.05,
        )
        watcher.start()
        return config, watcher

    def test_moved_directory_with_complete_config_resynchronizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "watched"
            target = parent / "auto-cpufreq.conf"
            prepared = root / "prepared"
            prepared.mkdir()
            (prepared / target.name).write_text("[profile]\nvalue = ready\n")
            config, watcher = self.create_watcher(target)
            try:
                self.assertIsNone(self.value(config))
                prepared.rename(parent)
                self.assertTrue(self.until(lambda: self.value(config) == "ready"))
            finally:
                watcher.stop()
                self.assertFalse(watcher.is_alive())

    def test_recovered_directory_with_paused_writer_waits_for_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "watched"
            target = parent / "auto-cpufreq.conf"
            staging = root / "staging"
            staging.mkdir()
            prepared = staging / target.name
            config, watcher = self.create_watcher(target)
            try:
                with prepared.open("w") as writer:
                    writer.write("[profile]\nvalue = incomplete\n")
                    writer.flush()
                    staging.rename(parent)
                    # Longer than recovery_delay, so metadata stability alone
                    # must not publish the syntactically valid prefix.
                    time.sleep(1.6)
                    self.assertIsNone(self.value(config))
                    writer.seek(0)
                    writer.truncate()
                    writer.write("[profile]\nvalue = finished\n")
                self.assertTrue(self.until(
                    lambda: self.value(config) == "finished"
                ))
            finally:
                watcher.stop()
                self.assertFalse(watcher.is_alive())

    def test_regular_file_waits_for_close_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "watched"
            target = parent / "auto-cpufreq.conf"
            config, watcher = self.create_watcher(target)
            try:
                parent.mkdir()
                with target.open("w") as stream:
                    stream.write("[profile]\nvalue = unfinished\n")
                    stream.flush()
                    time.sleep(0.35)
                    self.assertIsNone(self.value(config))
                    stream.seek(0)
                    stream.truncate()
                    stream.write("[profile]\nvalue = finished\n")
                self.assertTrue(self.until(
                    lambda: self.value(config) == "finished"
                ))
            finally:
                watcher.stop()
                self.assertFalse(watcher.is_alive())

    def test_delete_restores_defaults_and_atomic_replace_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "auto-cpufreq.conf"
            target.write_text("[profile]\nvalue = original\n")
            config, watcher = self.create_watcher(target)
            try:
                target.unlink()
                self.assertTrue(self.until(lambda: self.value(config) is None))
                replacement = root / "replacement.conf"
                replacement.write_text("[profile]\nvalue = new\n")
                replacement.replace(target)
                self.assertTrue(self.until(lambda: self.value(config) == "new"))
            finally:
                watcher.stop()
                self.assertFalse(watcher.is_alive())

    def test_invalid_config_keeps_last_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "auto-cpufreq.conf"
            target.write_text("[profile]\nvalue = valid\n")
            config, watcher = self.create_watcher(target)
            try:
                target.write_text("[profile\nvalue = malformed\n")
                time.sleep(0.35)
                self.assertEqual(self.value(config), "valid")
            finally:
                watcher.stop()
                self.assertFalse(watcher.is_alive())


if __name__ == "__main__":
    unittest.main()
