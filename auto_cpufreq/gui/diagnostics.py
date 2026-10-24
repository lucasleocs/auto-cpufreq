import copy
import json
import logging
from logging.handlers import QueueHandler, RotatingFileHandler
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import secrets
import sys
import tempfile
from threading import Event, Lock, Thread, main_thread
import time
import traceback


MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUPS = 3
RETAIN_SESSIONS = 4
QUEUE_SIZE = 256
HEARTBEAT_SECONDS = 5
STALL_SECONDS = 20
SUMMARY_SECONDS = 60
SESSION_RE = re.compile(r"^gtk-\d{8}-\d{6}-(\d+)-[0-9a-f]{8}\.log$")


def _boottime():
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return None


def _xdg_path(variable, fallback):
    value = os.environ.get(variable, "")
    return Path(value) if value.startswith("/") else Path.home() / fallback


def _is_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def __init__(self, *args, on_error=None, **kwargs):
        self._on_error = on_error
        super().__init__(*args, **kwargs)

    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise

    def handleError(self, record):
        if self._on_error:
            self._on_error(sys.exc_info()[1] or OSError("Unknown log write failure"))


class _BoundedQueueHandler(QueueHandler):
    def __init__(self, events, on_drop):
        super().__init__(events)
        self._on_drop = on_drop

    def prepare(self, record):
        # Format exceptions in the writer, not in the GTK event loop.
        return copy.copy(record)

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except Full:
            if record.levelno >= logging.WARNING:
                try:
                    self.queue.get_nowait()
                    self.queue.put_nowait(record)
                except (Empty, Full):
                    pass
            self._on_drop()


