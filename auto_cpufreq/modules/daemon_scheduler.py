import os
import selectors
import socket
import time
from typing import Callable, Mapping, Optional


NETLINK_KOBJECT_UEVENT = getattr(socket, "NETLINK_KOBJECT_UEVENT", 15)
UEVENT_GROUP = 1
UEVENT_BUFFER_SIZE = 64 * 1024


def parse_uevent(data: bytes) -> dict[str, str]:
    """Parse a kernel kobject uevent payload into string fields."""

    fields = [field for field in data.split(b"\0") if field]
    event: dict[str, str] = {}

    if fields:
        header = fields[0].decode("utf-8", errors="replace")
        if "@" in header:
            action, devpath = header.split("@", 1)
            event["ACTION"] = action
            event["DEVPATH"] = devpath

    for field in fields[1:]:
        decoded = field.decode("utf-8", errors="replace")
        if "=" not in decoded:
            continue
        key, value = decoded.split("=", 1)
        event[key] = value

    return event


class PowerSupplyUeventSource:
    """Read power_supply invalidation events from kobject uevent netlink."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    @classmethod
    def open(cls) -> "PowerSupplyUeventSource":
        sock = socket.socket(
            socket.AF_NETLINK,
            socket.SOCK_DGRAM,
            NETLINK_KOBJECT_UEVENT,
        )
        try:
            sock.bind((os.getpid(), UEVENT_GROUP))
            sock.setblocking(False)
        except Exception:
            sock.close()
            raise
        return cls(sock)

    def fileno(self) -> int:
        return self._sock.fileno()

    def drain_relevant_events(self) -> bool:
        relevant = False
        while True:
            try:
                payload = self._sock.recv(UEVENT_BUFFER_SIZE)
            except BlockingIOError:
                break

            event = parse_uevent(payload)
            if event.get("SUBSYSTEM") == "power_supply":
                relevant = True

        return relevant

    def close(self) -> None:
        self._sock.close()


def _systemd_notify(message: str, environment: Mapping[str, str]) -> bool:
    notify_socket = environment.get("NOTIFY_SOCKET")
    if not notify_socket:
        return False

    address = notify_socket
    if notify_socket.startswith("@"):
        address = "\0" + notify_socket[1:]

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
    except OSError:
        return False

    return True


class SystemdWatchdog:
    """Track systemd watchdog deadlines without a heartbeat thread."""

    def __init__(
        self,
        *,
        enabled: bool,
        interval: Optional[float],
        monotonic: Callable[[], float] = time.monotonic,
        notify: Optional[Callable[[str], bool]] = None,
    ) -> None:
        self.enabled = enabled
        self._interval = interval
        self._monotonic = monotonic
        self._notify = notify or (lambda _message: False)
        self._next_deadline = (
            monotonic() + interval
            if enabled and interval is not None
            else None
        )

    @classmethod
    def from_environment(
        cls,
        environment: Optional[Mapping[str, str]] = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        notify: Optional[Callable[[str], bool]] = None,
    ) -> "SystemdWatchdog":
        env = os.environ if environment is None else environment
        raw_usec = env.get("WATCHDOG_USEC")
        notify_socket = env.get("NOTIFY_SOCKET")

        try:
            watchdog_usec = int(raw_usec) if raw_usec is not None else 0
        except ValueError:
            watchdog_usec = 0

        raw_pid = env.get("WATCHDOG_PID")
        if raw_pid is not None:
            try:
                if int(raw_pid) != os.getpid():
                    watchdog_usec = 0
            except ValueError:
                watchdog_usec = 0

        enabled = watchdog_usec > 0 and bool(notify_socket)
        interval = watchdog_usec / 2_000_000 if enabled else None

        if notify is None:
            notify = lambda message: _systemd_notify(message, env)

        return cls(
            enabled=enabled,
            interval=interval,
            monotonic=monotonic,
            notify=notify,
        )

    def seconds_until_ping(self, now: Optional[float] = None) -> Optional[float]:
        if not self.enabled or self._next_deadline is None:
            return None
        current = self._monotonic() if now is None else now
        return max(0.0, self._next_deadline - current)

    def ping_if_due(self, now: Optional[float] = None) -> bool:
        if not self.enabled or self._next_deadline is None or self._interval is None:
            return False

        current = self._monotonic() if now is None else now
        if current < self._next_deadline:
            return False

        self._notify("WATCHDOG=1")
        self._next_deadline = current + self._interval
        return True


class DaemonScheduler:
    """Wait for the next policy re-evaluation trigger."""

    def __init__(
        self,
        backend,
        *,
        event_source=None,
        selector=None,
        watchdog: Optional[SystemdWatchdog] = None,
        monotonic: Callable[[], float] = time.monotonic,
        periodic_interval: float = 2.0,
    ) -> None:
        self._backend = backend
        self._event_source = event_source
        self._selector = selector or selectors.DefaultSelector()
        self._watchdog = watchdog or SystemdWatchdog.from_environment(
            monotonic=monotonic
        )
        self._monotonic = monotonic
        self._periodic_interval = periodic_interval

        self.using_event_source = event_source is not None
        self.using_periodic_fallback = (
            not backend.requires_periodic_tick and event_source is None
        )
        self._periodic = (
            backend.requires_periodic_tick or self.using_periodic_fallback
        )
        self._next_periodic = (
            monotonic() + periodic_interval if self._periodic else None
        )

        if event_source is not None:
            self._selector.register(
                event_source,
                selectors.EVENT_READ,
                data=event_source,
            )

    def _timeout(self, now: float) -> Optional[float]:
        deadlines = []

        if self._next_periodic is not None:
            deadlines.append(max(0.0, self._next_periodic - now))

        watchdog_timeout = self._watchdog.seconds_until_ping(now)
        if watchdog_timeout is not None:
            deadlines.append(watchdog_timeout)

        if not deadlines:
            return None
        return min(deadlines)

    def _advance_periodic_deadline(self, now: float) -> None:
        if self._next_periodic is None:
            return
        while self._next_periodic <= now:
            self._next_periodic += self._periodic_interval

    def wait_for_policy_trigger(self) -> bool:
        while True:
            now = self._monotonic()
            events = self._selector.select(self._timeout(now))

            event_triggered = False
            for key, _mask in events:
                source = key.data
                if source.drain_relevant_events():
                    event_triggered = True

            now = self._monotonic()
            self._watchdog.ping_if_due(now)

            if event_triggered:
                return True

            if self._next_periodic is not None and now >= self._next_periodic:
                self._advance_periodic_deadline(now)
                return True

    def close(self) -> None:
        try:
            self._selector.close()
        finally:
            if self._event_source is not None:
                self._event_source.close()
