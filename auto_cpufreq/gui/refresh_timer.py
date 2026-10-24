"""One low-overhead GLib refresh timer per active GTK view.

This is only the scheduling layer; GtkRefreshBridge owns delivery and
ReportRefreshCoordinator owns single-flight collection and coalescing.
Periodic ticks never queue work behind an already running collection.
"""
import time

from gi.repository import GLib


STALL_SECONDS = 20.0
SUSPEND_SECONDS = 3.0


def _clock_pair():
    """Monotonic time and optional Linux BOOTTIME (includes suspend)."""
    now = time.monotonic()
    try:
        boot = time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        boot = None
    return now, boot


class GtkRefreshTimer:
    """A single periodic timer that also checks health and resume gaps."""

    def __init__(
        self, *, coordinator, request, active, diagnostics, component,
        interval, clocks=_clock_pair,
    ):
        if interval <= 0:
            raise ValueError("Refresh interval must be positive")
        self.coordinator = coordinator
        self._request = request
        self._active = active
        self._diagnostics = diagnostics
        self._component = component
        self._interval = interval
        self._clocks = clocks
        self._last_clocks = None
        self._stall_reported = False
        self._source_id = None

    @property
    def source_id(self):
        return self._source_id

    def _log(self, method, *args):
        try:
            method(*args)
        except Exception:
            pass  # Optional diagnostics must not disable a live UI timer.

    def start(self):
        if self._source_id is not None or not self._active():
            return
        self._last_clocks = self._clocks()
        self._source_id = GLib.timeout_add_seconds(
            self._interval, self._tick
        )

    def set_interval(self, seconds):
        if seconds <= 0:
            raise ValueError("Refresh interval must be positive")
        if seconds == self._interval:
            return
        was_active = self._source_id is not None
        self.stop()
        self._interval = seconds
        if was_active:
            self.start()

    def _observe_resume(self):
        current = self._clocks()
        previous = self._last_clocks
        self._last_clocks = current
        if previous is None:
            return
        monotonic, boot = current
        old_monotonic, old_boot = previous
        if boot is None or old_boot is None:
            return
        suspended = (boot - old_boot) - (monotonic - old_monotonic)
        if suspended >= SUSPEND_SECONDS:
            self._log(
                self._diagnostics.info,
                "%s: system resume detected, suspended ~%.1fs",
                self._component, suspended,
            )

    def _tick(self):
        if not self._active():
            # GLib removes this source when False is returned.
            self._source_id = None
            return False
        try:
            self._observe_resume()
            if self.coordinator.in_progress:
                age = self.coordinator.request_age_seconds()
                if (age is not None and age >= STALL_SECONDS
                        and not self._stall_reported):
                    self._stall_reported = True
                    self._log(
                        self._diagnostics.warning,
                        "%s collection/delivery pending for %.1fs; "
                        "keeping single-flight worker",
                        self._component, age,
                    )
            else:
                self._stall_reported = False
                self._request()
        except Exception as error:
            # Do not permit a transient failure to silently remove the timer.
            self._log(
                self._diagnostics.warning,
                "%s periodic refresh failed: %s", self._component, error,
            )
        return True

    def stop(self):
        source_id, self._source_id = self._source_id, None
        if source_id is not None:
            GLib.source_remove(source_id)
        self._last_clocks = None
        self._stall_reported = False
