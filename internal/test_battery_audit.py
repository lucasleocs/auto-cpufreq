"""Regression tests for battery audit corrections, no host sysfs writes."""
import configparser
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from auto_cpufreq.core import get_power_supply_ignore_list
from auto_cpufreq.modules.battery_upower import _decode
from auto_cpufreq.modules.report_presentation import (
    Availability, interpret_battery, present_report,
)
from auto_cpufreq.modules.system_info import BatteryInfo, SystemInfo, SystemReport
from auto_cpufreq.modules.system_monitor import SystemMonitor


PATH = "/sys/class/power_supply/BAT1"


def props(**kwargs):
    data = dict(
        Type=2, PowerSupply=True, IsPresent=True, NativePath="BAT1",
        State=5, Percentage=80.0, EnergyRate=10.0,
        ChargeThresholdSupported=True, ChargeThresholdEnabled=True,
        ChargeThresholdSettingsSupported=2, ChargeEndThreshold=80,
    )
    data.update(kwargs)
    return data


class AuditTests(unittest.TestCase):
    def test_explicit_snapshot_controls_ignored_batteries(self):
        with TemporaryDirectory() as root:
            for bat in ("BAT0", "BAT1"):
                location = Path(root) / bat
                location.mkdir()
                (location / "type").write_text("Battery")
                (location / "scope").write_text("System")
            snapshot = configparser.ConfigParser()
            snapshot["power_supply_ignore_list"] = {"device": "BAT0"}
            with patch("auto_cpufreq.modules.system_info.POWER_SUPPLY_DIR", root), \
                 patch("auto_cpufreq.core.config.get_config",
                       side_effect=AssertionError("second snapshot read")):
                selected = SystemInfo.get_battery_path(snapshot)
            self.assertEqual(selected, str(Path(root) / "BAT1"))

    def test_ignore_list_default_callers_keep_existing_contract(self):
        cfg = configparser.ConfigParser()
        cfg["power_supply_ignore_list"] = {"mouse": "hidpp", "battery": "BAT0"}
        with patch("auto_cpufreq.core.config.get_config", return_value=cfg):
            self.assertEqual(
                get_power_supply_ignore_list(), ["hidpp", "BAT0", "hidpp_battery"]
            )

    def test_standard_sysfs_attributes_win_when_both_are_present(self):
        observed = {
            "status": "Not charging",
            "capacity": "80",
            "charge_control_start_threshold": "70",
            "charge_start_threshold": "10",
            "charge_control_end_threshold": "80",
            "charge_stop_threshold": "100",
        }
        with patch.object(SystemInfo, "get_battery_path", return_value=PATH), \
             patch.object(SystemInfo, "read_file",
                          side_effect=lambda p: observed.get(os.path.basename(p))), \
             patch.object(SystemInfo, "external_power_state", return_value=True), \
             patch.object(SystemInfo, "_battery_power_watts", return_value=5.0), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery",
                   return_value=None):
            battery = SystemInfo.battery_info()
        self.assertEqual(battery.charging_start_threshold, 70)
        self.assertEqual(battery.charging_stop_threshold, 80)

    def test_missing_energy_rate_preserves_upower_telemetry(self):
        result = _decode(props(EnergyRate=None), PATH)
        self.assertIsNotNone(result)
        self.assertEqual(result.percentage, 80)
        self.assertIsNone(result.power_watts)

    def test_bad_energy_rate_keeps_upower_data_and_sysfs_power(self):
        result = _decode(props(EnergyRate=float("nan")), PATH)
        self.assertIsNotNone(result)
        self.assertIsNone(result.power_watts)
        with patch.object(SystemInfo, "get_battery_path", return_value=PATH), \
             patch.object(SystemInfo, "read_file", return_value=None), \
             patch.object(SystemInfo, "external_power_state", return_value=True), \
             patch.object(SystemInfo, "_battery_power_watts", return_value=8.5), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery",
                   return_value=result):
            battery = SystemInfo.battery_info()
        self.assertEqual(battery.source, "upower")
        self.assertEqual(battery.battery_level, 80)
        self.assertEqual(battery.power_watts, 8.5)

    def test_malformed_capability_is_unknown_not_unsupported(self):
        malformed = _decode(props(ChargeThresholdSupported="yes"), PATH)
        self.assertIsNone(malformed.charge_protection_supported)
        battery = BatteryInfo(
            False, True, None, 80, 80, 10.0, status=malformed.status,
            charge_protection_supported=malformed.charge_protection_supported,
        )
        displayed = interpret_battery(battery)
        self.assertEqual(displayed.charge_limit.availability, Availability.UNKNOWN)
        self.assertEqual(displayed.charge_limit.text, "UPower capability unknown")

    def test_bad_capability_mask_is_unknown(self):
        malformed = _decode(props(ChargeThresholdSettingsSupported="2"), PATH)
        self.assertIsNone(malformed.charge_protection_supported)

    def test_old_upower_without_control_api_is_unsupported(self):
        old = props()
        for key in list(old):
            if key.startswith("Charge"):
                old.pop(key)
        result = _decode(old, PATH)
        self.assertIs(result.charge_protection_supported, False)

    def test_status_and_firmware_presentation_contracts(self):
        info = BatteryInfo(
            False, True, None, 80, 80, 0.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("Firmware managed",),
        )
        view = interpret_battery(info)
        self.assertEqual(view.status.text, "Not charging")
        self.assertTrue(view.firmware_only)
        self.assertEqual(view.charge_limit.text, "Firmware managed (active)")

    def test_gtk_renderer_hides_and_restores_threshold_rows(self):
        """Exercise the GTK renderer with inert widget stand-ins."""
        import importlib
        import sys
        from collections import defaultdict
        from types import ModuleType

        class Label:
            def __init__(self):
                self.text = ""
                self.visible = True

            def set_text(self, text):
                self.text = text

            def set_no_show_all(self, value):
                pass

            def show(self):
                self.visible = True

            def hide(self):
                self.visible = False

        class Grid:
            def __init__(self):
                self.widgets = {(col, row): Label()
                                for col in (0, 1) for row in range(7)}

            def get_child_at(self, col, row):
                return self.widgets.get((col, row))

        fake_gi = ModuleType("gi")
        fake_gi.require_version = lambda *_: None
        fake_repository = ModuleType("gi.repository")
        fake_repository.Gtk = SimpleNamespace(Box=object)
        fake_repository.Pango = SimpleNamespace()
        with patch.dict(sys.modules, {
            "gi": fake_gi,
            "gi.repository": fake_repository,
        }):
            report_view = importlib.import_module(
                "auto_cpufreq.gui.report_view"
            )

        view = report_view.SystemReportView.__new__(
            report_view.SystemReportView
        )
        for attr in (
            "system_values", "platform_values", "platform_names",
            "power_values", "stats_values", "battery_values",
            "battery_names",
        ):
            setattr(view, attr, defaultdict(Label))
        for attr in ("platform_grid", "power_grid", "battery_grid"):
            setattr(view, attr, Grid())
        for attr in (
            "max_freq_value", "min_freq_value", "fan_label", "offline_label",
        ):
            setattr(view, attr, Label())
        view._refresh_core_rows = lambda _: None
        battery = BatteryInfo(
            False, True, None, 80, 80, 0.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("Firmware managed",),
        )
        report = self._sample_report(battery)
        view.apply_report(report)
        self.assertEqual(view.battery_values["status"].text, "Not charging")
        self.assertEqual(
            view.battery_values["limit"].text, "Firmware managed (active)"
        )
        self.assertFalse(view.battery_grid.get_child_at(0, 3).visible)
        self.assertFalse(view.battery_grid.get_child_at(0, 4).visible)
        self.assertTrue(view.battery_grid.get_child_at(0, 6).visible)

        report.battery_info = BatteryInfo(False, True, 70, 80, 80, 0.0)
        view.apply_report(report)
        self.assertTrue(view.battery_grid.get_child_at(0, 3).visible)
        self.assertTrue(view.battery_grid.get_child_at(0, 4).visible)
        self.assertFalse(view.battery_grid.get_child_at(0, 6).visible)

        report.battery_info.is_present = False
        view.apply_report(report)
        self.assertEqual(view.battery_values["status"].text, "No battery detected")
        self.assertFalse(view.battery_grid.get_child_at(0, 6).visible)

    @staticmethod
    def _sample_report(battery):
        return SystemReport(
            distro_name="Test", distro_ver="1", arch="x86_64",
            processor_model="Example CPU", total_core=2, kernel_version="7.0",
            current_gov="powersave", current_epp=None, current_epb=None,
            current_hwp_dynamic_boost=None, cpu_driver="intel_pstate",
            cpu_fan_speed=None, cpu_usage=1.0, cpu_max_freq=None,
            cpu_min_freq=None, load=0.1, avg_load=None, cores_info=[],
            battery_info=battery, is_turbo_on=(None, None),
        )

    def test_terminal_consumes_same_battery_presentation(self):
        battery = BatteryInfo(
            False, True, None, 80, 80, 0.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("Firmware managed",),
        )
        report = SystemReport(
            distro_name="Test", distro_ver="1", arch="x86_64",
            processor_model="Example CPU", total_core=2, kernel_version="7.0",
            current_gov="powersave", current_epp=None, current_epb=None,
            current_hwp_dynamic_boost=None, cpu_driver="intel_pstate",
            cpu_fan_speed=None, cpu_usage=1.0, cpu_max_freq=None,
            cpu_min_freq=None, load=0.1, avg_load=None, cores_info=[],
            battery_info=battery, is_turbo_on=(None, None),
        )
        terminal = SystemMonitor.__new__(SystemMonitor)
        terminal.left_content = []
        terminal.right_content = []
        terminal.suggestion = False
        terminal.format_system_info(report)
        lines = []
        for widget in terminal.right_content:
            text_widget = getattr(widget, "original_widget", widget)
            if hasattr(text_widget, "get_text"):
                lines.append(text_widget.get_text()[0])
        combined = "\n".join(lines)
        view = present_report(report)
        self.assertIn("Battery status: " + view.battery.status.text, combined)
        self.assertIn("Charge limit: " + view.battery.charge_limit.text, combined)
        self.assertNotIn("Charging stop threshold:", combined)


if __name__ == "__main__":
    unittest.main()
