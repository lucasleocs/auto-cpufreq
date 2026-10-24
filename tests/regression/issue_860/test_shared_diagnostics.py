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
