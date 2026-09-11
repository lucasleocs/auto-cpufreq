#!/usr/bin/env python3
from auto_cpufreq.modules.daemon_scheduler import (
    DaemonScheduler,
    PolicyTrigger,
    SystemdWatchdog,
    WakeupSource,
    should_reapply_policy,
)
from auto_cpufreq.modules.policy import LegacyPolicy, ModernIntelHwpPolicy, PolicyActions, PowerSource


def actions():
    noop = lambda: None
    return PolicyActions(noop, noop, noop, noop)


def test_decision_matrix():
    charger = PowerSource.CHARGER
    battery = PowerSource.BATTERY

    assert not should_reapply_policy(
        frozenset({PolicyTrigger.POWER_SUPPLY}),
        charger,
        charger,
    )
    assert should_reapply_policy(
        frozenset({PolicyTrigger.POWER_SUPPLY}),
        charger,
        battery,
    )
    assert should_reapply_policy(
        frozenset({PolicyTrigger.CONFIG}),
        charger,
        charger,
    )
    assert should_reapply_policy(
        frozenset({PolicyTrigger.PERIODIC}),
        charger,
        charger,
    )
    assert should_reapply_policy(
        frozenset({PolicyTrigger.CONFIG, PolicyTrigger.POWER_SUPPLY}),
        charger,
        charger,
    )


def test_scheduler_reports_config_reason():
    modern = ModernIntelHwpPolicy(actions())
    wakeup = WakeupSource.open()
    scheduler = DaemonScheduler(
        modern,
        event_source=None,
        config_source=wakeup,
        watchdog=SystemdWatchdog.from_environment({}),
        periodic_interval=60.0,
    )
    try:
        wakeup.notify()
        assert scheduler.wait_for_policy_trigger() == frozenset({PolicyTrigger.CONFIG})
    finally:
        scheduler.close()


def test_scheduler_reports_power_reason():
    modern = ModernIntelHwpPolicy(actions())
    wakeup = WakeupSource.open()
    scheduler = DaemonScheduler(
        modern,
        event_source=wakeup,
        config_source=None,
        watchdog=SystemdWatchdog.from_environment({}),
        periodic_interval=60.0,
    )
    try:
        wakeup.notify()
        assert scheduler.wait_for_policy_trigger() == frozenset({PolicyTrigger.POWER_SUPPLY})
    finally:
        scheduler.close()


def test_scheduler_reports_periodic_reason():
    legacy = LegacyPolicy(actions())
    scheduler = DaemonScheduler(
        legacy,
        event_source=None,
        config_source=None,
        watchdog=SystemdWatchdog.from_environment({}),
        periodic_interval=0.001,
    )
    try:
        assert scheduler.wait_for_policy_trigger() == frozenset({PolicyTrigger.PERIODIC})
    finally:
        scheduler.close()


def main():
    test_decision_matrix()
    test_scheduler_reports_config_reason()
    test_scheduler_reports_power_reason()
    test_scheduler_reports_periodic_reason()
    print("event context deduplication checks passed")


if __name__ == "__main__":
    main()
