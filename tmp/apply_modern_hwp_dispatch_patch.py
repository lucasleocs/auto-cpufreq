from pathlib import Path


CORE = Path("auto_cpufreq/core.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = CORE.read_text()

    text = replace_once(
        text,
        "from auto_cpufreq.modules.policy import (\n"
        "    PolicyActions,\n"
        "    PowerSource,\n"
        "    select_policy_backend,\n"
        ")\n",
        "from auto_cpufreq.modules.policy import (\n"
        "    ModernIntelActions,\n"
        "    PolicyActions,\n"
        "    PowerSource,\n"
        "    select_policy_backend,\n"
        ")\n",
        "Modern Intel actions import",
    )

    marker = "\ndef set_powersave():\n"
    if "def mon_modern_intel_hwp(source):" not in text:
        if text.count(marker) != 1:
            raise RuntimeError("modern monitor: expected one set_powersave marker")
        monitor = r'''
def mon_modern_intel_hwp(source):
    """Report the policy an eligible Intel HWP system would use."""
    profile = source.value
    conf = config.get_config()
    gov = get_modern_intel_governor(conf, profile)

    print(f'Suggesting use of "{gov}" governor (Modern Intel HWP)')

    if gov == "performance":
        print('Suggested EPP: "performance" (controlled by performance governor)')
    else:
        epp = (
            conf[profile]["energy_performance_preference"]
            if conf.has_option(profile, "energy_performance_preference")
            else "balance_performance" if profile == "charger" else "balance_power"
        )
        print(f'Suggested EPP: "{epp}"')

    target_dynboost = get_hwp_dynamic_boost_target(conf, profile)
    if target_dynboost is not None:
        print("HWP dynamic boost:", "on" if target_dynboost else "off")

    turbo_target, turbo_mode = get_modern_intel_turbo_target(conf, profile)
    if turbo_mode == "auto":
        print("Suggested hardware-managed turbo boost: allowed")
    else:
        print("Suggested turbo boost:", "on" if turbo_target else "off")
    get_turbo()
    footer()


def mon_legacy_performance():
    print(f'Suggesting use of "{AVAILABLE_GOVERNORS_SORTED[0]}" governor')
    mon_performance()


def mon_legacy_powersave():
    print(f'Suggesting use of "{AVAILABLE_GOVERNORS_SORTED[-1]}" governor')
    mon_powersave()
'''
        text = text.replace(marker, monitor + marker, 1)

    old_backend = '''def get_policy_backend():
    global _policy_backend
    if _policy_backend is None:
        _policy_backend = select_policy_backend(
            IntelPowerDiscovery().snapshot(),
            PolicyActions(
                apply_charger=set_performance,
                apply_battery=set_powersave,
                monitor_charger=mon_performance,
                monitor_battery=mon_powersave,
            ),
        )
    return _policy_backend
'''
    new_backend = '''def get_policy_backend():
    global _policy_backend
    if _policy_backend is None:
        _policy_backend = select_policy_backend(
            IntelPowerDiscovery().snapshot(),
            PolicyActions(
                apply_charger=set_performance,
                apply_battery=set_powersave,
                monitor_charger=mon_legacy_performance,
                monitor_battery=mon_legacy_powersave,
            ),
            modern_actions=ModernIntelActions(
                apply=set_modern_intel_hwp,
                monitor=mon_modern_intel_hwp,
            ),
        )
    return _policy_backend
'''
    text = replace_once(text, old_backend, new_backend, "policy backend wiring")

    old_monitor = '''def mon_autofreq():
    """
    make cpufreq suggestions
    :return:
    """
    print("\\n" + "-" * 28 + " CPU frequency scaling " + "-" * 28 + "\\n")

    # determine which governor should be used
    if charging():
        print("Battery is: charging\\n")
        get_current_gov()
        print(f'Suggesting use of "{AVAILABLE_GOVERNORS_SORTED[0]}" governor')
        source = PowerSource.CHARGER
    else:
        print("Battery is: discharging\\n")
        get_current_gov()
        print(f'Suggesting use of "{AVAILABLE_GOVERNORS_SORTED[-1]}" governor')
        source = PowerSource.BATTERY

    get_policy_backend().monitor(source)
'''
    new_monitor = '''def mon_autofreq():
    """
    make cpufreq suggestions
    :return:
    """
    print("\\n" + "-" * 28 + " CPU frequency scaling " + "-" * 28 + "\\n")

    # determine which governor should be used
    if charging():
        print("Battery is: charging\\n")
        source = PowerSource.CHARGER
    else:
        print("Battery is: discharging\\n")
        source = PowerSource.BATTERY

    get_current_gov()
    get_policy_backend().monitor(source)
'''
    text = replace_once(text, old_monitor, new_monitor, "monitor dispatcher")

    CORE.write_text(text)


if __name__ == "__main__":
    main()
