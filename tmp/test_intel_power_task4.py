from unittest.mock import patch

import auto_cpufreq.modules.system_info as system_info_module
from auto_cpufreq.bin import auto_cpufreq as cli_module
from auto_cpufreq.modules.intel_power import (
    CpuFreqPolicySnapshot,
    CpuTopologySnapshot,
    IntelPowerSnapshot,
    PowerSample,
    PowercapConstraintSnapshot,
    PowercapZoneSnapshot,
    ReadResult,
    ReadStatus,
    ThermalThrottleSnapshot,
)


def available(value):
    return ReadResult(ReadStatus.AVAILABLE, value)


def missing():
    return ReadResult(ReadStatus.MISSING)


def make_snapshot() -> IntelPowerSnapshot:
    constraint = PowercapConstraintSnapshot(
        index=0,
        name=available("long_term"),
        power_limit_uw=available(15_000_000),
        time_window_us=available(28_000_000),
        min_power_uw=missing(),
        max_power_uw=missing(),
        min_time_window_us=missing(),
        max_time_window_us=missing(),
    )
    zone = PowercapZoneSnapshot(
        zone_id="intel-rapl/intel-rapl:0",
        control_type="intel-rapl",
        sysfs_name="intel-rapl:0",
        name=available("package-0"),
        energy_uj=available(90_000_000),
        max_energy_range_uj=available(100_000_000),
        constraints=(constraint,),
        power_sample=PowerSample(
            delta_energy_uj=20_000_000,
            elapsed_ns=1_000_000_000,
            average_power_w=20.0,
        ),
    )
    return IntelPowerSnapshot(
        intel_pstate_status=available("active"),
        turbo_allowed=available(True),
        hwp_dynamic_boost=available(True),
        cpu_topology=(
            CpuTopologySnapshot(
                cpu_id=0,
                physical_package_id=available(0),
                core_id=available(0),
                thread_siblings=available((0, 4)),
            ),
        ),
        cpufreq_policies=(
            CpuFreqPolicySnapshot(
                policy="policy0",
                related_cpus=available((0, 2)),
                scaling_driver=available("intel_pstate"),
                scaling_governor=available("powersave"),
                cpuinfo_min_freq_khz=available(400_000),
                cpuinfo_max_freq_khz=available(4_500_000),
                scaling_min_freq_khz=available(400_000),
                scaling_max_freq_khz=available(4_500_000),
                epp=available("balance_performance"),
                available_epp=available(
                    ("performance", "balance_performance", "balance_power", "power")
                ),
            ),
        ),
        powercap_zones=(zone,),
        thermal_packages=(
            ThermalThrottleSnapshot(
                package_id=0,
                representative_cpu=0,
                throttle_count=available(12),
                total_time_ms=available(300),
                max_time_ms=available(40),
            ),
        ),
    )


def main() -> None:
    snapshot = make_snapshot()

    # Default report collection must remain free of Intel Powercap/thermal work.
    with patch.object(system_info_module.intel_power, "snapshot", return_value=snapshot) as probe:
        normal_report = system_info_module.system_info.generate_system_report()
        probe.assert_not_called()
        assert normal_report.intel_power is None

        enhanced_report = system_info_module.system_info.generate_system_report(
            include_intel_power=True,
            sample_intel_energy=False,
        )
        probe.assert_called_once_with(sample_energy=False)
        assert enhanced_report.intel_power is snapshot

    normal_text = system_info_module.format_system_report(normal_report)
    assert "Intel Power Diagnostics" not in normal_text

    enhanced_text = system_info_module.format_system_report(enhanced_report)
    assert "Intel Power Diagnostics" in enhanced_text
    assert "intel_pstate: active" in enhanced_text
    assert "Turbo permission: Allowed" in enhanced_text
    assert "HWP Dynamic Boost: On" in enhanced_text
    assert "policy0" in enhanced_text
    assert "package-0" in enhanced_text
    assert "Control type: intel-rapl" in enhanced_text
    assert "Energy: 90.000 J" in enhanced_text
    assert "Average power: 20.000 W" in enhanced_text
    assert "long_term: 15.000 W, 28.000 s" in enhanced_text
    assert "Package 0 thermal throttling" in enhanced_text

    # The command callback also checks sys.argv itself, so reproduce the real
    # invocation contract instead of relying only on Click's CliRunner argv.
    sentinel_report = object()
    with (
        patch.object(cli_module.sys, "argv", ["auto-cpufreq", "--debug"]),
        patch.object(cli_module, "root_check"),
        patch.object(cli_module, "battery_get_thresholds"),
        patch.object(cli_module, "cpufreqctl"),
        patch.object(cli_module, "footer"),
        patch.object(cli_module, "app_version"),
        patch.object(cli_module, "python_info"),
        patch.object(cli_module, "device_info"),
        patch.object(cli_module, "app_res_use"),
        patch.object(cli_module, "get_load"),
        patch.object(cli_module, "get_current_gov"),
        patch.object(cli_module, "get_turbo"),
        patch.object(cli_module, "charging", return_value=False),
        patch.object(
            cli_module,
            "find_config_file",
            return_value="/tmp/nonexistent-auto-cpufreq.conf",
        ),
        patch.object(cli_module.conf, "set_path"),
        patch.object(cli_module.conf, "has_config", return_value=False),
        patch.object(
            cli_module.system_info,
            "generate_system_report",
            return_value=sentinel_report,
        ) as generate,
        patch.object(cli_module, "print_system_report") as printer,
    ):
        cli_module.main.callback(
            monitor=False,
            live=False,
            daemon=False,
            install=False,
            update=None,
            remove=False,
            force=None,
            turbo=None,
            config=None,
            stats=False,
            pp=False,
            get_state=False,
            bluetooth_boot_off=False,
            bluetooth_boot_on=False,
            debug=True,
            version=False,
            donate=False,
        )

    generate.assert_called_once_with(
        include_intel_power=True,
        sample_intel_energy=False,
    )
    printer.assert_called_once_with(sentinel_report)

    print("task4 opt-in diagnostics checks passed")


if __name__ == "__main__":
    main()
