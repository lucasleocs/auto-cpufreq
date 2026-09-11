from pathlib import Path


CORE = Path("auto_cpufreq/core.py")


def main() -> None:
    text = CORE.read_text()
    marker = "\ndef set_powersave():\n"
    if "def set_modern_intel_hwp(source):" in text:
        return
    if text.count(marker) != 1:
        raise RuntimeError("expected exactly one set_powersave marker")

    implementation = r'''
def get_modern_intel_governor(conf, profile):
    override = get_override()
    if override in ("powersave", "performance"):
        return override
    if conf.has_option(profile, "governor"):
        return conf[profile]["governor"]
    return "powersave"


def get_modern_intel_turbo_target(conf, profile):
    mode = conf[profile]["turbo"] if conf.has_option(profile, "turbo") else "auto"
    override = get_turbo_override()
    if override != "auto":
        mode = override
    return mode != "never", mode


def set_modern_intel_hwp(source):
    """Apply the slow policy envelope for an eligible Intel HWP system."""
    profile = source.value
    conf = config.get_config()
    governor_override = get_override()
    gov = get_modern_intel_governor(conf, profile)

    print(f'Setting Modern Intel HWP policy for [{profile}]')
    print(f'Setting to use: "{gov}" governor')
    if governor_override != "default":
        print("Warning: governor overwritten using `--force` flag.")

    try:
        result = run(
            ["cpufreqctl.auto-cpufreq", "--governor", f"--set={gov}"]
        )
    except OSError as error:
        print(f'Failed to set "{gov}" governor: {error}')
        footer()
        return
    if result.returncode != 0:
        print(f'Failed to set "{gov}" governor')
        footer()
        return

    target_dynboost = get_hwp_dynamic_boost_target(conf, profile)
    if target_dynboost is not None:
        set_hwp_dynamic_boost(target_dynboost)

    if gov == "performance":
        print(
            'Not setting EPP (intel_pstate performance governor controls EPP '
            'as "performance")'
        )
    else:
        epp = (
            conf[profile]["energy_performance_preference"]
            if conf.has_option(profile, "energy_performance_preference")
            else "balance_performance" if profile == "charger" else "balance_power"
        )
        set_energy_perf_preference(epp)

    set_energy_perf_bias(conf, profile)
    set_platform_profile(conf, profile)

    global last_applied_config_section
    last_applied_config_section = profile

    turbo_target, turbo_mode = get_modern_intel_turbo_target(conf, profile)
    if turbo_mode == "always":
        print("Configuration file enforces turbo boost")
    elif turbo_mode == "never":
        print("Configuration file disables turbo boost")
    else:
        print("Modern Intel HWP uses hardware-managed turbo boost")
    set_turbo(turbo_target)

    set_frequencies(profile)
    footer()
'''

    CORE.write_text(text.replace(marker, implementation + marker, 1))


if __name__ == "__main__":
    main()
