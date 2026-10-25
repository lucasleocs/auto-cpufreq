"""GTK/Stats parity using the same synthetic reports without a GUI display.

Test the rendered *meaning*; GTK owns widgets, Urwid owns terminal layout.
No daemon, hardware sampling, privileged calls or X server are required.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import urwid

from auto_cpufreq.gui import report_view
from auto_cpufreq.modules.report_presentation import (
    configuration_file_text, offline_cpus_text, frequency_limit_text,
    present_platform_profile, present_report,
)
from auto_cpufreq.modules.platform_profile import (
    PlatformProfileDevice, PlatformProfileSnapshot,
)
from auto_cpufreq.modules.system_info import (
    BatteryInfo, CoreInfo, SystemInfo, SystemReport,
    format_platform_profile_choices,
    format_platform_profile_summary, format_system_report,
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
        battery_values=labels(("status", "charge", "ac", "start", "stop", "power", "limit")),
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

    def test_governor_unavailable_has_identical_label(self):
        report = replace(sample_report(), current_gov=None)
        expected = present_report(report).governor.text
        self.assertEqual(expected, "Unavailable")
        self.assertIn("Governor: " + expected, terminal_lines(report))
        view, _ = self.render_gtk(report)
        view.power_values["governor"].set_text.assert_called_with(expected)

    def test_frequency_limits_are_equivalent_for_known_and_missing_values(self):
        for maximum, minimum in (
            (4500.0, 400.0), (None, 400.0),
            (4500.0, None), (None, None), (0.0, 0.0),
        ):
            with self.subTest(maximum=maximum, minimum=minimum):
                report = replace(
                    sample_report(), cpu_max_freq=maximum,
                    cpu_min_freq=minimum,
                )
                max_text = frequency_limit_text(maximum)
                min_text = frequency_limit_text(minimum)
                self.assertIn(
                    "CPU max frequency: " + max_text,
                    terminal_lines(report),
                )
                self.assertIn(
                    "CPU min frequency: " + min_text,
                    terminal_lines(report),
                )
                view, _ = self.render_gtk(report)
                view.max_freq_value.set_text.assert_called_with(max_text)
                view.min_freq_value.set_text.assert_called_with(min_text)

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
            [1, 2, 3, 4, 5, 6],
        )

    def test_no_battery_real_collection_path_keeps_ac_and_ui_consistent(self):
        # Exercise SystemInfo's real battery_info() branch, not a synthetic
        # None that the normal collector never returns.
        with patch.object(SystemInfo, "get_battery_path", return_value=None):
            battery = SystemInfo.battery_info()

        self.assertIsInstance(battery, BatteryInfo)
        self.assertIs(battery.is_present, False)
        self.assertIs(battery.is_ac_plugged, True)
        self.assertIsNone(battery.battery_level)

        report = sample_report(battery=battery)
        self.assertEqual(
            present_report(report).battery.status.text, "No battery detected"
        )
        lines = terminal_lines(report)
        self.assertIn("Battery status: No battery detected", lines)
        self.assertNotIn("Battery percentage: Unavailable", lines)
        view, visible = self.render_gtk(report)
        view.battery_values["status"].set_text.assert_called_with(
            "No battery detected"
        )
        self.assertEqual(
            [row for grid, row, shown in visible
             if grid is view.battery_grid and not shown],
            [1, 2, 3, 4, 5, 6],
        )

    def test_detected_battery_keeps_charge_status_and_all_rows(self):
        # Mock sysfs reads while exercising the production collector branch.
        with patch.object(
            SystemInfo, "get_battery_path",
            return_value="/synthetic/BAT0",
        ):
            with patch.object(
                SystemInfo, "read_file",
                side_effect=lambda name: (
                    "Charging" if name.endswith("/status")
                    else "60" if name.endswith("/capacity")
                    else None
                ),
            ):
                with patch.object(
                    SystemInfo, "external_power_state", return_value=True
                ):
                    with patch.object(
                        SystemInfo, "_battery_power_watts", return_value=5.0
                    ):
                        battery = SystemInfo.battery_info()

        self.assertIs(battery.is_present, True)
        self.assertIs(battery.is_ac_plugged, True)
        report = sample_report(battery=battery)
        self.assertIn("Battery status: Charging", terminal_lines(report))
        view, visible = self.render_gtk(report)
        view.battery_values["status"].set_text.assert_called_with("Charging")
        self.assertEqual(
            [row for grid, row, shown in visible
             if grid is view.battery_grid and shown],
            [1, 2, 3, 4, 5, 6],
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
            [1, 2, 3, 4, 5, 6],
        )
        view.battery_values["status"].set_text.assert_called_with(expected)
        final = {row: shown for grid, row, shown in visibility
                 if grid is view.battery_grid}
        self.assertIs(final[6], False)

    def test_upower_charge_protection_row_is_visible_when_supported(self):
        battery = BatteryInfo(
            is_charging=False, is_ac_plugged=True,
            charging_start_threshold=None, charging_stop_threshold=80,
            battery_level=60, power_watts=0.0,
            charge_protection_supported=True,
            charge_protection_enabled=False,
        )
        view, visibility = self.render_gtk(sample_report(battery=battery))
        final = {row: shown for grid, row, shown in visibility
                 if grid is view.battery_grid}
        self.assertIs(final[6], True)
        view.battery_values["limit"].set_text.assert_called_with(
            "Supported (disabled)"
        )

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

    def test_legacy_text_report_reuses_offline_cpu_rule(self):
        report = sample_report(offline=(2, 5))
        text = format_system_report(report, include_config=False)
        self.assertIn(offline_cpus_text((2, 5)), text)
        no_offline = format_system_report(
            sample_report(), include_config=False
        )
        self.assertNotIn("Disabled CPUs:", no_offline)

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

    def test_legacy_platform_text_preserves_historical_labels(self):
        def device(provider, profile):
            return PlatformProfileDevice(
                provider=provider, profile=profile,
                choices=("balanced", "performance"),
                profile_path=Path("/tmp/synthetic-profile"),
                choices_known=True,
            )

        scenarios = (
            (
                PlatformProfileSnapshot(),
                "No interface detected", "Not available",
                "Could not be determined", "None reported",
            ),
            (
                PlatformProfileSnapshot(
                    devices=(device("vendor-a", "performance"),),
                    interface="modern", current="performance", writable=True,
                    available_profiles=("balanced", "performance"),
                    choices_known=True,
                ),
                "Available", "performance",
                "balanced, performance", "vendor-a",
            ),
            (
                PlatformProfileSnapshot(
                    devices=(device("vendor-a", "performance"),
                             device("vendor-b", None)),
                    interface="modern", current=None, writable=True,
                    choices_known=False,
                ),
                "Partially available", "Unknown",
                "Could not be determined",
                "vendor-a: performance; vendor-b: Unknown",
            ),
            (
                PlatformProfileSnapshot(
                    devices=(device("vendor-a", "balanced"),),
                    interface="legacy", current="balanced", writable=False,
                    available_profiles=("balanced",), choices_known=True,
                ),
                "Read-only", "balanced", "balanced", "vendor-a",
            ),
        )
        for state, status, current, choices, providers in scenarios:
            with self.subTest(status=status):
                report = sample_report(platform=state)
                rendered = present_platform_profile(state)
                output = format_system_report(report, include_config=False)
                self.assertEqual(rendered.status_text, status)
                self.assertIn(
                    f"Platform profile status: {status}", output
                )
                self.assertIn(f"Platform profile: {current}", output)
                self.assertIn(
                    f"Available platform profiles "
                    f"({len(state.available_profiles)}): {choices}"
                    if state.choices_known
                    else "Available platform profiles: Could not be determined",
                    output,
                )
                label, value = format_platform_profile_choices(
                    state, label="Available platform profiles"
                )
                self.assertIn(f"{label}: {value}", output)
                if len(state.provider_states) > 1:
                    self.assertIn(
                        f"Platform profile providers: {providers}", output
                    )
                else:
                    self.assertIn(
                        f"Platform profile provider: {providers}", output
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
