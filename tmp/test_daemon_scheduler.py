#!/usr/bin/env python3
import os
from types import SimpleNamespace

from auto_cpufreq.modules.policy import (
    LegacyPolicy,
    ModernIntelHwpPolicy,
    PolicyActions,
)
from auto_cpufreq.modules.daemon_scheduler import (
    DaemonScheduler,
    SystemdWatchdog,
    parse_uevent,
)


class FakeClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeSelector:
    def __init__(self, clock):
        self.clock = clock
        self.registered = []
        self.events = []
        self.timeouts = []

    def register(self, fileobj, events, data=None):
        self.registered.append((fileobj, events, data))

    def select(self, timeout=None):
        self.timeouts.append(timeout)
        if self.events:
            return self.events.pop(0)
        if timeout is not None:
            self.clock.value += timeout
        return []

    def close(self):
        pass


class FakeSource:
    def __init__(self, relevant=True):
        self.relevant = relevant
        self.closed = False

    def fileno(self):
        return 42

    def drain_relevant_events(self):
        return self.relevant

    def close(self):
        self.closed = True


def policy_actions():
    noop = lambda: None
    return PolicyActions(noop, noop, noop, noop)


def main():
    legacy = LegacyPolicy(policy_actions())
    modern = ModernIntelHwpPolicy(policy_actions())
    assert legacy.requires_periodic_tick is True
    assert modern.requires_periodic_tick is False

    payload = (
        b"change@/devices/platform/AC/power_supply/AC\0"
        b"ACTION=change\0"
        b"DEVPATH=/devices/platform/AC/power_supply/AC\0"
        b"SUBSYSTEM=power_supply\0"
        b"POWER_SUPPLY_NAME=AC\0"
    )
    event = parse_uevent(payload)
    assert event["ACTION"] == "change"
    assert event["DEVPATH"] == "/devices/platform/AC/power_supply/AC"
    assert event["SUBSYSTEM"] == "power_supply"
    assert event["POWER_SUPPLY_NAME"] == "AC"

    messages = []
    clock = FakeClock()
    watchdog = SystemdWatchdog.from_environment(
        {
            "WATCHDOG_USEC": "10000000",
            "WATCHDOG_PID": str(os.getpid()),
            "NOTIFY_SOCKET": "/run/systemd/notify",
        },
        monotonic=clock,
        notify=lambda message: messages.append(message) or True,
    )
    assert watchdog.enabled is True
    assert watchdog.seconds_until_ping(clock()) == 5.0
    assert watchdog.ping_if_due(clock()) is False
    clock.value = 5.0
    assert watchdog.ping_if_due(clock()) is True
    assert messages == ["WATCHDOG=1"]
    assert watchdog.seconds_until_ping(clock()) == 5.0

    wrong_pid = SystemdWatchdog.from_environment(
        {
            "WATCHDOG_USEC": "10000000",
            "WATCHDOG_PID": str(os.getpid() + 1),
            "NOTIFY_SOCKET": "/run/systemd/notify",
        },
        monotonic=clock,
        notify=lambda message: True,
    )
    assert wrong_pid.enabled is False

    clock = FakeClock()
    selector = FakeSelector(clock)
    source = FakeSource(relevant=True)
    scheduler = DaemonScheduler(
        modern,
        event_source=source,
        selector=selector,
        watchdog=SystemdWatchdog.from_environment({}, monotonic=clock),
        monotonic=clock,
        periodic_interval=2.0,
    )
    selector.events.append(
        [(SimpleNamespace(data=source), 1)]
    )
    assert scheduler.using_event_source is True
    assert scheduler.using_periodic_fallback is False
    assert scheduler.wait_for_policy_trigger() is True
    assert selector.timeouts == [None]
    scheduler.close()
    assert source.closed is True

    clock = FakeClock()
    selector = FakeSelector(clock)
    scheduler = DaemonScheduler(
        modern,
        event_source=None,
        selector=selector,
        watchdog=SystemdWatchdog.from_environment({}, monotonic=clock),
        monotonic=clock,
        periodic_interval=2.0,
    )
    assert scheduler.using_event_source is False
    assert scheduler.using_periodic_fallback is True
    assert scheduler.wait_for_policy_trigger() is True
    assert selector.timeouts == [2.0]

    clock = FakeClock()
    selector = FakeSelector(clock)
    scheduler = DaemonScheduler(
        legacy,
        event_source=None,
        selector=selector,
        watchdog=SystemdWatchdog.from_environment({}, monotonic=clock),
        monotonic=clock,
        periodic_interval=2.0,
    )
    assert scheduler.using_periodic_fallback is False
    assert scheduler.wait_for_policy_trigger() is True
    assert selector.timeouts == [2.0]

    print("event-driven daemon scheduler checks passed")


if __name__ == "__main__":
    main()
