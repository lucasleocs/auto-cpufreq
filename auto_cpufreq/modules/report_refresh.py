"""Toolkit-independent single-flight report refresh coordination.

An adapter decides *when* to request refresh and *how* to deliver results
onto GTK/GLib or Urwid's event loop. This module never touches widgets,
system policy, or privileged APIs.
"""
from dataclasses import dataclass
from threading import Lock, Thread
import time
from typing import Any, Callable


@dataclass(frozen=True)
class RefreshResult:
    generation: int
    report: Any
    error: Exception | None
    duration: float

    @property
    def succeeded(self):
        return self.error is None


class ReportRefreshCoordinator:
    """Coalesce refresh requests; no overlapping collectors for one view.

    Dispatch is called from a worker thread and must only schedule delivery
    onto the appropriate UI context. The UI calls acknowledge(generation)
    once it has processed a result; a pending request then starts a fresh
    collection. Closing invalidates all outstanding generations but does not
    pretend to forcibly interrupt psutil or another blocking system read.
    """

    def __init__(
        self,
        collect: Callable[[], Any],
        dispatch: Callable[[RefreshResult], None],
        thread_name="report-refresh",
    ):
        self._collect = collect
        self._dispatch = dispatch
        self._thread_name = thread_name
        self._lock = Lock()
        self._generation = 0
        self._running = False
        self._delivering = False
        self._pending = False
        self._closed = False

    @property
    def closed(self):
        with self._lock:
            return self._closed

    @property
    def in_progress(self):
        with self._lock:
            return self._running or self._delivering

    def request(self):
        with self._lock:
            if self._closed:
                return False
            if self._running or self._delivering:
                self._pending = True
                return False
            self._generation += 1
            generation = self._generation
            self._running = True
        worker = Thread(
            target=self._run, args=(generation,),
            daemon=True, name=self._thread_name,
        )
        try:
            worker.start()
        except RuntimeError:
            with self._lock:
                if generation == self._generation:
                    self._running = False
            raise
        return True

    def _run(self, generation):
        started = time.monotonic()
        try:
            value = self._collect()
            error = None
        except Exception as exc:
            value = None
            error = exc
        result = RefreshResult(
            generation=generation,
            report=value,
            error=error,
            duration=time.monotonic() - started,
        )
        with self._lock:
            if self._closed or generation != self._generation:
                return
            self._running = False
            self._delivering = True
        try:
            self._dispatch(result)
        except Exception:
            # A disconnected display/closed watch pipe cannot strand the
            # coordinator in a permanently "delivery in progress" state.
            self.acknowledge(generation)

    def is_current(self, generation):
        with self._lock:
            return (
                not self._closed
                and generation == self._generation
                and self._delivering
            )

    def acknowledge(self, generation):
        with self._lock:
            if (
                self._closed
                or generation != self._generation
                or not self._delivering
            ):
                return False
            self._delivering = False
            again = self._pending
            self._pending = False
        if again:
            self.request()
        return True

    def close(self):
        with self._lock:
            self._closed = True
            self._generation += 1
            self._pending = False
            self._delivering = False
