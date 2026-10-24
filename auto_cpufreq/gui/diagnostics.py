import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread, main_thread
import time
import traceback


MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUPS = 3
HEARTBEAT_SECONDS = 5
STALL_SECONDS = 20


def _boottime():
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        return None


def _xdg_path(variable, fallback):
    value = os.environ.get(variable, "")
    return Path(value) if value.startswith("/") else Path.home() / fallback


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            return os.fdopen(fd, "a", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise


class GuiDiagnostics:
    def __init__(self):
        self.log_path = _xdg_path(
            "XDG_STATE_HOME", ".local/state"
        ) / "auto-cpufreq" / "gtk.log"
        self.settings_path = _xdg_path(
            "XDG_CONFIG_HOME", ".config"
        ) / "auto-cpufreq" / "gtk-debug.json"
        self._logger = logging.getLogger(
            f"auto_cpufreq.gui.diagnostics.{id(self)}"
        )
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        self._handler = None
        self._glib = None
        self._heartbeat_id = None
        self._last_heartbeat = time.monotonic()
        self._last_boottime = _boottime()
        self._main_thread_id = main_thread().ident
        self._stall_reported = False
        self._watchdog_stop = None
        self.enabled = False

        if self._read_preference():
            try:
                self._activate()
            except OSError:
                pass

    def _read_preference(self):
        try:
            data = json.loads(self.settings_path.read_text(
                encoding="utf-8"
            ))
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
            os.replace(name, self.settings_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def attach_mainloop(self, glib):
        self._glib = glib
        if self.enabled:
            self._start_heartbeat()

    def _start_heartbeat(self):
        if self._glib is None or self._heartbeat_id is not None:
            return
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
        gap = now - self._last_heartbeat
        boottime = _boottime()
        if boottime is not None and self._last_boottime is not None:
            suspended = boottime - self._last_boottime - gap
            if suspended > 3:
                self.info(
                    "System suspend/resume detected: %.1f seconds suspended",
                    suspended,
                )
        self._last_boottime = boottime
        self._last_heartbeat = now
        self._stall_reported = False
        if gap > HEARTBEAT_SECONDS * 2:
            self.warning("GTK main loop delay: %.2f seconds", gap)
        else:
            self.debug("GTK main loop heartbeat: %.2f seconds", gap)
        return True

    def _check_stall(self):
        if not self.enabled or self._glib is None:
            return

        delay = time.monotonic() - self._last_heartbeat
        if delay < STALL_SECONDS or self._stall_reported:
            return

        self._stall_reported = True
        frame = sys._current_frames().get(self._main_thread_id)
        stack = "".join(traceback.format_stack(frame, limit=24)) if frame else "unavailable"
        self.warning(
            "GTK main loop unresponsive for at least %.1f seconds; main thread stack:\n%s",
            delay, stack
        )

    def _watchdog(self, stop):
        while not stop.wait(HEARTBEAT_SECONDS):
            self._check_stall()

    def _activate(self):
        if self.enabled:
            return
        self.log_path.parent.mkdir(
            mode=0o700, parents=True, exist_ok=True
        )
        handler = _PrivateRotatingFileHandler(
            self.log_path, maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUPS, delay=True
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(threadName)s %(message)s"
        ))
        self._logger.addHandler(handler)
        self._handler = handler
        self.enabled = True
        self._stall_reported = False
        self.info(
            "GUI debug logging enabled; pid=%d session=%s",
            os.getpid(), os.environ.get("XDG_SESSION_TYPE", "unknown")
        )
        self._start_heartbeat()
        stop = Event()
        self._watchdog_stop = stop
        Thread(
            target=self._watchdog, args=(stop,),
            daemon=True, name="gui-debug-watchdog"
        ).start()

    def _deactivate(self):
        if not self.enabled:
            return
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
