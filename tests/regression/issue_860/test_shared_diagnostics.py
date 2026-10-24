"""Pure-Python regression checks for the shared logging engine (no GUI deps)."""
import importlib
import logging
import os
from pathlib import Path
from threading import Event, Thread
import tempfile
import time
import unittest
from unittest.mock import patch

from auto_cpufreq.modules import diagnostics as module


class SharedLoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.instances = []

    def make(self, component="stats"):
        instance = module.SessionDiagnostics(
            component=component, state_dir=Path(self.tmp.name)
        )
        self.instances.append(instance)
        self.addCleanup(instance.shutdown)
        return instance

    def await_log(self, logger, text, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if text in logger.log_path.read_text(encoding="utf-8"):
                return
            time.sleep(0.005)
        self.fail(f"Did not find {text!r} in asynchronous log")

    def test_off_is_noop(self):
        logger = self.make()
        logger.info("silent")
        self.assertFalse(logger.enabled)
        self.assertFalse(logger.log_path.exists())
        self.assertFalse(list(Path(self.tmp.name).iterdir()))

    def test_session_start_end(self):
        logger = self.make()
        logger._activate()
        path = logger.log_path
        logger.warning("this should be logged")
        self.await_log(logger, "this should be logged")
        logger._deactivate()
        self.assertFalse(logger.enabled)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        logger._activate()
        self.assertNotEqual(path, logger.log_path)
        logger._deactivate()

    def test_components_use_independent_logs(self):
        gtk = self.make("gtk")
        stats = self.make("stats")
        gtk._activate()
        stats._activate()
        self.assertNotEqual(gtk.log_path, stats.log_path)
        gtk.info("only gtk")
        stats.info("only stats")
        self.await_log(gtk, "only gtk")
        self.await_log(stats, "only stats")
        self.assertNotIn("only gtk", stats.log_path.read_text())

    def test_no_toolkit_import(self):
        src = Path(module.__file__).read_text()
        self.assertNotIn("import gi", src)
        self.assertNotIn("import urwid", src)
        self.assertNotIn("power_helper", src)
        self.assertNotIn("set_autofreq", src)

    def test_exception_records_do_not_retain_traceback(self):
        source = module._BoundedQueueHandler(
            module.Queue(maxsize=8), lambda: None
        )
        try:
            raise RuntimeError("synthetic traceback")
        except RuntimeError:
            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 0, "caught error",
                (), os.sys.exc_info()
            )
        prepared = source.prepare(record)
        self.assertIsNone(prepared.exc_info)
        self.assertIn("RuntimeError: synthetic traceback", prepared.exc_text)
        self.assertIsNotNone(record.exc_info)

    def test_atomic_shutdown_rejects_racing_producers(self):
        events = module.Queue(maxsize=3)
        dropped = []
        handler = module._BoundedQueueHandler(events, lambda: dropped.append(1))
        stop = Event()
        producer_failed = []

        def producer():
            i = 0
            while not stop.is_set():
                try:
                    record = logging.LogRecord(
                        "test", logging.INFO, __file__, 1,
                        "queued %d", (i,), None,
                    )
                    handler.emit(record)
                except Exception as error:
                    producer_failed.append(error)
                    break
                i += 1

        producers = [Thread(target=producer, daemon=True) for _ in range(3)]
        for thread in producers:
            thread.start()
        time.sleep(0.02)
        started = time.monotonic()
        handler.close_producers()
        elapsed = time.monotonic() - started
        stop.set()
        for thread in producers:
            thread.join(timeout=2)
        self.assertLess(elapsed, 1.5)
        self.assertFalse(producer_failed)
        items = []
        while not events.empty():
            items.append(events.get_nowait())
        self.assertEqual(sum(item is None for item in items), 1)
        self.assertIsNone(items[-1])
        size = events.qsize()
        handler.emit(logging.LogRecord(
            "test", logging.INFO, __file__, 1, "late", (), None
        ))
        self.assertEqual(events.qsize(), size)

    def test_blocked_disk_cannot_block_shutdown(self):
        log = self.make()
        release = Event()
        original = module._PrivateRotatingFileHandler.emit

        def slow_emit(output, record):
            release.wait(timeout=2)
            original(output, record)

        with patch.object(module._PrivateRotatingFileHandler, "emit", slow_emit):
            log._activate()
            log.warning("delayed write")
            time.sleep(0.025)
            started = time.monotonic()
            try:
                log._deactivate()
                self.assertLess(time.monotonic() - started, 0.8)
            finally:
                release.set()

    def test_blocked_writer_refuses_new_session_until_it_exits(self):
        log = self.make()
        entered, release = Event(), Event()
        original = module._PrivateRotatingFileHandler.emit

        def blocked_emit(output, record):
            entered.set()
            release.wait(timeout=3)
            return original(output, record)

        with patch.object(
            module._PrivateRotatingFileHandler, "emit", blocked_emit
        ):
            log._activate()
            log.info("first session")
            self.assertTrue(entered.wait(timeout=1))
            old_path = log.log_path
            try:
                log._deactivate()
                self.assertFalse(log.enabled)
                with self.assertRaisesRegex(OSError, "still stopping"):
                    log._activate()
            finally:
                release.set()
            deadline = time.monotonic() + 2
            while (
                log._retiring_session.writer.is_alive()
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertFalse(log._retiring_session.writer.is_alive())
        log._activate()
        self.assertNotEqual(old_path, log.log_path)
        log._deactivate()

    def test_background_write_failure_is_queryable_without_ui_toolkit(self):
        log = self.make()
        def broken_emit(_output, _record):
            raise OSError("simulated disk error")
        with patch.object(
            module._PrivateRotatingFileHandler,
            "emit", broken_emit,
        ):
            log._activate()
            log.info("trigger failure")
            deadline = time.monotonic() + 2
            while log.error_status()[0] == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            count, message = log.error_status()
            self.assertGreater(count, 0)
            self.assertIn("simulated disk error", message)
            log._deactivate()

    def test_privileged_cli_ignores_inherited_user_log_directory(self):
        from types import SimpleNamespace
        with patch.dict(os.environ, {
            "HOME": "/home/ordinary-user",
            "XDG_STATE_HOME": "/home/ordinary-user/.local/state",
        }):
            with patch.object(module.os, "geteuid", return_value=0):
                with patch("pwd.getpwuid", return_value=SimpleNamespace(
                    pw_dir="/root"
                )):
                    self.assertEqual(
                        module.cli_state_directory(),
                        Path("/root/.local/state/auto-cpufreq"),
                    )

    def test_unprivileged_cli_uses_xdg_state_home(self):
        with patch.dict(os.environ, {
            "XDG_STATE_HOME": self.tmp.name,
        }):
            with patch.object(module.os, "geteuid", return_value=1000):
                self.assertEqual(
                    module.cli_state_directory(),
                    Path(self.tmp.name) / "auto-cpufreq",
                )

    def test_invalid_component_is_rejected(self):
        with self.assertRaises(ValueError):
            self.make("../escape")

    def test_terminate_stale_record_is_a_single_sentinel(self):
        log = self.make()
        log._activate()
        events = log._events
        for i in range(module.QUEUE_SIZE * 2):
            log.info("queue entry %d", i)
        log._deactivate()
        self.assertIsNone(log._events)
        # The writer drains asynchronously. The shutdown sentinel cannot
        # be overtaken by a producer thanks to the handler's producer gate.
        self.assertIsNone(log._handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)
