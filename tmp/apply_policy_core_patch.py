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
        "from auto_cpufreq.modules.platform_profile import platform_profile\n",
        "from auto_cpufreq.modules.intel_power import IntelPowerDiscovery\n"
        "from auto_cpufreq.modules.platform_profile import platform_profile\n"
        "from auto_cpufreq.modules.policy import (\n"
        "    PolicyActions,\n"
        "    PowerSource,\n"
        "    select_policy_backend,\n"
        ")\n",
        "policy imports",
    )

    text = replace_once(
        text,
        "last_applied_config_section = None\n",
        "last_applied_config_section = None\n"
        "_policy_backend = None\n",
        "policy backend cache",
    )

    old_dispatch = '''def set_autofreq():
    """
    set cpufreq governor based if device is charging
    """
    print("\\n" + "-" * 28 + " CPU frequency scaling " + "-" * 28 + "\\n")

    # determine which power profile should be used
    if charging():
        print("Battery is: charging\\n")
        set_performance()
    else:
        print("Battery is: discharging\\n")
        set_powersave()

def mon_autofreq():
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
        mon_performance()
    else:
        print("Battery is: discharging\\n")
        get_current_gov()
        print(f'Suggesting use of "{AVAILABLE_GOVERNORS_SORTED[-1]}" governor')
        mon_powersave()
'''

    new_dispatch = '''def get_policy_backend():
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


def set_autofreq():
    """
    set cpufreq governor based if device is charging
    """
    print("\\n" + "-" * 28 + " CPU frequency scaling " + "-" * 28 + "\\n")

    # determine which power profile should be used
    if charging():
        print("Battery is: charging\\n")
        source = PowerSource.CHARGER
    else:
        print("Battery is: discharging\\n")
        source = PowerSource.BATTERY

    get_policy_backend().apply(source)


def mon_autofreq():
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

    text = replace_once(text, old_dispatch, new_dispatch, "policy dispatchers")
    CORE.write_text(text)


if __name__ == "__main__":
    main()
