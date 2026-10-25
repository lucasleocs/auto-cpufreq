"""Lab-only coverage for conservative UPower, explicit vendor targets, and Lenovo readback.

All write targets use temporary files. No host sysfs or hardware writes.
"""
import configparser
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.ideapad_laptop import IdeapadBatteryDevice
from auto_cpufreq.battery_scripts.probe import ChargeCapabilities, ChargeServiceProbe
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict
from auto_cpufreq.modules.battery_upower import _decode, decode_charge_capabilities
from auto_cpufreq.modules.system_info import SystemInfo


class ChargingAuditRegressionTests(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name) / "power_supply"
        self.root.mkdir()
        self.selected = self.root / "CMB0"
        self.selected.mkdir()
        self.other = self.root / "BAT0"
        self.other.mkdir()
        for path in (self.selected, self.other):
            (path / "type").write_text("Battery")
            (path / "scope").write_text("System")
            (path / "charge_stop_threshold").write_text("100")
            (path / "charge_start_threshold").write_text("70")
        self.conf = configparser.ConfigParser()
        self.conf["battery"] = {
            "enable_thresholds": "true", "battery_device": "CMB0",
            "start_threshold": "70", "stop_threshold": "80",
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (
            "auto_cpufreq.modules.system_info",
            "auto_cpufreq.battery_scripts.shared",
            "auto_cpufreq.battery_scripts.generic",
        ):
            self.stack.enter_context(
                patch(module + ".POWER_SUPPLY_DIR", str(self.root))
            )
        self.stack.enter_context(
            patch.object(battery.config, "get_config", return_value=self.conf)
        )
        self.stack.enter_context(
            patch.object(battery, "lsmod",
                         side_effect=lambda name: name == "asus_wmi")
        )
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.competing_charge_manager",
            return_value=ChargeConflict(False)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe(
                "ready", ChargeCapabilities(False)
            ),
        ))

    def test_explicit_non_bat_vendor_can_select_valid_system_battery(self):
        self.assertEqual(
            SystemInfo.get_battery_path(self.conf, strict_configured=True),
            str(self.selected)
        )
        device = battery.get_battery_device()
        self.assertIsInstance(device, battery.AsusBatteryDevice)
        self.assertEqual(device.batteries, ["CMB0"])
        self.assertEqual(list(device.stop_paths), ["CMB0"])

    def test_explicit_non_bat_vendor_writes_only_selected_device(self):
        controller = battery.BatteryDaemonController()
        controller.tick(now=0)
        self.assertEqual((self.selected / "charge_stop_threshold").read_text(), "80")
        self.assertEqual((self.other / "charge_stop_threshold").read_text(), "100")

    def test_explicit_peripheral_non_bat_never_falls_back(self):
        (self.selected / "scope").write_text("Device")
        self.assertIsNone(battery.get_battery_device())
        controller = battery.BatteryDaemonController()
        controller.tick(now=0)
        self.assertEqual((self.other / "charge_stop_threshold").read_text(), "100")

    def test_default_vendor_enumeration_retains_only_bat_prefix(self):
        del self.conf["battery"]["battery_device"]
        device = battery.get_battery_device()
        self.assertEqual(device.batteries, ["BAT0"])

    def test_explicit_invalid_path_is_still_rejected(self):
        for value in ("../CMB0", "/tmp/CMB0", "", "MISSING"):
            with self.subTest(value=value):
                self.conf["battery"]["battery_device"] = value
                self.assertIsNone(battery.get_battery_device())

    def test_disabled_policy_does_not_write_unusual_name(self):
        self.conf["battery"]["enable_thresholds"] = "false"
        controller = battery.BatteryDaemonController()
        controller.tick(now=0)
        self.assertEqual((self.selected / "charge_stop_threshold").read_text(), "100")

    def test_upower_false_support_with_active_protection_is_ambiguous(self):
        self.assertIsNone(decode_charge_capabilities({
            "ChargeThresholdSupported": False,
            "ChargeThresholdEnabled": True,
        }))

    def test_upower_false_support_with_configured_controls_is_ambiguous(self):
        for update in (
            {"ChargeThresholdSettingsSupported": 2},
            {"ChargeEndThreshold": 80},
            {"ChargeStartThreshold": 65},
            {"ChargeThresholdSettingsSupported": "invalid"},
            {"ChargeThresholdEnabled": "yes"},
        ):
            with self.subTest(update=update):
                self.assertIsNone(decode_charge_capabilities({
                    "ChargeThresholdSupported": False, **update
                }))

    def test_upower_false_support_with_explicitly_inactive_fields_is_valid(self):
        value = decode_charge_capabilities({
            "ChargeThresholdSupported": False,
            "ChargeThresholdEnabled": False,
            "ChargeThresholdSettingsSupported": 0,
            "ChargeEndThreshold": 0,
        })
        self.assertFalse(value.charge_protection_supported)

    def test_upower_contradiction_preserves_valid_telemetry(self):
        value = {
            "Type": 2, "PowerSupply": True, "IsPresent": True,
            "NativePath": "BAT1", "State": 2, "Percentage": 74.0,
            "EnergyRate": 6.0, "ChargeThresholdSupported": False,
            "ChargeThresholdEnabled": True,
        }
        output = _decode(value, "/sys/class/power_supply/BAT1")
        self.assertIsNotNone(output)
        self.assertIsNone(output.charge_protection_supported)
        self.assertEqual(output.percentage, 74)

    def test_lenovo_no_write_when_conservation_already_matches(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("1")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        with patch.object(device, "_write_value_to_file",
                          side_effect=AssertionError("must not rewrite")):
            self.assertTrue(device.set_conservation_mode(1))
        self.assertEqual(mode.read_text(), "1")

    def test_lenovo_verifies_real_write_by_readback(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("0")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        self.assertTrue(device.set_conservation_mode(1))
        self.assertEqual(mode.read_text(), "1")
        self.assertTrue(device.set_conservation_mode(0))
        self.assertEqual(mode.read_text(), "0")

    def test_lenovo_unreadable_initial_state_prevents_write(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("not readable")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        with patch.object(device, "_write_value_to_file",
                          side_effect=AssertionError("unsafe write")):
            self.assertFalse(device.set_conservation_mode(1))

    def test_lenovo_writer_claims_success_without_state_change(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("0")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        with patch.object(device, "_write_value_to_file", return_value=True):
            self.assertFalse(device.set_conservation_mode(1))
        self.assertEqual(mode.read_text(), "0")

    def test_lenovo_readback_unavailable_after_write_fails(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("0")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        with patch.object(device, "_read_value_from_file",
                          side_effect=["0", ""]):
            with patch.object(device, "_write_value_to_file", return_value=True):
                self.assertFalse(device.set_conservation_mode(1))

    def test_lenovo_invalid_requested_values_do_not_write(self):
        mode = Path(self.work.name) / "conservation_mode"
        mode.write_text("0")
        with patch.object(IdeapadBatteryDevice, "_find_conservation_mode_path",
                          return_value=str(mode)):
            device = IdeapadBatteryDevice(self.conf)
        with patch.object(device, "_write_value_to_file",
                          side_effect=AssertionError("invalid write")):
            for value in (-1, 2, None, True, "1"):
                with self.subTest(value=value):
                    self.assertFalse(device.set_conservation_mode(value))


if __name__ == "__main__":
    unittest.main()
