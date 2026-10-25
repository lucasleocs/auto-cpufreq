"""Backend policy-fit selection and UPower state, with simulated kernel supplies."""
import configparser
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.battery_scripts.asus import AsusBatteryDevice
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict
from auto_cpufreq.battery_scripts.probe import ChargeCapabilities, ChargeServiceProbe
from auto_cpufreq.modules.battery_upower import _decode


class BackendFitTests(unittest.TestCase):
    def setUp(self):
        t = TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name) / "power_supply"
        self.root.mkdir()
        self.bat = self.root / "BAT1"
        self.bat.mkdir()
        (self.bat / "type").write_text("Battery")
        (self.bat / "scope").write_text("System")
        (self.bat / "charge_control_end_threshold").write_text("100")
        (self.bat / "charge_start_threshold").write_text("70")
        (self.bat / "charge_stop_threshold").write_text("100")
        self.config = configparser.ConfigParser()
        self.config["battery"] = {
            "enable_thresholds": "true", "battery_device": "BAT1",
            "start_threshold": "70", "stop_threshold": "80",
        }
        stack = ExitStack()
        self.addCleanup(stack.close)
        for m in ("auto_cpufreq.battery_scripts.shared",
                  "auto_cpufreq.battery_scripts.generic",
                  "auto_cpufreq.modules.system_info"):
            stack.enter_context(patch(m + ".POWER_SUPPLY_DIR", str(self.root)))
        stack.enter_context(patch.object(battery.config, "get_config",
                                         return_value=self.config))
        stack.enter_context(patch.object(battery, "lsmod",
                                         side_effect=lambda mod: mod=="asus_wmi"))
        stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.competing_charge_manager",
            return_value=ChargeConflict(False),
        ))
        stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe("ready", ChargeCapabilities(False))
        ))

    def test_start_requested_with_partial_standard_prefers_complete_legacy_pair(self):
        dev = battery.get_battery_device()
        self.assertIsInstance(dev, AsusBatteryDevice)
        self.assertEqual(dev.start_paths["BAT1"], str(self.bat / "charge_start_threshold"))
        self.assertEqual(dev.stop_paths["BAT1"], str(self.bat / "charge_stop_threshold"))

    def test_legacy_pair_used_exclusively_during_application(self):
        dev = battery.get_battery_device()
        self.assertTrue(dev.apply_threshold_settings(self.config))
        self.assertEqual((self.bat / "charge_start_threshold").read_text(), "70")
        self.assertEqual((self.bat / "charge_stop_threshold").read_text(), "80")
        self.assertEqual((self.bat / "charge_control_end_threshold").read_text(), "100")

    def test_complete_standard_pair_stays_on_generic_backend(self):
        (self.bat / "charge_control_start_threshold").write_text("65")
        dev = battery.get_battery_device()
        self.assertIsInstance(dev, GenericKernelBatteryDevice)
        self.assertEqual(dev.start_paths["BAT1"],
                         str(self.bat / "charge_control_start_threshold"))
        self.assertEqual(dev.stop_paths["BAT1"],
                         str(self.bat / "charge_control_end_threshold"))

    def test_stop_only_stays_generic_with_partial_standard_pair(self):
        del self.config["battery"]["start_threshold"]
        dev = battery.get_battery_device()
        self.assertIsInstance(dev, GenericKernelBatteryDevice)
        self.assertEqual(dev.stop_paths["BAT1"],
                         str(self.bat / "charge_control_end_threshold"))

    def test_no_compatible_pair_fails_before_any_writes(self):
        (self.bat / "charge_stop_threshold").unlink()
        self.assertIsNone(battery.get_battery_device())
        with patch("auto_cpufreq.battery_scripts.shared.BatteryDevice._write_value_to_file",
                   side_effect=AssertionError("no device selected, must not write")):
            controller = battery.BatteryDaemonController()
            controller.tick(now=0)
        self.assertEqual((self.bat / "charge_control_end_threshold").read_text(), "100")

    def test_vendor_module_missing_does_not_mix_interfaces(self):
        with patch.object(battery, "lsmod", return_value=False):
            self.assertIsNone(battery.get_battery_device())

    def test_invalid_device_does_not_fallback(self):
        self.config["battery"]["battery_device"] = "BAD0"
        self.assertIsNone(battery.get_battery_device())

    def test_generic_always_refuses_legacy_mixing_if_instantiated_directly(self):
        dev = GenericKernelBatteryDevice(self.config)
        self.assertIn("BAT1", dev.stop_paths)
        self.assertNotIn("BAT1", dev.start_paths)

    def test_active_upower_does_not_conflate_legacy_and_standard_readbacks(self):
        # Writer selects the complete legacy pair. The standard end-only
        # attribute happens to match UPower, but the legacy stop does not.
        (self.bat / "charge_control_end_threshold").write_text("80")
        with patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe("ready", ChargeCapabilities(
                True, True, ("Start threshold", "End threshold"),
                configured_start_threshold=70, configured_end_threshold=80,
            )),
        ):
            dev = battery.get_battery_device()
            self.assertIsInstance(dev, AsusBatteryDevice)
            with patch.object(dev, "_write_value_to_file",
                              side_effect=AssertionError("UPower owns policy")):
                outcome = dev.apply_threshold_settings(self.config)
        self.assertEqual(outcome.value, "blocked")
        self.assertEqual((self.bat / "charge_stop_threshold").read_text(), "100")

    def test_active_upower_satisfied_by_selected_legacy_pair(self):
        # A matching legacy pair should count even if a leftover standard
        # stop attribute disagrees: only the selected paths are authoritative.
        (self.bat / "charge_stop_threshold").write_text("80")
        with patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe("ready", ChargeCapabilities(
                True, True, ("Start threshold", "End threshold"),
                configured_start_threshold=70, configured_end_threshold=80,
            )),
        ):
            dev = battery.get_battery_device()
            self.assertIsInstance(dev, AsusBatteryDevice)
            with patch.object(dev, "_write_value_to_file",
                              side_effect=AssertionError("already satisfied")):
                outcome = dev.apply_threshold_settings(self.config)
        self.assertEqual(outcome.value, "satisfied")

    def test_battery_state_five_preserved_from_upower(self):
        value = _decode({
            "Type": 2, "PowerSupply": True, "IsPresent": True,
            "NativePath": "BAT1", "State": 5, "Percentage": 79.0,
        }, "/sys/class/power_supply/BAT1")
        self.assertEqual(value.status, "Pending charge")


if __name__ == "__main__":
    unittest.main()
