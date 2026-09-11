from types import SimpleNamespace
from unittest.mock import patch

import urwid

import auto_cpufreq.modules.system_monitor as monitor_module
from auto_cpufreq.modules.platform_profile import PlatformProfileSnapshot
from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType


def widget_text(widget) -> str:
    if isinstance(widget, urwid.AttrMap):
        widget = widget.original_widget
    return widget.get_text()[0]


def make_report():
    return SimpleNamespace(
        distro_name="TestOS",
        distro_ver="1",
        kernel_version="test-kernel",
        processor_model="Test CPU",
        total_core=4,
        arch="x86_64",
        cpu_driver="intel_pstate",
        cpu_max_freq=4500.0,
        cpu_min_freq=400.0,
        cores_info=[],
        cpu_fan_speed=None,
        battery_info=None,
        platform_profile=PlatformProfileSnapshot(),
        current_gov="powersave",
        current_epp="balance_performance",
        current_epb="balance_performance",
        current_hwp_dynamic_boost=True,
        is_turbo_on=(True, False),
        cpu_usage=3.0,
        load=0.25,
        avg_load=(0.25, 0.20, 0.15),
        cpu_avg_temp=42.0,
        intel_power=object(),
    )


def main() -> None:
    report = make_report()

    stats = SystemMonitor(type=ViewType.STATS)
    with patch.object(
        monitor_module.system_info,
        "generate_system_report",
        return_value=report,
    ) as generate:
        stats._collect_report()
    generate.assert_called_once_with(
        include_intel_power=True,
        sample_intel_energy=True,
    )

    for view_type in (ViewType.MONITOR, ViewType.LIVE):
        monitor = SystemMonitor(type=view_type)
        with patch.object(
            monitor_module.system_info,
            "generate_system_report",
            return_value=report,
        ) as generate:
            monitor._collect_report()
        generate.assert_called_once_with()

    with (
        patch.object(monitor_module.config, "has_config", return_value=False),
        patch.object(
            monitor_module,
            "format_intel_power_summary",
            return_value=["Intel sample line"],
        ) as formatter,
    ):
        stats.format_system_info(report)
        stats_text = [widget_text(widget) for widget in stats.right_content]
        assert "Intel Power" in stats_text
        assert "Intel sample line" in stats_text
        formatter.assert_called_once_with(report.intel_power)

    for view_type in (ViewType.MONITOR, ViewType.LIVE):
        monitor = SystemMonitor(type=view_type)
        with (
            patch.object(monitor_module.config, "has_config", return_value=False),
            patch.object(
                monitor_module,
                "format_intel_power_summary",
                return_value=["Intel sample line"],
            ) as formatter,
        ):
            monitor.format_system_info(report)
            text = [widget_text(widget) for widget in monitor.right_content]
            assert "Intel Power" not in text
            assert "Intel sample line" not in text
            formatter.assert_not_called()

    print("task5 stats-only Intel telemetry checks passed")


if __name__ == "__main__":
    main()
