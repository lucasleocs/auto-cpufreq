"""Offline generic kernel charging-limit tests. Writes only to tempfile fixtures."""

import configparser
from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.modules.battery_upower import UPowerBattery
from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe, ChargeCapabilities
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict


class GenericBatteryFixture(unittest.TestCase):
    def setUp(self):
        self.dir = TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.power_supply = Path(self.dir.name) / "power_supply"
        self.power_supply.mkdir()
        self.bat = self.power_supply / "BAT1"
        self.bat.mkdir()
        (self.bat / "type").write_text("Battery\n")
        (self.bat / "scope").write_text("System\n")
        (self.bat / "charge_control_end_threshold").write_text("100\n")

        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.POWER_SUPPLY_DIR",
            str(self.power_supply)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.generic.POWER_SUPPLY_DIR",
            str(self.power_supply)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.battery.SystemInfo.get_battery_path",
            return_value=str(self.bat)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.generic.SystemInfo.get_battery_path",
            return_value=str(self.bat)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe(
                "ready", ChargeCapabilities(charge_protection_supported=False),
            )
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.competing_charge_manager",
            return_value=ChargeConflict(False),
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.battery.lsmod", return_value=False
        ))
        self.set_config(enabled=True, stop="80")

    def set_config(self, *, enabled=True, stop="80", start=None):
        cfg = configparser.ConfigParser()
        values = {"enable_thresholds": str(enabled).lower()}
        if stop is not None:
            values["stop_threshold"] = stop
        if start is not None:
            values["start_threshold"] = start
        cfg["battery"] = values
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.config.get_config",
            return_value=cfg
        ))
        return cfg

    def device(self):
        dev = battery.get_battery_device()
        self.assertIsInstance(dev, GenericKernelBatteryDevice)
        self.assertEqual(dev.batteries, ["BAT1"])
        return dev

    def read_stop(self):
        return int((self.bat / "charge_control_end_threshold").read_text())


class TestOptInGenericStop(GenericBatteryFixture):
    def test_80_percent_is_applied_without_start_attribute(self):
        dev = self.device()
        self.assertNotIn("BAT1", dev.start_paths)
        parsed = dev.get_parsed_config()
        self.assertTrue(parsed["thresholds_enabled"])
        self.assertIsNone(parsed["start_threshold"])
        dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 80)
        self.assertEqual(dev.get_current_threshold("BAT1"), (None, 80))

    def test_not_enabled_never_writes(self):
        dev = self.device()
        cfg = configparser.ConfigParser()
        cfg["battery"] = {"enable_thresholds": "false", "stop_threshold": "80"}
        with patch("auto_cpufreq.battery_scripts.shared.config.get_config", return_value=cfg):
            with patch.object(dev, "_write_value_to_file", side_effect=AssertionError("unexpected write")):
                dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)

    def test_invalid_values_are_rejected_without_writing(self):
        dev = self.device()
        for value in ("", "0", "101", "-4", "80.5", "NaN"):
            with self.subTest(value=value):
                cfg = configparser.ConfigParser()
                cfg["battery"] = {"enable_thresholds": "true", "stop_threshold": value}
                with patch("auto_cpufreq.battery_scripts.shared.config.get_config", return_value=cfg):
                    with patch.object(dev, "_write_value_to_file", side_effect=AssertionError("unexpected write")):
                        dev.apply_threshold_settings()
                self.assertEqual(self.read_stop(), 100)

    def test_start_setting_unsupported_does_not_write(self):
        dev = self.device()
        cfg = configparser.ConfigParser()
        cfg["battery"] = {"enable_thresholds": "true", "start_threshold": "75", "stop_threshold": "80"}
        with patch("auto_cpufreq.battery_scripts.shared.config.get_config", return_value=cfg):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)

    def test_upower_active_protection_blocks_direct_sysfs_write(self):
        dev = self.device()
        mock = UPowerBattery(
            status="Charging", percentage=55, power_watts=12.0,
            charge_protection_supported=True, charge_protection_enabled=True,
        )
        with patch("auto_cpufreq.battery_scripts.priority.probe_charge_service",
                   return_value=ChargeServiceProbe("ready", ChargeCapabilities(
                       charge_protection_supported=True,
                       charge_protection_enabled=True))):
            with patch.object(dev, "_write_value_to_file", side_effect=AssertionError("must not write")):
                dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)

    def test_no_unnecessary_rewrite_when_already_at_target(self):
        dev = self.device()
        (self.bat / "charge_control_end_threshold").write_text("80")
        with patch.object(dev, "_write_value_to_file", side_effect=AssertionError("unnecessary write")):
            dev.apply_threshold_settings()

    def test_kernel_rounding_readback_is_cached_until_external_change(self):
        dev = self.device()
        cfg = configparser.ConfigParser()
        cfg["battery"] = {"enable_thresholds": "true", "stop_threshold": "79"}
        count = [0]
        def kernel_rounds(_path, _requested):
            count[0] += 1
            (self.bat / "charge_control_end_threshold").write_text("80")
            return True
        with patch("auto_cpufreq.battery_scripts.shared.config.get_config", return_value=cfg):
            with patch.object(dev, "_write_value_to_file", side_effect=kernel_rounds):
                dev.apply_threshold_settings()
                dev.apply_threshold_settings()
                self.assertEqual(count[0], 1)
                (self.bat / "charge_control_end_threshold").write_text("100")
                dev.apply_threshold_settings()
                self.assertEqual(count[0], 2)

    def test_failed_write_does_not_report_success(self):
        dev = self.device()
        with patch.object(dev, "_write_value_to_file", return_value=False):
            self.assertFalse(dev.apply_threshold_settings_to_bat("BAT1", dev.get_parsed_config()))
        self.assertEqual(self.read_stop(), 100)

    def test_unreadable_readback_is_rejected(self):
        dev = self.device()
        with patch.object(dev, "_write_value_to_file", return_value=True):
            self.assertFalse(dev.apply_threshold_settings_to_bat("BAT1", dev.get_parsed_config()))
        self.assertEqual(self.read_stop(), 100)

    def test_physical_battery_selection_skips_peripheral(self):
        (self.bat / "scope").write_text("Device")
        dev = battery.get_battery_device()
        self.assertIsNone(dev)

    def test_refuses_path_not_a_power_supply_child(self):
        evil = Path(self.dir.name) / "not-power-supply" / "BAT1"
        evil.mkdir(parents=True)
        (evil / "type").write_text("Battery")
        (evil / "charge_control_end_threshold").write_text("100")
        with patch("auto_cpufreq.battery_scripts.battery.SystemInfo.get_battery_path",
                   return_value=str(evil)), patch(
                "auto_cpufreq.battery_scripts.generic.SystemInfo.get_battery_path",
                return_value=str(evil)):
            self.assertIsNone(battery.get_battery_device())

    def test_standard_kernel_selected_before_vendor_when_supported(self):
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name == "thinkpad_acpi"):
            dev = battery.get_battery_device()
        self.assertIsInstance(dev, GenericKernelBatteryDevice)


if __name__ == "__main__":
    unittest.main()