class GuiDiagnostics:
    def __init__(self):
        self.state_dir = _xdg_path("XDG_STATE_HOME", ".local/state") / "auto-cpufreq"
        self.settings_path = _xdg_path("XDG_CONFIG_HOME", ".config") / "auto-cpufreq" / "gtk-debug.json"
        self.log_path = self._new_log_path()
        self._logger = logging.getLogger(f"auto_cpufreq.gui.diagnostics.{id(self)}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._handler = None
        self._file_handler = None
        self._writer = None
        self._events = None
        self._glib = None
        self._heartbeat_id = None
        self._last_heartbeat = time.monotonic()
        self._last_boottime = _boottime()
        self._main_thread_id = main_thread().ident
        self._stall_reported = False
        self._watchdog_stop = None
        self._lock = Lock()
        self._dropped = 0
        self._summary = {}
        self._last_summary = time.monotonic()
        self._error_callback = None
        self.last_error = None
        self.enabled = False
        self._activated_once = False

        if self._read_preference():
            try:
                self._activate()
            except OSError as error:
                self._report_error(error)

    def _new_log_path(self):
        name = f"gtk-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}.log"
        return self.state_dir / name

    def _read_preference(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data.get("debug_logging") is True
        except (OSError, ValueError, AttributeError):
            return False

    def _save_preference(self, enabled):
        directory = self.settings_path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".gtk-debug-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"debug_logging": enabled}, stream)
                stream.write("\n")
            os.chmod(name, 0o600)
            os.replace(name, self.settings_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def set_error_callback(self, callback):
        with self._lock:
            self._error_callback = callback
            error = self.last_error
        if callback is not None and error is not None:
            self._notify_error(error)

    def _notify_error(self, message):
        if self._glib is not None:
            self._glib.idle_add(self._deliver_error, message)

    def _deliver_error(self, message):
        with self._lock:
            callback = self._error_callback
        if callback:
            callback(message)
        return False

    def _report_error(self, error):
        message = f"Unable to write GUI debug log: {error}"
        with self._lock:
            if self.last_error == message:
                return
            self.last_error = message
        self._notify_error(message)

    def _dropped_event(self):
        with self._lock:
            self._dropped += 1

    def _writer_loop(self, events, output):
        try:
            self._prune_old_sessions()
            while True:
                record = events.get()
                if record is None:
                    break
                try:
                    output.handle(record)
                except Exception as error:
                    self._report_error(error)
                with self._lock:
                    dropped = self._dropped
                    self._dropped = 0
                if dropped:
                    output.handle(self._logger.makeRecord(
                        self._logger.name, logging.WARNING, __file__, 0,
                        "Dropped %d debug log entries because the queue was full",
                        (dropped,), None,
                    ))
        finally:
            output.close()

    def _prune_old_sessions(self):
        try:
            old = []
            for path in self.state_dir.glob("gtk-*.log"):
                match = SESSION_RE.match(path.name)
                if not match or path == self.log_path or _is_running(int(match[1])):
                    continue
                old.append(path)
            old.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for base in old[RETAIN_SESSIONS:]:
                for suffix in ("", *(f".{i}" for i in range(1, LOG_BACKUPS + 1))):
                    try:
                        base.with_name(base.name + suffix).unlink(missing_ok=True)
                    except OSError:
                        pass
        except OSError:
            pass

    def attach_mainloop(self, glib):
        self._glib = glib
        if self.enabled:
            self._start_heartbeat()

    def _start_heartbeat(self):
        if self._glib is None or self._heartbeat_id is not None:
            return
        with self._lock:
            self._last_heartbeat = time.monotonic()
            self._last_boottime = _boottime()
        self._heartbeat_id = self._glib.timeout_add_seconds(
            HEARTBEAT_SECONDS, self._heartbeat
        )

    def _heartbeat(self):
        if not self.enabled:
            self._heartbeat_id = None
            return False
        now = time.monotonic()
        boottime = _boottime()
        with self._lock:
            gap = now - self._last_heartbeat
            suspended = (boottime - self._last_boottime - gap
                         if boottime is not None and self._last_boottime is not None else 0)
            self._last_heartbeat = now
            self._last_boottime = boottime
            self._stall_reported = False
        if suspended > 3:
            self.info("System suspend/resume detected: %.1f seconds suspended", suspended)
        if gap > HEARTBEAT_SECONDS * 2:
            self.warning("GTK main loop delay: %.2f seconds", gap)
        self._flush_summary(now)
        return True

    def _flush_summary(self, now=None, force=False):
        now = time.monotonic() if now is None else now
        with self._lock:
            if not force and now - self._last_summary < SUMMARY_SECONDS:
                return
            summary, self._summary = self._summary, {}
            self._last_summary = now
        for kind, (count, total, maximum) in summary.items():
            self.info("%s: %d calls, mean=%.3fs max=%.3fs", kind,
                      count, total / count, maximum)

    def record_duration(self, kind, duration, slow_after=2.0):
        if not self.enabled:
            return
        with self._lock:
            count, total, maximum = self._summary.get(kind, (0, 0.0, 0.0))
            self._summary[kind] = (count + 1, total + duration, max(maximum, duration))
        if duration > slow_after:
            self.warning("Slow %s: %.3fs", kind, duration)

    def _check_stall(self):
        if not self.enabled or self._glib is None:
            return
        with self._lock:
            delay = time.monotonic() - self._last_heartbeat
            if delay < STALL_SECONDS or self._stall_reported:
                return
            self._stall_reported = True
        self._flush_summary(force=True)
        frame = sys._current_frames().get(self._main_thread_id)
        stack = "".join(traceback.format_stack(frame, limit=24)) if frame else "unavailable"
        self.warning("GTK main loop unresponsive for at least %.1f seconds; main thread stack:\n%s",
                     delay, stack)

    def _watchdog(self, stop):
        while not stop.wait(HEARTBEAT_SECONDS):
            self._check_stall()

    def _activate(self):
        if self.enabled:
            return
        if self._activated_once:
            self.log_path = self._new_log_path()
        self.state_dir = self.log_path.parent
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        output = _PrivateRotatingFileHandler(
            self.log_path, maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUPS, delay=False, on_error=self._report_error
        )
        output.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(threadName)s %(message)s"
        ))
        events = Queue(maxsize=QUEUE_SIZE)
        handler = _BoundedQueueHandler(events, self._dropped_event)
        writer = Thread(target=self._writer_loop, args=(events, output),
                        daemon=True, name="gui-debug-writer")
        self._events = events
        self._file_handler = output
        self._handler = handler
        self._writer = writer
        self._dropped = 0
        self._last_summary = time.monotonic()
        self._logger.addHandler(handler)
        self.enabled = True
        self.last_error = None
        self._activated_once = True
        writer.start()
        self.info("GUI debug logging enabled; pid=%d session=%s", os.getpid(),
                  os.environ.get("XDG_SESSION_TYPE", "unknown"))
        self._start_heartbeat()
        stop = Event()
        self._watchdog_stop = stop
        Thread(target=self._watchdog, args=(stop,), daemon=True,
               name="gui-debug-watchdog").start()

    def _deactivate(self):
        if not self.enabled:
            return
        self._flush_summary(force=True)
        self.info("GUI debug logging disabled")
        self.enabled = False
        if self._watchdog_stop is not None:
            self._watchdog_stop.set()
            self._watchdog_stop = None
        if self._heartbeat_id is not None and self._glib is not None:
            self._glib.source_remove(self._heartbeat_id)
            self._heartbeat_id = None
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None
        if self._events is not None:
            try:
                self._events.put_nowait(None)
            except Full:
                try:
                    self._events.get_nowait()
                except Empty:
                    pass
                self._events.put_nowait(None)
            self._events = None
        if self._writer is not None:
            self._writer.join(timeout=0.3)
            self._writer = None
        self._file_handler = None

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        if enabled:
            self._activate()
            try:
                self._save_preference(True)
            except OSError:
                self._deactivate()
                raise
        else:
            self._save_preference(False)
            self._deactivate()

    def debug(self, message, *args):
        if self.enabled:
            self._logger.debug(message, *args)

    def info(self, message, *args):
        if self.enabled:
            self._logger.info(message, *args)

    def warning(self, message, *args):
        if self.enabled:
            self._logger.warning(message, *args)

    def exception(self, message, *args):
        if self.enabled:
            self._logger.exception(message, *args)

    def shutdown(self):
        self.info("GUI session ended")
        self._deactivate()


diagnostics = GuiDiagnostics()
