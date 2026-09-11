#!/usr/bin/env python3
import sys
import tempfile
from pathlib import Path

from auto_cpufreq.config.config import _Config
from auto_cpufreq.modules.daemon_scheduler import (
    DaemonScheduler,
    SystemdWatchdog,
    WakeupSource,
    create_daemon_scheduler,
)
from auto_cpufreq.modules.policy import (
    ModernIntelHwpPolicy,
    PolicyActions,
)


def policy_actions():
    noop = lambda: None
    return PolicyActions(noop, noop, noop, noop)


def test_config_callback():
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


def test_config_wakeup_source():
    modern = ModernIntelHwpPolicy(policy_actions())
    wakeup = WakeupSource.open()
    scheduler = DaemonScheduler(
        modern,
        event_source=None,
        config_source=wakeup,
        watchdog=SystemdWatchdog.from_environment({}),
        periodic_interval=60.0,
    )

    wakeup.notify()
    assert scheduler.wait_for_policy_trigger() is True
    scheduler.close()


def test_modern_netlink_failure_falls_back():
    modern = ModernIntelHwpPolicy(policy_actions())

    def fail_open():
        raise OSError("netlink unavailable")

    scheduler = create_daemon_scheduler(
        modern,
        event_source_factory=fail_open,
        watchdog=SystemdWatchdog.from_environment({}),
    )
    try:
        assert scheduler.using_event_source is False
        assert scheduler.using_periodic_fallback is True
        assert scheduler.event_source_error is not None
        assert "netlink unavailable" in scheduler.event_source_error
    finally:
        scheduler.close()


def test_modern_config_wakeup_failure_falls_back():
    modern = ModernIntelHwpPolicy(policy_actions())

    def fail_config_open():
        raise OSError("config wakeup unavailable")

    scheduler = create_daemon_scheduler(
        modern,
        event_source_factory=WakeupSource.open,
        config_source_factory=fail_config_open,
        watchdog=SystemdWatchdog.from_environment({}),
    )
    try:
        assert scheduler.using_event_source is True
        assert scheduler.using_config_source is False
        assert scheduler.using_periodic_fallback is True
        assert scheduler.config_source_error is not None
        assert "config wakeup unavailable" in scheduler.config_source_error
    finally:
        scheduler.close()


def test_daemon_cli_uses_scheduler():
    import auto_cpufreq.bin.auto_cpufreq as cli

    assert hasattr(cli, "create_daemon_scheduler")
    assert hasattr(cli.conf, "set_change_callback")

    events = []

    class FakeNotifier:
        def start(self):
            events.append("notifier-start")

        def stop(self):
            events.append("notifier-stop")

    class FakeScheduler:
        using_periodic_fallback = False
        event_source_error = None
        config_source_error = None

        def notify_config_change(self):
            events.append("config-wakeup")

        def wait_for_policy_trigger(self):
            events.append("wait")
            raise KeyboardInterrupt

        def close(self):
            events.append("scheduler-close")

    scheduler = FakeScheduler()
    callbacks = []

    cli.IS_INSTALLED_WITH_SNAP = False
    cli.root_check = lambda: events.append("root-check")
    cli.file_stats = lambda: events.append("file-stats")
    cli.gnome_power_detect = lambda: events.append("gnome-detect")
    cli.tlp_service_detect = lambda: events.append("tlp-detect")
    cli.start_battery_daemon = lambda: events.append("battery-daemon")
    cli.gov_check = lambda: events.append("gov-check")
    cli.cpufreqctl = lambda: events.append("cpufreqctl")
    cli.print_system_report = lambda: events.append("report")
    cli.set_autofreq = lambda: events.append("apply")
    cli.get_policy_backend = lambda: "backend"
    cli.create_daemon_scheduler = lambda backend: (
        events.append(("scheduler", backend)) or scheduler
    )
    cli.conf.notifier = FakeNotifier()
    cli.conf.set_change_callback = lambda callback: callbacks.append(callback)

    old_argv = sys.argv[:]
    sys.argv = ["auto-cpufreq", "--daemon"]
    try:
        cli.main.callback(
            monitor=False,
            live=False,
            daemon=True,
            install=False,
            update=None,
            remove=False,
            force=None,
            turbo=None,
            config=None,
            stats=False,
            pp=False,
            get_state=False,
            bluetooth_boot_off=False,
            bluetooth_boot_on=False,
            debug=False,
            version=False,
            donate=False,
        )
    finally:
        sys.argv = old_argv

    assert events.count("apply") == 1
    assert "wait" in events
    assert "scheduler-close" in events
    assert "notifier-stop" in events
    assert callbacks[0] == scheduler.notify_config_change
    assert callbacks[-1] is None


def main():
    test_config_callback()
    test_config_wakeup_source()
    test_modern_netlink_failure_falls_back()
    test_modern_config_wakeup_failure_falls_back()
    test_daemon_cli_uses_scheduler()
    print("daemon event integration checks passed")


if __name__ == "__main__":
    main()
