from auto_cpufreq.modules.intel_power import (
    CpuFreqPolicySnapshot,
    IntelPowerSnapshot,
    ReadResult,
    ReadStatus,
)
from auto_cpufreq.modules.policy import (
    LegacyPolicy,
    ModernIntelHwpPolicy,
    PolicyActions,
    PowerSource,
    modern_intel_hwp_eligible,
    select_policy_backend,
)


def available(value):
    return ReadResult(ReadStatus.AVAILABLE, value)


def missing():
    return ReadResult(ReadStatus.MISSING)


def policy_snapshot(*, status="active", driver="intel_pstate", epp="balance_performance"):
    policy = CpuFreqPolicySnapshot(
        policy="policy0",
        related_cpus=available((0,)),
        scaling_driver=available(driver),
        scaling_governor=available("powersave"),
        cpuinfo_min_freq_khz=available(400000),
        cpuinfo_max_freq_khz=available(4500000),
        scaling_min_freq_khz=available(400000),
        scaling_max_freq_khz=available(4500000),
        epp=available(epp) if epp is not None else missing(),
        available_epp=available(("performance", "balance_performance", "balance_power", "power")),
    )
    return IntelPowerSnapshot(
        intel_pstate_status=available(status),
        turbo_allowed=available(True),
        hwp_dynamic_boost=available(True),
        cpu_topology=(),
        cpufreq_policies=(policy,),
    )


def main():
    modern = policy_snapshot()
    assert modern_intel_hwp_eligible(modern) is True

    assert modern_intel_hwp_eligible(policy_snapshot(status="passive")) is False
    assert modern_intel_hwp_eligible(policy_snapshot(driver="acpi-cpufreq")) is False
    assert modern_intel_hwp_eligible(policy_snapshot(epp=None)) is False
    assert modern_intel_hwp_eligible(
        IntelPowerSnapshot(
            intel_pstate_status=available("active"),
            turbo_allowed=available(True),
            hwp_dynamic_boost=available(True),
            cpu_topology=(),
            cpufreq_policies=(),
        )
    ) is False

    calls = []
    actions = PolicyActions(
        apply_charger=lambda: calls.append("apply-charger"),
        apply_battery=lambda: calls.append("apply-battery"),
        monitor_charger=lambda: calls.append("monitor-charger"),
        monitor_battery=lambda: calls.append("monitor-battery"),
    )

    backend = select_policy_backend(modern, actions)
    assert isinstance(backend, ModernIntelHwpPolicy)
    assert backend.requires_periodic_tick is True
    backend.apply(PowerSource.CHARGER)
    backend.apply(PowerSource.BATTERY)
    backend.monitor(PowerSource.CHARGER)
    backend.monitor(PowerSource.BATTERY)
    assert calls == [
        "apply-charger",
        "apply-battery",
        "monitor-charger",
        "monitor-battery",
    ]

    calls.clear()
    legacy = select_policy_backend(policy_snapshot(status="passive"), actions)
    assert isinstance(legacy, LegacyPolicy)
    assert not isinstance(legacy, ModernIntelHwpPolicy)
    assert legacy.requires_periodic_tick is True
    legacy.apply(PowerSource.CHARGER)
    legacy.apply(PowerSource.BATTERY)
    assert calls == ["apply-charger", "apply-battery"]

    print("policy backend contract checks passed")


if __name__ == "__main__":
    main()
