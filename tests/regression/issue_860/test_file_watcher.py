"""Non-privileged, real-kernel tests for the isolated config file watcher."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from auto_cpufreq.config.file_watcher import ConfigFileWatcher


class FileWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "config.conf"
        self.path.write_text("initial")
        self.events = []
        self.event_lock = threading.Lock()

        def changed():
            value = self.path.read_text() if self.path.is_file() else None
            with self.event_lock:
                self.events.append(value)

        self.watcher = ConfigFileWatcher(
            self.path, changed, retry_interval=0.15, delete_grace=0.10,
        )
        self.addCleanup(self.watcher.stop)

    def events_now(self):
        with self.event_lock:
            return list(self.events)

    def wait_for(self, value, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if value in self.events_now():
                return
            time.sleep(0.015)
        self.fail("No callback for %r; observed %r" % (value, self.events_now()))

    def start_clean(self):
        self.watcher.start()
        with self.event_lock:
            self.events.clear()

    def test_write_is_observed_only_after_close(self):
        self.start_clean()
        with self.path.open("w") as stream:
            stream.write("written")
            stream.flush()
            time.sleep(0.16)
            self.assertNotIn("written", self.events_now())
        self.wait_for("written")

    def test_atomic_replacement_and_backup_files(self):
        self.start_clean()
        backup = self.root / "config.conf~"
        backup.write_text("irrelevant")
        time.sleep(0.20)
        self.assertEqual(self.events_now(), [])
        staged = self.root / "staged"
        staged.write_text("replacement")
        os.replace(staged, self.path)
        self.wait_for("replacement")

    def test_rename_pair_does_not_publish_intermediate_deletion(self):
        self.start_clean()
        staged = self.root / "staged"
        staged.write_text("updated")
        os.replace(self.path, self.root / "old")
        os.replace(staged, self.path)
        self.wait_for("updated")
        self.assertNotIn(None, self.events_now())

    def test_delete_and_recreate(self):
        self.start_clean()
        self.path.unlink()
        self.wait_for(None)
        self.path.write_text("restored")
        self.wait_for("restored")

    def test_deleted_directory_recovers(self):
        self.watcher.set_path(self.root / "nested" / "config.conf")
        self.path = self.root / "nested" / "config.conf"
        self.path.parent.mkdir()
        self.path.write_text("nested")
        self.start_clean()
        self.path.unlink()
        self.path.parent.rmdir()
        self.path.parent.mkdir()
        self.path.write_text("resurrected")
        self.wait_for("resurrected")

    def test_missing_directory_at_start_recovers(self):
        self.watcher.set_path(self.root / "later" / "config.conf")
        self.path = self.root / "later" / "config.conf"
        self.start_clean()
        self.path.parent.mkdir()
        self.path.write_text("appeared")
        self.wait_for("appeared")

    def test_stop_is_idempotent_and_restart_works(self):
        self.start_clean()
        self.assertTrue(self.watcher.is_alive())
        self.watcher.stop()
        self.watcher.stop()
        self.assertFalse(self.watcher.is_alive())
        self.watcher.start()
        self.path.write_text("after restart")
        self.wait_for("after restart")

    def test_path_change_is_rejected_while_running(self):
        self.start_clean()
        with self.assertRaises(RuntimeError):
            self.watcher.set_path(self.root / "different.conf")

    def test_initialization_failure_does_not_spin_or_block_stop(self):
        with patch(
            "auto_cpufreq.config.file_watcher.INotify",
            side_effect=OSError("simulated initialization failure"),
        ):
            self.start_clean()
            start = time.process_time()
            time.sleep(0.30)
            self.assertLess(time.process_time() - start, 0.15)
            self.watcher.stop()
            self.assertFalse(self.watcher.is_alive())

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux /proc required")
    def test_repeated_start_stop_does_not_leak_descriptors(self):
        before = len(list(Path("/proc/self/fd").iterdir()))
        for _ in range(8):
            self.watcher.start()
            self.watcher.stop()
        after = len(list(Path("/proc/self/fd").iterdir()))
        self.assertLessEqual(after, before + 1)
        self.assertFalse(self.watcher.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
