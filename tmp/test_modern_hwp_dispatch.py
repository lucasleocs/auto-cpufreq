from configparser import ConfigParser
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


def snapshot(status="active"):
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
        intel_pstate_status=available(status),
        turbo_allowed=available(True),
        hwp_dynamic_boost=available(True),
        cpu_topology=(),
        cpufreq_policies=(policy,),
    )


class FakeModernDiscovery:
    def snapshot(self):
        return snapshot("active")


class FakeLegacyDiscovery:
    def snapshot(self):
        return snapshot("passive")


def main():
    assert hasattr(core, "mon_modern_intel_hwp")

    originals = {
        "discovery": core.IntelPowerDiscovery,
        "set_modern": core.set_modern_intel_hwp,
        "mon_modern": core.mon_modern_intel_hwp,
        "set_performance": core.set_performance,
        "set_powersave": core.set_powersave,
        "mon_performance": core.mon_performance,
        "mon_powersave": core.mon_powersave,
        "charging": core.charging,
        "get_current_gov": core.get_current_gov,
        "governors": core.AVAILABLE_GOVERNORS_SORTED,
        "backend": core._policy_backend,
        "get_config": core.config.get_config,
        "dynboost_target": core.get_hwp_dynamic_boost_target,
        "get_turbo": core.get_turbo,
        "get_load": core.get_load,
    }

    try:
        calls = []
        core.IntelPowerDiscovery = FakeModernDiscovery
        core.set_modern_intel_hwp = lambda source: calls.append(("modern-apply", source))
        core.mon_modern_intel_hwp = lambda source: calls.append(("modern-monitor", source))
        core.set_performance = lambda: calls.append(("legacy-apply-charger",))
        core.set_powersave = lambda: calls.append(("legacy-apply-battery",))
        core.mon_performance = lambda: calls.append(("legacy-monitor-charger",))
        core.mon_powersave = lambda: calls.append(("legacy-monitor-battery",))
        core.AVAILABLE_GOVERNORS_SORTED = ("performance", "powersave")
        core._policy_backend = None

        backend = core.get_policy_backend()
        assert isinstance(backend, ModernIntelHwpPolicy)
        backend.apply(PowerSource.CHARGER)
        backend.monitor(PowerSource.BATTERY)
        assert calls == [
            ("modern-apply", PowerSource.CHARGER),
            ("modern-monitor", PowerSource.BATTERY),
        ]

        calls.clear()
        core.IntelPowerDiscovery = FakeLegacyDiscovery
        core._policy_backend = None
        legacy = core.get_policy_backend()
        legacy.apply(PowerSource.CHARGER)
        legacy.monitor(PowerSource.BATTERY)
        assert calls == [
            ("legacy-apply-charger",),
            ("legacy-monitor-battery",),
        ]

        # Legacy monitor keeps the original suggestion before the old handler.
        calls.clear()
        core.IntelPowerDiscovery = FakeLegacyDiscovery
        core._policy_backend = None
        core.charging = lambda: True
        core.get_current_gov = lambda: calls.append(("current-governor",))
        with redirect_stdout(StringIO()) as output:
            core.mon_autofreq()
        text = output.getvalue()
        assert 'Suggesting use of "performance" governor' in text
        assert calls == [
            ("current-governor",),
            ("legacy-monitor-charger",),
        ]

        # Modern monitor owns its own suggestion; the generic performance
        # suggestion must not leak from mon_autofreq().
        calls.clear()
        core.IntelPowerDiscovery = FakeModernDiscovery
        core._policy_backend = None
        core.mon_modern_intel_hwp = lambda source: (
            print('Suggesting use of "powersave" governor (Modern Intel HWP)')
        )
        with redirect_stdout(StringIO()) as output:
            core.mon_autofreq()
        text = output.getvalue()
        assert 'Suggesting use of "performance" governor' not in text
        assert 'Suggesting use of "powersave" governor (Modern Intel HWP)' in text

        # The concrete modern monitor is read-only and does not sample load.
        conf = ConfigParser()
        conf["charger"] = {}
        conf["battery"] = {}
        core.config.get_config = lambda: conf
        core.get_hwp_dynamic_boost_target = lambda _conf, profile: profile == "charger"
        core.get_turbo = lambda: print("Currently turbo boost is: on")
        core.get_load = lambda: (_ for _ in ()).throw(
            AssertionError("Modern Intel monitor must not sample CPU load")
        )
        core.AVAILABLE_GOVERNORS_SORTED = ("performance", "powersave")

        # Restore the real function for this direct behavior check.
        core.mon_modern_intel_hwp = originals["mon_modern"]
        with redirect_stdout(StringIO()) as output:
            core.mon_modern_intel_hwp(PowerSource.CHARGER)
        text = output.getvalue()
        assert 'Suggesting use of "powersave" governor (Modern Intel HWP)' in text
        assert 'Suggested EPP: "balance_performance"' in text
        assert "HWP dynamic boost: on" in text
        assert "hardware-managed turbo boost" in text
    finally:
        core.IntelPowerDiscovery = originals["discovery"]
        core.set_modern_intel_hwp = originals["set_modern"]
        core.mon_modern_intel_hwp = originals["mon_modern"]
        core.set_performance = originals["set_performance"]
        core.set_powersave = originals["set_powersave"]
        core.mon_performance = originals["mon_performance"]
        core.mon_powersave = originals["mon_powersave"]
        core.charging = originals["charging"]
        core.get_current_gov = originals["get_current_gov"]
        core.AVAILABLE_GOVERNORS_SORTED = originals["governors"]
        core._policy_backend = originals["backend"]
        core.config.get_config = originals["get_config"]
        core.get_hwp_dynamic_boost_target = originals["dynboost_target"]
        core.get_turbo = originals["get_turbo"]
        core.get_load = originals["get_load"]

    print("Modern Intel dispatcher behavior checks passed")


if __name__ == "__main__":
    main()
