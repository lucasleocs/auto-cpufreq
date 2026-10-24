"""Small Linux inotify observer for the selected configuration file.

The observer owns its descriptors in one worker. Configuration parsing and
publication remain the responsibility of its callback (ConfigManager).
"""
import logging
import os
import select
import threading
import time

from inotify_simple import INotify, flags


_LOG = logging.getLogger(__name__)


class ConfigFileWatcher:
    """Watch one pathname, recovering when its parent directory disappears."""

    _MASK = (
        flags.CLOSE_WRITE | flags.MOVED_TO | flags.MOVED_FROM
        | flags.DELETE | flags.CREATE | flags.DELETE_SELF
        | flags.MOVE_SELF | flags.UNMOUNT
    )
    _CHANGED = flags.CLOSE_WRITE | flags.MOVED_TO
    _REMOVED = flags.DELETE | flags.MOVED_FROM
    _LOST = flags.IGNORED | flags.DELETE_SELF | flags.MOVE_SELF | flags.UNMOUNT

    def __init__(self, path, on_change, retry_interval=1.0, delete_grace=0.12):
        self.path = os.path.abspath(os.path.expanduser(path))
        self._on_change = on_change
        self._retry_interval = max(0.1, float(retry_interval))
        self._delete_grace = max(0.0, float(delete_grace))
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._ready = threading.Event()
        self._stop_write = None

    def set_path(self, path):
        """Changing the selection requires a stopped watcher."""
        selected = os.path.abspath(os.path.expanduser(path))
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if selected != self.path:
                    raise RuntimeError("Stop config watcher before changing its path")
            self.path = selected

    def is_alive(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            read_fd, write_fd = os.pipe()
            self._stop_event = threading.Event()
            self._ready = threading.Event()
            self._stop_write = write_fd
            self._thread = threading.Thread(
                target=self._run, args=(read_fd, self._stop_event, self._ready),
                name="auto-cpufreq-config-watch", daemon=True,
            )
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._stop_write = None
                os.close(read_fd)
                os.close(write_fd)
                raise
            ready = self._ready
        # The initial watch is installed before callers receive control.
        ready.wait(timeout=3.0)

    def stop(self):
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            try:
                if self._stop_write is not None:
                    os.write(self._stop_write, b"x")
            except OSError:
                pass
        if threading.current_thread() is thread:
            return
        thread.join(timeout=5.0)
        with self._lock:
            if not thread.is_alive() and self._thread is thread:
                self._thread = None
                self._stop_write = None
            elif thread.is_alive():
                _LOG.warning("Configuration watcher did not stop within five seconds")

    def _run(self, stop_read, stop_event, ready):
        notifier = None
        watched_dir = None
        watched_identity = None
        wd = None
        pending = None
        parent = os.path.dirname(self.path)
        basename = os.path.basename(self.path)

        def notify():
            try:
                self._on_change()
            except Exception:
                _LOG.exception("Configuration reload callback failed")

        def release_watch():
            nonlocal wd, watched_dir, watched_identity
            if wd is not None and notifier is not None:
                try:
                    notifier.rm_watch(wd)
                except OSError:
                    pass
            wd = None
            watched_dir = None
            watched_identity = None

        def closest_watch():
            candidate = parent
            while True:
                try:
                    identity = os.stat(candidate)
                    if os.path.isdir(candidate):
                        return candidate, (identity.st_dev, identity.st_ino)
                except OSError:
                    pass
                previous = candidate
                candidate = os.path.dirname(candidate)
                if candidate == previous:
                    return None, None

        def reconcile():
            nonlocal watched_dir, watched_identity, wd
            if notifier is None:
                return False
            candidate, identity = closest_watch()
            if (candidate == watched_dir and identity == watched_identity
                    and wd is not None):
                return False
            release_watch()
            if candidate is None:
                return False
            try:
                wd = notifier.add_watch(candidate, self._MASK)
                watched_dir, watched_identity = candidate, identity
            except OSError as error:
                _LOG.warning("Cannot watch config directory %s: %s", candidate, error)
                return False
            return True

        try:
            while not stop_event.is_set():
                if notifier is None:
                    try:
                        notifier = INotify(nonblocking=True)
                    except OSError as error:
                        _LOG.warning("Cannot initialize inotify: %s", error)
                changed_watch = reconcile()
                if changed_watch:
                    # A file may have appeared while no directory was watched.
                    notify()
                ready.set()
                timeout = self._retry_interval
                if pending is not None:
                    timeout = min(timeout, max(0.0, pending - time.monotonic()))
                sources = [stop_read]
                if notifier is not None:
                    sources.append(notifier.fileno())
                try:
                    readable, _, _ = select.select(sources, [], [], timeout)
                except (OSError, ValueError) as error:
                    _LOG.warning("Configuration watch select failed: %s", error)
                    if notifier is not None:
                        notifier.close()
                        notifier = None
                    wd = watched_dir = watched_identity = None
                    continue
                if stop_read in readable or stop_event.is_set():
                    break
                if notifier is not None and notifier.fileno() in readable:
                    try:
                        events = notifier.read(timeout=0)
                    except (OSError, BlockingIOError) as error:
                        _LOG.warning("Configuration watch read failed: %s", error)
                        notifier.close()
                        notifier = None
                        wd = watched_dir = watched_identity = None
                        continue
                    for event in events:
                        mask = event.mask
                        if mask & flags.Q_OVERFLOW:
                            pending = time.monotonic()
                            release_watch()
                            break
                        if wd is None or event.wd != wd:
                            continue
                        if mask & self._LOST:
                            release_watch()
                            pending = time.monotonic()
                            continue
                        if watched_dir == parent and event.name == basename:
                            if mask & self._CHANGED:
                                pending = time.monotonic()
                            elif mask & self._REMOVED and pending is None:
                                pending = time.monotonic() + self._delete_grace
                        # A newly created subdirectory can make a closer watch
                        # available. Reconcile on the next iteration.
                if pending is not None and time.monotonic() >= pending:
                    pending = None
                    notify()
        finally:
            ready.set()
            if notifier is not None:
                notifier.close()
            os.close(stop_read)
            with self._lock:
                write_fd = self._stop_write
                # Do not close a replacement start's pipe.
                if self._thread is threading.current_thread():
                    self._stop_write = None
                else:
                    write_fd = None
            if write_fd is not None:
                os.close(write_fd)
