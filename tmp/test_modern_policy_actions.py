from auto_cpufreq.modules.intel_power import (
    CpuFreqPolicySnapshot,
    IntelPowerSnapshot,
    ReadResult,
    ReadStatus,
)
from auto_cpufreq.modules.policy import (
    LegacyPolicy,
    ModernIntelActions,
    ModernIntelHwpPolicy,
    PolicyActions,
    PowerSource,
    select_policy_backend,
)


def available(value):
    return ReadResult(ReadStatus.AVAILABLE, value)


def modern_snapshot():
    policy = CpuFreqPolicySnapshot(
        policy="policy0",
        related_cpus=available((0,)),
        scaling_driver=available("intel_pstate"),
        scaling_governor=available("powersave"),
        cpuinfo_min_freq_khz=available(400000),
        cpuinfo_max_freq_khz=available(4500000),
        scaling_min_freq_khz=available(400000),
        scaling_max_freq_khz=available(4500000),
        epp=available("balance_performance"),
        available_epp=available(("performance", "balance_performance", "balance_power", "power")),
    )
    return IntelPowerSnapshot(
        intel_pstate_status=available("active"),
        turbo_allowed=available(True),
        hwp_dynamic_boost=available(True),
        cpu_topology=(),
        cpufreq_policies=(policy,),
    )


def legacy_snapshot():
    snap = modern_snapshot()
    return IntelPowerSnapshot(
        intel_pstate_status=available("passive"),
        turbo_allowed=snap.turbo_allowed,
        hwp_dynamic_boost=snap.hwp_dynamic_boost,
        cpu_topology=snap.cpu_topology,
        cpufreq_policies=snap.cpufreq_policies,
    )


def main():
    legacy_calls = []
    modern_calls = []

    legacy_actions = PolicyActions(
        apply_charger=lambda: legacy_calls.append("apply-charger"),
        apply_battery=lambda: legacy_calls.append("apply-battery"),
        monitor_charger=lambda: legacy_calls.append("monitor-charger"),
        monitor_battery=lambda: legacy_calls.append("monitor-battery"),
    )
    modern_actions = ModernIntelActions(
        apply=lambda source: modern_calls.append(("apply", source)),
        monitor=lambda source: modern_calls.append(("monitor", source)),
    )

    modern = select_policy_backend(
        modern_snapshot(),
        legacy_actions,
        modern_actions=modern_actions,
    )
    assert isinstance(modern, ModernIntelHwpPolicy)
    assert modern.requires_periodic_tick is True
    modern.apply(PowerSource.CHARGER)
    modern.apply(PowerSource.BATTERY)
    modern.monitor(PowerSource.CHARGER)
    modern.monitor(PowerSource.BATTERY)
    assert modern_calls == [
        ("apply", PowerSource.CHARGER),
        ("apply", PowerSource.BATTERY),
        ("monitor", PowerSource.CHARGER),
        ("monitor", PowerSource.BATTERY),
    ]
    assert legacy_calls == []

    legacy = select_policy_backend(
        legacy_snapshot(),
        legacy_actions,
        modern_actions=modern_actions,
    )
    assert isinstance(legacy, LegacyPolicy)
    assert not isinstance(legacy, ModernIntelHwpPolicy)
    legacy.apply(PowerSource.CHARGER)
    legacy.monitor(PowerSource.BATTERY)
    assert legacy_calls == ["apply-charger", "monitor-battery"]

    # Keeping modern_actions optional preserves the Stage-2 safe fallback.
    fallback = select_policy_backend(modern_snapshot(), legacy_actions)
    assert isinstance(fallback, ModernIntelHwpPolicy)
    fallback.apply(PowerSource.CHARGER)
    fallback.monitor(PowerSource.BATTERY)
    assert legacy_calls[-2:] == ["apply-charger", "monitor-battery"]

    print("modern Intel action routing checks passed")


if __name__ == "__main__":
    main()
