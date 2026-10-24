"""GTK/Stats parity using the same synthetic reports without a GUI display.

Test the rendered *meaning*; GTK owns widgets, Urwid owns terminal layout.
No daemon, hardware sampling, privileged calls or X server are required.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import urwid

from auto_cpufreq.gui import report_view
from auto_cpufreq.modules.report_presentation import (
    configuration_file_text, offline_cpus_text,
    present_platform_profile, present_report,
)
from auto_cpufreq.modules.platform_profile import (
    PlatformProfileDevice, PlatformProfileSnapshot,
)
from auto_cpufreq.modules.system_info import (
    BatteryInfo, CoreInfo, SystemReport,
    format_platform_profile_summary,
)
from auto_cpufreq.modules import system_monitor


def sample_report(*, battery=None, temperatures=(40.0, 41.0),
                  platform=None, offline=()):
    if platform is None:
        platform = PlatformProfileSnapshot()
    return SystemReport(
        distro_name="SyntheticOS", distro_ver="1",
        arch="x86_64", processor_model="Test CPU", total_core=2,
        kernel_version="test", current_gov="powersave",
        current_epp="balance_performance", current_epb=None,
        current_hwp_dynamic_boost=None, cpu_driver="intel_pstate",
        cpu_fan_speed=2300, cpu_usage=10.0, cpu_max_freq=4500.0,
        cpu_min_freq=400.0, load=0.5, avg_load=(0.5, 0.6, 0.7),
        cores_info=[
            CoreInfo(id=i, usage=10.0, temperature=temp, frequency=800.0)
            for i, temp in enumerate(temperatures)
        ],
        battery_info=battery, is_turbo_on=(True, False),
        cpu_avg_temp=None, offline_cpus=offline,
        platform_profile=platform,
    )


def make_gtk_double():
    def labels(keys):
        return {key: Mock() for key in keys}
    return SimpleNamespace(
        system_values=labels(("distro", "kernel", "processor", "cores",
                              "architecture", "driver")),
        platform_values=labels(("interface", "current", "available", "provider")),
        platform_names=labels(("available", "provider")),
        power_values=labels(("governor", "epp", "epb", "hwp", "turbo")),
        battery_values=labels(("status", "charge", "ac", "start", "stop", "power")),
        battery_names=labels(("power",)),
        stats_values=labels(("usage", "load", "load_avg", "temp")),
        max_freq_value=Mock(), min_freq_value=Mock(),
        fan_label=Mock(), offline_label=Mock(),
        platform_grid=Mock(), battery_grid=Mock(), power_grid=Mock(),
        _refresh_core_rows=Mock(),
    )


def terminal_lines(report, *, config_exists=False):
    monitor = system_monitor.SystemMonitor(
        type=system_monitor.ViewType.STATS
    )
    with patch.object(
        system_monitor.config, "has_config", return_value=config_exists
    ):
        with patch.object(
            system_monitor.config, "path", "/tmp/synthetic.conf"
        ):
            monitor.format_system_info(report)

    def text(widget):
        base = widget.base_widget if isinstance(widget, urwid.AttrMap) else widget
        return base.text if isinstance(base, urwid.Text) else ""

    return [text(widget) for widget in
            list(monitor.left_content) + list(monitor.right_content)]


class FrontendReportParityTests(unittest.TestCase):
    def render_gtk(self, report):
        view = make_gtk_double()
        visibility = []
        with patch.object(report_view, "_set_row_visible") as visible:
            report_view.SystemReportView.apply_report(view, report)
            visibility = [
                (call.args[0], call.args[1], call.args[2])
                for call in visible.call_args_list
            ]
        return view, visibility

    def test_missing_battery_has_same_explicit_status(self):
        report = sample_report()
        expected = present_report(report).battery.status.text
        self.assertEqual(expected, "No battery detected")
        lines = terminal_lines(report)
        self.assertIn("Battery status: " + expected, lines)
        self.assertNotIn("Battery percentage: Unavailable", lines)
        view, visibility = self.render_gtk(report)
        view.battery_values["status"].set_text.assert_called_with(expected)
        self.assertEqual(
            [row for grid, row, shown in visibility
             if grid is view.battery_grid and not shown],
            [1, 2, 3, 4, 5],
        )

    def test_battery_rows_return_after_device_becomes_available(self):
        battery = BatteryInfo(
            is_charging=False, is_ac_plugged=True,
            charging_start_threshold=None, charging_stop_threshold=80,
            battery_level=60, power_watts=0.0,
        )
        report = sample_report(battery=battery)
        expected = present_report(report).battery.status.text
        self.assertIn("Battery status: " + expected, terminal_lines(report))
        view, visibility = self.render_gtk(report)
        self.assertEqual(
            [row for grid, row, shown in visibility
             if grid is view.battery_grid and shown],
            [1, 2, 3, 4, 5],
        )
        view.battery_values["status"].set_text.assert_called_with(expected)

    def test_missing_average_temperature_is_visible_in_both(self):
        report = sample_report(temperatures=(0.0, 0.0))
        expected = present_report(report).average_temperature.text
        self.assertEqual(expected, "Unavailable")
        self.assertIn(
            "Average CPU temperature: " + expected,
            terminal_lines(report),
        )
        view, _ = self.render_gtk(report)
        view.stats_values["temp"].set_text.assert_called_with(expected)

    def test_offline_cpu_list_shown_only_when_nonempty(self):
        for offline in ((), (2, 5)):
            with self.subTest(offline=offline):
                report = sample_report(offline=offline)
                expected = offline_cpus_text(offline)
                lines = terminal_lines(report)
                view, _ = self.render_gtk(report)
                if expected:
                    self.assertIn(expected, lines)
                    view.offline_label.set_text.assert_called_with(expected)
                    view.offline_label.show.assert_called_once_with()
                else:
                    self.assertFalse(any("Disabled CPUs:" in line for line in lines))
                    view.offline_label.hide.assert_called_once_with()

    def test_config_path_labels_share_same_semantics(self):
        for found in (False, True):
            with self.subTest(found=found):
                expected = configuration_file_text(
                    "/tmp/synthetic.conf", found
                )
                self.assertIn(
                    expected,
                    terminal_lines(sample_report(), config_exists=found),
                )

    def test_platform_no_interface_and_unknown_choices(self):
        for platform in (
            PlatformProfileSnapshot(),
            PlatformProfileSnapshot(
                interface="modern", current=None, writable=False,
                choices_known=False,
            ),
        ):
            with self.subTest(interface=platform.interface):
                report = sample_report(platform=platform)
                rendered = present_platform_profile(platform)
                lines = terminal_lines(report)
                view, _ = self.render_gtk(report)
                self.assertIn("Interface: " + rendered.interface, lines)
                view.platform_values["interface"].set_text.assert_called_with(
                    rendered.interface
                )
                if rendered.detected:
                    self.assertIn(
                        rendered.available_label + ": " + rendered.available,
                        lines,
                    )
                    view.platform_values["available"].set_text.assert_called_with(
                        rendered.available
                    )

    def test_multiple_profile_providers_match_gtk_and_stats(self):
        devices = (
            PlatformProfileDevice(
                provider="vendor-a", profile="performance",
                choices=("balanced", "performance"),
                profile_path=Path("/tmp/vendor-a"), choices_known=True,
            ),
            PlatformProfileDevice(
                provider="vendor-b", profile=None,
                choices=("balanced", "performance"),
                profile_path=Path("/tmp/vendor-b"), choices_known=True,
            ),
        )
        profile = PlatformProfileSnapshot(
            devices=devices, interface="modern", current=None,
            available_profiles=("balanced", "performance"),
            choices_known=True, writable=True,
        )
        report = sample_report(platform=profile)
        rendered = present_platform_profile(profile)
        summary = format_platform_profile_summary(profile)
        self.assertEqual(rendered.provider_label, "Providers")
        self.assertIn("vendor-a: performance; vendor-b: Unknown", summary[-1])
        lines = terminal_lines(report)
        self.assertIn(
            rendered.provider_label + ": " + rendered.provider, lines
        )
        view, visible = self.render_gtk(report)
        view.platform_values["provider"].set_text.assert_called_with(
            rendered.provider
        )
        self.assertIn(
            (view.platform_grid, 3, True), visible
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
