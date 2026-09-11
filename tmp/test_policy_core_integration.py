from contextlib import redirect_stdout
from io import StringIO

import auto_cpufreq.core as core
from auto_cpufreq.modules.intel_power import (
    CpuFreqPolicySnapshot,
    IntelPowerSnapshot,
    ReadResult,
    ReadStatus,
)
from auto_cpufreq.modules.policy import ModernIntelHwpPolicy, PowerSource


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


class FakeDiscovery:
    calls = 0

    def snapshot(self):
        type(self).calls += 1
        return modern_snapshot()


class FakeBackend:
    def __init__(self):
        self.calls = []

    def apply(self, source):
        self.calls.append(("apply", source))

    def monitor(self, source):
        self.calls.append(("monitor", source))


def main():
    original_discovery = core.IntelPowerDiscovery
    original_set_performance = core.set_performance
    original_set_powersave = core.set_powersave
    original_mon_performance = core.mon_performance
    original_mon_powersave = core.mon_powersave
    original_charging = core.charging
    original_backend = core._policy_backend
    original_get_policy_backend = core.get_policy_backend
    original_governors = core.AVAILABLE_GOVERNORS_SORTED

    try:
        legacy_calls = []
        core.IntelPowerDiscovery = FakeDiscovery
        core.set_performance = lambda: legacy_calls.append("set-performance")
        core.set_powersave = lambda: legacy_calls.append("set-powersave")
        core.mon_performance = lambda: legacy_calls.append("mon-performance")
        core.mon_powersave = lambda: legacy_calls.append("mon-powersave")
        core.AVAILABLE_GOVERNORS_SORTED = ("performance", "powersave")
        core._policy_backend = None
        FakeDiscovery.calls = 0

        backend = core.get_policy_backend()
        assert isinstance(backend, ModernIntelHwpPolicy)
        assert core.get_policy_backend() is backend
        assert FakeDiscovery.calls == 1

        backend.apply(PowerSource.CHARGER)
        backend.apply(PowerSource.BATTERY)
        backend.monitor(PowerSource.CHARGER)
        backend.monitor(PowerSource.BATTERY)
        assert legacy_calls == [
            "set-performance",
            "set-powersave",
            "mon-performance",
            "mon-powersave",
        ]

        fake = FakeBackend()
        core.get_policy_backend = lambda: fake

        core.charging = lambda: True
        with redirect_stdout(StringIO()) as output:
            core.set_autofreq()
        assert "Battery is: charging" in output.getvalue()
        assert fake.calls == [("apply", PowerSource.CHARGER)]

        fake.calls.clear()
        core.charging = lambda: False
        with redirect_stdout(StringIO()) as output:
            core.set_autofreq()
        assert "Battery is: discharging" in output.getvalue()
        assert fake.calls == [("apply", PowerSource.BATTERY)]

        fake.calls.clear()
        core.charging = lambda: True
        with redirect_stdout(StringIO()) as output:
            core.mon_autofreq()
        assert "Battery is: charging" in output.getvalue()
        assert 'Suggesting use of "performance" governor' in output.getvalue()
        assert fake.calls == [("monitor", PowerSource.CHARGER)]

        fake.calls.clear()
        core.charging = lambda: False
        with redirect_stdout(StringIO()) as output:
            core.mon_autofreq()
        assert "Battery is: discharging" in output.getvalue()
        assert 'Suggesting use of "powersave" governor' in output.getvalue()
        assert fake.calls == [("monitor", PowerSource.BATTERY)]
    finally:
        core.IntelPowerDiscovery = original_discovery
        core.set_performance = original_set_performance
        core.set_powersave = original_set_powersave
        core.mon_performance = original_mon_performance
        core.mon_powersave = original_mon_powersave
        core.charging = original_charging
        core._policy_backend = original_backend
        core.get_policy_backend = original_get_policy_backend
        core.AVAILABLE_GOVERNORS_SORTED = original_governors

    print("policy dispatcher integration checks passed")


if __name__ == "__main__":
    main()
