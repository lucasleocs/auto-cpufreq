#!/usr/bin/env python3
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from auto_cpufreq.config.config import _Config
from auto_cpufreq.modules.daemon_scheduler import (
    DaemonScheduler,
    PolicyTrigger,
    SystemdWatchdog,
    WakeupSource,
    create_daemon_scheduler,
    parse_uevent,
    should_reapply_policy,
)
from auto_cpufreq.modules.policy import (
    LegacyPolicy,
    ModernIntelHwpPolicy,
    PolicyActions,
    PowerSource,
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


def actions():
    noop = lambda: None
    return PolicyActions(noop, noop, noop, noop)


def test_backend_tick_contract():
    assert LegacyPolicy(actions()).requires_periodic_tick is True
    assert ModernIntelHwpPolicy(actions()).requires_periodic_tick is False


def test_uevent_parser():
    payload = (
        b"change@/devices/platform/AC/power_supply/AC\0"
        b"ACTION=change\0"
        b"DEVPATH=/devices/platform/AC/power_supply/AC\0"
        b"SUBSYSTEM=power_supply\0"
        b"POWER_SUPPLY_NAME=AC\0"
    )
    event = parse_uevent(payload)
    assert event == {
        "ACTION": "change",
        "DEVPATH": "/devices/platform/AC/power_supply/AC",
        "SUBSYSTEM": "power_supply",
        "POWER_SUPPLY_NAME": "AC",
    }


def test_trigger_decision_matrix():
    charger = PowerSource.CHARGER
    battery = PowerSource.BATTERY
    power = frozenset({PolicyTrigger.POWER_SUPPLY})

    assert should_reapply_policy(power, charger, charger) is False
    assert should_reapply_policy(power, charger, battery) is True
    assert should_reapply_policy(
        frozenset({PolicyTrigger.CONFIG}), charger, charger
    ) is True
    assert should_reapply_policy(
        frozenset({PolicyTrigger.PERIODIC}), charger, charger
    ) is True
    assert should_reapply_policy(
        frozenset({PolicyTrigger.CONFIG, PolicyTrigger.POWER_SUPPLY}),
        charger,
        charger,
    ) is True


def test_watchdog_deadline_without_policy_trigger():
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

    selector = FakeSelector(clock)
    legacy = LegacyPolicy(actions())
    scheduler = DaemonScheduler(
        legacy,
        selector=selector,
        watchdog=watchdog,
        monotonic=clock,
        periodic_interval=20.0,
    )
    try:
        triggers = scheduler.wait_for_policy_trigger()
    finally:
        scheduler.close()

    assert messages == ["WATCHDOG=1", "WATCHDOG=1", "WATCHDOG=1", "WATCHDOG=1"]
    assert triggers == frozenset({PolicyTrigger.PERIODIC})
    assert selector.timeouts == [5.0, 5.0, 5.0, 5.0]


def test_event_config_and_combined_reasons():
    modern = ModernIntelHwpPolicy(actions())
    clock = FakeClock()
    selector = FakeSelector(clock)
    event_source = FakeSource()
    config_source = FakeSource()
    scheduler = DaemonScheduler(
        modern,
        event_source=event_source,
        config_source=config_source,
        selector=selector,
        watchdog=SystemdWatchdog.from_environment({}, monotonic=clock),
        monotonic=clock,
    )
    try:
        selector.events.append([
            (SimpleNamespace(data=(event_source, PolicyTrigger.POWER_SUPPLY)), 1),
            (SimpleNamespace(data=(config_source, PolicyTrigger.CONFIG)), 1),
        ])
        assert scheduler.wait_for_policy_trigger() == frozenset(
            {PolicyTrigger.POWER_SUPPLY, PolicyTrigger.CONFIG}
        )
    finally:
        scheduler.close()

    assert event_source.closed is True
    assert config_source.closed is True


def test_periodic_legacy_and_modern_fallback():
    for backend, expect_fallback in (
        (LegacyPolicy(actions()), False),
        (ModernIntelHwpPolicy(actions()), True),
    ):
        clock = FakeClock()
        selector = FakeSelector(clock)
        scheduler = DaemonScheduler(
            backend,
            event_source=None,
            config_source=None,
            selector=selector,
            watchdog=SystemdWatchdog.from_environment({}, monotonic=clock),
            monotonic=clock,
            periodic_interval=2.0,
        )
        try:
            assert scheduler.using_periodic_fallback is expect_fallback
            assert scheduler.wait_for_policy_trigger() == frozenset(
                {PolicyTrigger.PERIODIC}
            )
            assert selector.timeouts == [2.0]
        finally:
            scheduler.close()


def test_factory_failure_preserves_periodic_fallback():
    modern = ModernIntelHwpPolicy(actions())

    def fail_event():
        raise OSError("netlink unavailable")

    scheduler = create_daemon_scheduler(
        modern,
        event_source_factory=fail_event,
        config_source_factory=WakeupSource.open,
        watchdog=SystemdWatchdog.from_environment({}),
    )
    try:
        assert scheduler.using_event_source is False
        assert scheduler.using_config_source is True
        assert scheduler.using_periodic_fallback is True
        assert scheduler.event_source_error == "netlink unavailable"
    finally:
        scheduler.close()


def test_config_callback_wakes_daemon_channel():
    cfg = _Config()
    calls = []
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "auto-cpufreq.conf"
        path.write_text("[charger]\nturbo = auto\n", encoding="utf-8")
        cfg.path = str(path)
        cfg.set_change_callback(lambda: calls.append("changed"))
        cfg.update_config()
    assert calls == ["changed"]
    cfg.set_change_callback(None)


def test_set_autofreq_accepts_authoritative_source():
    import auto_cpufreq.core as core

    applied = []

    class Backend:
        def apply(self, source):
            applied.append(source)

    original_backend = core.get_policy_backend
    try:
        core.get_policy_backend = lambda: Backend()
        result = core.set_autofreq(PowerSource.BATTERY)
    finally:
        core.get_policy_backend = original_backend

    assert result is PowerSource.BATTERY
    assert applied == [PowerSource.BATTERY]


def test_daemon_deduplicates_delayed_second_power_event():
    import auto_cpufreq.bin.auto_cpufreq as cli

    applied = []
    current_sources = iter([
        PowerSource.BATTERY,
        PowerSource.BATTERY,
        PowerSource.BATTERY,
    ])

    class Scheduler:
        def __init__(self):
            self.triggers = iter([
                frozenset({PolicyTrigger.POWER_SUPPLY}),
                frozenset({PolicyTrigger.POWER_SUPPLY}),
                frozenset({PolicyTrigger.CONFIG}),
            ])

        def wait_for_policy_trigger(self):
            try:
                return next(self.triggers)
            except StopIteration:
                raise KeyboardInterrupt

    originals = {
        "set_autofreq": cli.set_autofreq,
        "get_power_source": cli.get_power_source,
        "footer": cli.footer,
        "gov_check": cli.gov_check,
        "cpufreqctl": cli.cpufreqctl,
        "print_system_report": cli.print_system_report,
    }

    def fake_set_autofreq(source=None):
        if source is None:
            source = PowerSource.CHARGER
        applied.append(source)
        return source

    try:
        cli.set_autofreq = fake_set_autofreq
        cli.get_power_source = lambda: next(current_sources)
        cli.footer = lambda: None
        cli.gov_check = lambda: None
        cli.cpufreqctl = lambda: None
        cli.print_system_report = lambda: None
        try:
            cli._run_daemon_policy_loop(Scheduler())
        except KeyboardInterrupt:
            pass
    finally:
        for name, value in originals.items():
            setattr(cli, name, value)

    assert applied == [
        PowerSource.CHARGER,
        PowerSource.BATTERY,
        PowerSource.BATTERY,
    ]


def main():
    test_backend_tick_contract()
    test_uevent_parser()
    test_trigger_decision_matrix()
    test_watchdog_deadline_without_policy_trigger()
    test_event_config_and_combined_reasons()
    test_periodic_legacy_and_modern_fallback()
    test_factory_failure_preserves_periodic_fallback()
    test_config_callback_wakes_daemon_channel()
    test_set_autofreq_accepts_authoritative_source()
    test_daemon_deduplicates_delayed_second_power_event()
    print("final event-loop regression checks passed")


if __name__ == "__main__":
    main()
