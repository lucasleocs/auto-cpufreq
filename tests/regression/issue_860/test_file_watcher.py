"""Non-privileged, real-kernel tests for the isolated config file watcher."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from inotify_simple import flags

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

    def test_simulated_queue_overflow_rescans_latest_file(self):
        # Kernel overflows are difficult to induce deterministically. Deliver
        # the exact API event over a fake selectable inotify descriptor.
        class FakeINotify:
            instance = None

            def __init__(self, nonblocking=False):
                self.read_fd, self.write_fd = os.pipe()
                self.queued = []
                FakeINotify.instance = self

            def fileno(self):
                return self.read_fd

            def add_watch(self, path, mask):
                return 11

            def rm_watch(self, wd):
                pass

            def read(self, timeout=0):
                os.read(self.read_fd, 1)
                result, self.queued = self.queued, []
                return result

            def send_overflow(self):
                self.queued.append(
                    SimpleNamespace(
                        wd=-1, mask=flags.Q_OVERFLOW, cookie=0, name="",
                    )
                )
                os.write(self.write_fd, b"x")

            def close(self):
                os.close(self.read_fd)
                os.close(self.write_fd)

        with patch("auto_cpufreq.config.file_watcher.INotify", FakeINotify):
            self.start_clean()
            self.path.write_text("state-after-lost-events")
            FakeINotify.instance.send_overflow()
            self.wait_for("state-after-lost-events")


    def test_new_regular_file_remains_unpublished_until_close(self):
        self.start_clean()
        self.path.unlink()
        self.wait_for(None)
        with self.event_lock:
            self.events.clear()
        with self.path.open("w") as stream:
            stream.write("partial-new-file")
            stream.flush()
            time.sleep(0.18)
            self.assertNotIn("partial-new-file", self.events_now())
        self.wait_for("partial-new-file")

    def test_symlink_creation_is_observed_without_close_write(self):
        self.start_clean()
        self.path.unlink()
        self.wait_for(None)
        destination = self.root / "linked"
        destination.write_text("link-content")
        self.path.symlink_to(destination)
        self.wait_for("link-content")

    def test_chmod_during_open_write_does_not_reload_partial_contents(self):
        self.start_clean()
        mode = self.path.stat().st_mode
        try:
            with self.path.open("w") as stream:
                stream.write("partial-")
                stream.flush()
                self.path.chmod(mode ^ 0o100)
                time.sleep(0.22)
                self.assertNotIn("partial-", self.events_now())
                stream.write("complete")
            self.wait_for("partial-complete")
        finally:
            self.path.chmod(mode)

    def test_symlink_target_written_outside_parent_is_observed(self):
        external = self.root / "outside"
        external.mkdir()
        target = external / "settings"
        target.write_text("one")
        self.path.unlink()
        self.path.symlink_to(target)
        self.start_clean()
        target.write_text("two")
        self.wait_for("two")

    def test_symlink_target_atomic_replacement_is_observed(self):
        external = self.root / "outside"
        external.mkdir()
        target = external / "settings"
        target.write_text("old")
        self.path.unlink()
        self.path.symlink_to(target)
        self.start_clean()
        replacement = external / "next"
        replacement.write_text("atomic-new")
        os.replace(replacement, target)
        self.wait_for("atomic-new")

    def test_symlink_retargeting_rearms_target_watch(self):
        external = self.root / "outside"
        external.mkdir()
        old_target, fresh_target = external / "one", external / "two"
        old_target.write_text("old")
        fresh_target.write_text("new")
        self.path.unlink()
        self.path.symlink_to(old_target)
        self.start_clean()
        link = self.root / "new-link"
        link.symlink_to(fresh_target)
        os.replace(link, self.path)
        self.wait_for("new")
        fresh_target.write_text("newest")
        self.wait_for("newest")

    def test_failed_initialization_recovers_with_visible_state(self):
        from inotify_simple import INotify as RealINotify

        attempts = []
        def intermittently_unavailable(**kwargs):
            attempts.append(1)
            if len(attempts) <= 2:
                raise OSError("temporary inotify initialization failure")
            return RealINotify(**kwargs)

        with patch("auto_cpufreq.config.file_watcher.INotify",
                   intermittently_unavailable):
            self.watcher.start()
            self.assertIn(self.watcher.state, ("starting", "recovering"))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if self.watcher.state == "watching":
                    break
                time.sleep(0.01)
            self.assertEqual(self.watcher.state, "watching")
            self.assertGreaterEqual(len(attempts), 3)
            self.path.write_text("recovered")
            self.wait_for("recovered")

    def test_stop_during_callback_rejects_restart_until_worker_exits(self):
        callback_entered = threading.Event()
        callback_release = threading.Event()

        def slow_callback():
            callback_entered.set()
            callback_release.wait(3)

        self.watcher.start()
        self.watcher._on_change = slow_callback
        self.path.write_text("trigger")
        self.assertTrue(callback_entered.wait(2))
        stopped = threading.Thread(target=self.watcher.stop)
        stopped.start()
        try:
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and self.watcher.state != "stopping":
                time.sleep(0.01)
            self.assertEqual(self.watcher.state, "stopping")
            with self.assertRaises(RuntimeError):
                self.watcher.start()
        finally:
            callback_release.set()
            stopped.join(timeout=2)
        self.assertFalse(stopped.is_alive())
        self.assertEqual(self.watcher.state, "stopped")
        self.watcher.start()
        self.assertEqual(self.watcher.state, "watching")

    def test_multiple_atomic_replacements_reach_latest_revision(self):
        self.start_clean()
        for index in range(25):
            staged = self.root / ("new-%d" % index)
            staged.write_text("revision-%d" % index)
            os.replace(staged, self.path)
        self.wait_for("revision-24")


    def test_repeated_select_errors_back_off_and_stop_promptly(self):
        attempts = []

        def unavailable_select(*_args, **_kwargs):
            attempts.append(time.monotonic())
            raise OSError("simulated failed select")

        with patch(
            "auto_cpufreq.config.file_watcher.select.select",
            side_effect=unavailable_select,
        ):
            self.watcher.start()
            time.sleep(0.47)
            self.assertLessEqual(len(attempts), 5, "Repeated select error spun")
            begin = time.monotonic()
            self.watcher.stop()
            self.assertLess(time.monotonic() - begin, 0.5)
            self.assertEqual(self.watcher.state, "stopped")


    def _check_failed_start_is_clean(self, failure):
        from contextlib import ExitStack
        from auto_cpufreq.config import file_watcher

        original_pipe = os.pipe
        opened = []

        def tracked_pipe():
            pair = original_pipe()
            opened.extend(pair)
            return pair

        class StartFails:
            def start(self):
                raise RuntimeError("simulated Thread.start failure")

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(file_watcher.os, "pipe", side_effect=tracked_pipe)
            )
            if failure == "set_blocking":
                stack.enter_context(
                    patch.object(
                        file_watcher.os, "set_blocking",
                        side_effect=OSError("simulated set_blocking failure"),
                    )
                )
                expected = OSError
            elif failure == "thread_constructor":
                stack.enter_context(
                    patch.object(
                        file_watcher.threading, "Thread",
                        side_effect=RuntimeError("simulated constructor failure"),
                    )
                )
                expected = RuntimeError
            else:
                stack.enter_context(
                    patch.object(
                        file_watcher.threading, "Thread",
                        return_value=StartFails(),
                    )
                )
                expected = RuntimeError
            with self.assertRaises(expected):
                self.watcher.start()

        self.assertEqual(len(opened), 2)
        self.assertEqual(self.watcher.state, "stopped")
        self.assertIsNone(self.watcher._thread)
        self.assertIsNone(self.watcher._stop_write)
        for fd in opened:
            with self.assertRaises(OSError):
                os.fstat(fd)

        # A failed start must not poison later use of the same watcher.
        self.watcher.start()
        self.assertTrue(self.watcher.is_alive())
        self.watcher.stop()
        self.assertEqual(self.watcher.state, "stopped")

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux /proc required")
    def test_set_blocking_failure_closes_both_pipe_fds(self):
        self._check_failed_start_is_clean("set_blocking")

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux /proc required")
    def test_thread_constructor_failure_closes_both_pipe_fds(self):
        self._check_failed_start_is_clean("thread_constructor")

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "Linux /proc required")
    def test_thread_start_failure_closes_both_pipe_fds(self):
        self._check_failed_start_is_clean("thread_start")

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
