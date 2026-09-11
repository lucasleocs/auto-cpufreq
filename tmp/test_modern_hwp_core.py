from configparser import ConfigParser
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace

import auto_cpufreq.core as core
from auto_cpufreq.modules.policy import PowerSource


def config_for(charger=None, battery=None):
    conf = ConfigParser()
    conf["charger"] = charger or {}
    conf["battery"] = battery or {}
    return conf


def run_case(conf, source, *, governor_override="default", turbo_override="auto"):
    calls = []

    originals = {
        "get_config": core.config.get_config,
        "get_override": core.get_override,
        "get_turbo_override": core.get_turbo_override,
        "run": core.run,
        "get_hwp_dynamic_boost_target": core.get_hwp_dynamic_boost_target,
        "set_hwp_dynamic_boost": core.set_hwp_dynamic_boost,
        "set_energy_perf_preference": core.set_energy_perf_preference,
        "set_energy_perf_bias": core.set_energy_perf_bias,
        "set_platform_profile": core.set_platform_profile,
        "set_turbo": core.set_turbo,
        "set_frequencies": core.set_frequencies,
        "footer": core.footer,
        "get_load": core.get_load,
    }

    try:
        core.config.get_config = lambda: conf
        core.get_override = lambda: governor_override
        core.get_turbo_override = lambda: turbo_override
        core.run = lambda argv: (
            calls.append(("run", tuple(argv))) or SimpleNamespace(returncode=0)
        )
        core.get_hwp_dynamic_boost_target = lambda _conf, profile: (
            True if profile == "charger" else False
        )
        core.set_hwp_dynamic_boost = lambda value: calls.append(("dynboost", value)) or True
        core.set_energy_perf_preference = lambda value: calls.append(("epp", value))
        core.set_energy_perf_bias = lambda _conf, profile: calls.append(("epb", profile))
        core.set_platform_profile = lambda _conf, profile: calls.append(("platform", profile))
        core.set_turbo = lambda value: calls.append(("turbo", value))
        core.set_frequencies = lambda profile: calls.append(("frequencies", profile))
        core.footer = lambda *args, **kwargs: calls.append(("footer",))
        core.get_load = lambda: (_ for _ in ()).throw(
            AssertionError("Modern Intel HWP policy must not sample CPU load")
        )

        with redirect_stdout(StringIO()) as output:
            core.set_modern_intel_hwp(source)
        return calls, output.getvalue()
    finally:
        core.config.get_config = originals["get_config"]
        core.get_override = originals["get_override"]
        core.get_turbo_override = originals["get_turbo_override"]
        core.run = originals["run"]
        core.get_hwp_dynamic_boost_target = originals["get_hwp_dynamic_boost_target"]
        core.set_hwp_dynamic_boost = originals["set_hwp_dynamic_boost"]
        core.set_energy_perf_preference = originals["set_energy_perf_preference"]
        core.set_energy_perf_bias = originals["set_energy_perf_bias"]
        core.set_platform_profile = originals["set_platform_profile"]
        core.set_turbo = originals["set_turbo"]
        core.set_frequencies = originals["set_frequencies"]
        core.footer = originals["footer"]
        core.get_load = originals["get_load"]


def main():
    calls, output = run_case(config_for(), PowerSource.CHARGER)
    assert ("run", ("cpufreqctl.auto-cpufreq", "--governor", "--set=powersave")) in calls
    assert ("epp", "balance_performance") in calls
    assert ("dynboost", True) in calls
    assert ("turbo", True) in calls
    assert ("epb", "charger") in calls
    assert ("platform", "charger") in calls
    assert ("frequencies", "charger") in calls
    assert "hardware-managed" in output

    calls, _ = run_case(config_for(), PowerSource.BATTERY)
    assert ("run", ("cpufreqctl.auto-cpufreq", "--governor", "--set=powersave")) in calls
    assert ("epp", "balance_power") in calls
    assert ("dynboost", False) in calls
    assert ("turbo", True) in calls
    assert ("frequencies", "battery") in calls

    explicit = config_for(
        charger={
            "governor": "performance",
            "energy_performance_preference": "balance_power",
            "turbo": "never",
        }
    )
    calls, output = run_case(explicit, PowerSource.CHARGER)
    assert ("run", ("cpufreqctl.auto-cpufreq", "--governor", "--set=performance")) in calls
    assert not any(call[0] == "epp" for call in calls)
    assert ("turbo", False) in calls
    assert "performance governor controls EPP" in output

    calls, _ = run_case(
        config_for(charger={"governor": "performance", "turbo": "never"}),
        PowerSource.CHARGER,
        governor_override="powersave",
        turbo_override="always",
    )
    assert ("run", ("cpufreqctl.auto-cpufreq", "--governor", "--set=powersave")) in calls
    assert ("epp", "balance_performance") in calls
    assert ("turbo", True) in calls

    custom_epp = config_for(battery={"energy_performance_preference": "power"})
    calls, _ = run_case(custom_epp, PowerSource.BATTERY)
    assert ("epp", "power") in calls

    print("Modern Intel HWP core behavior checks passed")


if __name__ == "__main__":
    main()
