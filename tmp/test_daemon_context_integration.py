#!/usr/bin/env python3
from auto_cpufreq.modules.daemon_scheduler import PolicyTrigger
from auto_cpufreq.modules.policy import PowerSource


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


def test_daemon_deduplicates_power_events_but_not_config():
    import auto_cpufreq.bin.auto_cpufreq as cli

    applied = []
    current_sources = iter(
        [
            PowerSource.BATTERY,
            PowerSource.BATTERY,
            PowerSource.BATTERY,
        ]
    )

    class Scheduler:
        def __init__(self):
            self._triggers = iter(
                [
                    frozenset({PolicyTrigger.POWER_SUPPLY}),
                    frozenset({PolicyTrigger.POWER_SUPPLY}),
                    frozenset({PolicyTrigger.CONFIG}),
                ]
            )

        def wait_for_policy_trigger(self):
            try:
                return next(self._triggers)
            except StopIteration:
                raise KeyboardInterrupt

    def fake_set_autofreq(source=None):
        if source is None:
            source = PowerSource.CHARGER
        applied.append(source)
        return source

    original = {
        "set_autofreq": cli.set_autofreq,
        "get_power_source": getattr(cli, "get_power_source", None),
        "footer": cli.footer,
        "gov_check": cli.gov_check,
        "cpufreqctl": cli.cpufreqctl,
        "print_system_report": cli.print_system_report,
    }

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
        cli.set_autofreq = original["set_autofreq"]
        if original["get_power_source"] is None:
            delattr(cli, "get_power_source")
        else:
            cli.get_power_source = original["get_power_source"]
        cli.footer = original["footer"]
        cli.gov_check = original["gov_check"]
        cli.cpufreqctl = original["cpufreqctl"]
        cli.print_system_report = original["print_system_report"]

    assert applied == [
        PowerSource.CHARGER,
        PowerSource.BATTERY,
        PowerSource.BATTERY,
    ]


def main():
    test_set_autofreq_accepts_authoritative_source()
    test_daemon_deduplicates_power_events_but_not_config()
    print("daemon authoritative context checks passed")


if __name__ == "__main__":
    main()
