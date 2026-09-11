import inspect
import os
from configparser import ConfigParser
from contextlib import redirect_stdout
from io import StringIO

import auto_cpufreq.core as core
from auto_cpufreq.bin import auto_cpufreq as cli
from auto_cpufreq.modules.intel_rapl import RaplEnvelopeTargets
from auto_cpufreq.modules.policy import (
    LegacyPolicy,
    ModernIntelActions,
    ModernIntelHwpPolicy,
    PolicyActions,
    PowerSource,
)


def legacy_actions(events):
    return PolicyActions(
        apply_charger=lambda: events.append("legacy-apply-charger"),
        apply_battery=lambda: events.append("legacy-apply-battery"),
        monitor_charger=lambda: events.append("legacy-monitor-charger"),
        monitor_battery=lambda: events.append("legacy-monitor-battery"),
    )


def test_backend_envelope_routing():
    events = []
    legacy = LegacyPolicy(legacy_actions(events))
    legacy.apply_power_envelope(PowerSource.CHARGER)
    assert events == []

    modern = ModernIntelHwpPolicy(
        legacy_actions(events),
        ModernIntelActions(
            apply=lambda source: events.append(("modern-apply", source)),
            monitor=lambda source: events.append(("modern-monitor", source)),
            apply_power_envelope=lambda source: events.append(("rapl", source)),
        ),
    )
    modern.monitor(PowerSource.CHARGER)
    assert events == [("modern-monitor", PowerSource.CHARGER)]
    modern.apply_power_envelope(PowerSource.BATTERY)
    assert events[-1] == ("rapl", PowerSource.BATTERY)

    # The callback is optional so Stage 3 callers remain source-compatible.
    no_rapl = ModernIntelHwpPolicy(
        legacy_actions([]),
        ModernIntelActions(
            apply=lambda source: None,
            monitor=lambda source: None,
        ),
    )
    no_rapl.apply_power_envelope(PowerSource.CHARGER)


class FakeController:
    def __init__(self):
        self.calls = []

    def apply(self, targets):
        self.calls.append(("apply", targets))
        return ()

    def restore_owned(self):
        self.calls.append(("restore",))
        return ()


def with_config(mapping):
    conf = ConfigParser()
    conf.read_dict(mapping)
    return conf


def run_core_case(conf, source, marker):
    controller = FakeController()
    original_get_config = core.config.get_config
    original_controller = getattr(core, "IntelRaplController", None)
    old_marker = os.environ.get("AUTO_CPUFREQ_RAPL_FAILSAFE")

    core.config.get_config = lambda: conf
    core.IntelRaplController = lambda: controller
    if marker is None:
        os.environ.pop("AUTO_CPUFREQ_RAPL_FAILSAFE", None)
    else:
        os.environ["AUTO_CPUFREQ_RAPL_FAILSAFE"] = marker

    output = StringIO()
    try:
        with redirect_stdout(output):
            core.apply_modern_intel_rapl(source)
    finally:
        core.config.get_config = original_get_config
        if original_controller is None:
            delattr(core, "IntelRaplController")
        else:
            core.IntelRaplController = original_controller
        if old_marker is None:
            os.environ.pop("AUTO_CPUFREQ_RAPL_FAILSAFE", None)
        else:
            os.environ["AUTO_CPUFREQ_RAPL_FAILSAFE"] = old_marker

    return controller.calls, output.getvalue()


def test_core_gate_and_config_semantics():
    calls, _ = run_core_case(
        with_config({"intel_power": {"enable_rapl_envelopes": "false"}}),
        PowerSource.CHARGER,
        "1",
    )
    assert calls == [("restore",)]

    calls, _ = run_core_case(
        with_config({
            "intel_power": {"enable_rapl_envelopes": "true"},
            "battery": {},
        }),
        PowerSource.BATTERY,
        "1",
    )
    assert calls == [("apply", RaplEnvelopeTargets())]

    calls, _ = run_core_case(
        with_config({
            "intel_power": {"enable_rapl_envelopes": "true"},
            "charger": {"rapl_package_long_term_w": "12.5"},
        }),
        PowerSource.CHARGER,
        "1",
    )
    assert calls == [("apply", RaplEnvelopeTargets(long_term_uw=12_500_000))]

    calls, output = run_core_case(
        with_config({
            "intel_power": {"enable_rapl_envelopes": "true"},
            "charger": {"rapl_package_long_term_w": "12"},
        }),
        PowerSource.CHARGER,
        None,
    )
    assert calls == []
    assert "failsafe" in output.lower()

    calls, output = run_core_case(
        with_config({
            "intel_power": {"enable_rapl_envelopes": "true"},
            "charger": {"rapl_package_long_term_w": "not-a-number"},
        }),
        PowerSource.CHARGER,
        "1",
    )
    assert calls == []
    assert "rapl" in output.lower()


def test_daemon_only_ordering():
    order = []

    class Backend:
        def apply_power_envelope(self, source):
            order.append(("rapl", source))

    originals = {
        "footer": cli.footer,
        "gov_check": cli.gov_check,
        "cpufreqctl": cli.cpufreqctl,
        "print_system_report": cli.print_system_report,
        "set_autofreq": cli.set_autofreq,
        "get_policy_backend": cli.get_policy_backend,
    }
    cli.footer = lambda: None
    cli.gov_check = lambda: None
    cli.cpufreqctl = lambda: None
    cli.print_system_report = lambda: None
    cli.set_autofreq = lambda source=None: (
        order.append(("hwp", source)), source
    )[1]
    cli.get_policy_backend = lambda: Backend()

    try:
        result = cli._daemon_policy_cycle(PowerSource.CHARGER)
    finally:
        for name, value in originals.items():
            setattr(cli, name, value)

    assert result is PowerSource.CHARGER
    assert order == [
        ("hwp", PowerSource.CHARGER),
        ("rapl", PowerSource.CHARGER),
    ]

    # Monitor and --live continue through existing policy functions only.
    assert "apply_power_envelope" not in inspect.getsource(core.set_autofreq)
    assert "apply_power_envelope" not in inspect.getsource(core.mon_autofreq)
    main_source = inspect.getsource(cli.main)
    live_block = main_source.split("elif live:", 1)[1].split("elif daemon:", 1)[0]
    assert "apply_power_envelope" not in live_block


def main():
    test_backend_envelope_routing()
    test_core_gate_and_config_semantics()
    test_daemon_only_ordering()
    print("Intel RAPL daemon integration checks passed")


if __name__ == "__main__":
    main()
