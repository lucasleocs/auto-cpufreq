"""Audit existing routing, not a proposed implementation.

All writes target TemporaryDirectory power_supply fixtures. A few assertions
intentionally describe unsafe behavior present in the current code so the
maintainer can decide what to change before enabling it on real hardware.
"""

from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.modules.battery_upower import UPowerBattery

from test_generic_kernel_threshold import GenericBatteryFixture


def upower(*, supported, enabled, kinds=(), end=None):
    return UPowerBattery(
        status="Charging", percentage=55, power_watts=12.0,
        charge_protection_supported=supported,
        charge_protection_enabled=enabled,
        charge_control_types=kinds,
        configured_end_threshold=end,
    )


class PriorityAuditTests(GenericBatteryFixture):
    """These tests document today's implementation, including discrepancies."""

    def test_upower_unavailable_goes_directly_to_kernel(self):
        device = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=None):
            self.assertTrue(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 80)

    def test_upower_explicitly_unsupported_goes_to_kernel(self):
        device = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=False, enabled=None)):
            self.assertTrue(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 80)

    def test_upower_supported_but_disabled_currently_writes_sysfs(self):
        # Audit finding: enabled=false is NOT an explicit UPower backend selection.
        device = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=True, enabled=False,
                                       kinds=("End threshold",), end=80)):
            self.assertTrue(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 80)

    def test_upower_supported_but_enabled_unknown_currently_writes_sysfs(self):
        # Audit finding: unknown must not be conflated with unsupported.
        device = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=True, enabled=None,
                                       kinds=("End threshold",), end=80)):
            self.assertTrue(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 80)

    def test_upower_active_blocks_kernel_even_when_same_target_requested(self):
        device = self.device()
        (self.bat / "charge_control_end_threshold").write_text("80")
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=True, enabled=True,
                                       kinds=("End threshold",), end=80)):
            self.assertFalse(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 80)

    def test_upower_active_firmware_only_blocks_kernel(self):
        device = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=True, enabled=True,
                                       kinds=("Firmware managed",))):
            self.assertFalse(device.apply_threshold_settings_to_bat(
                "BAT1", device.get_parsed_config()))
        self.assertEqual(self.read_stop(), 100)

    def test_upower_supported_but_no_kernel_attribute_cannot_select_controller(self):
        (self.bat / "charge_control_end_threshold").unlink()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=upower(supported=True, enabled=True,
                                       kinds=("End threshold",), end=80)):
            self.assertIsNone(battery.get_battery_device())

    def test_vendor_driver_selected_before_generic_upower_capability_check(self):
        marker = object()
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name == "asus_wmi"), patch(
                   "auto_cpufreq.battery_scripts.battery.AsusBatteryDevice",
                   return_value=marker) as vendor:
            self.assertIs(battery.get_battery_device(), marker)
        vendor.assert_called_once_with()

    def test_generic_route_sees_only_one_physical_battery(self):
        other = self.power_supply / "BAT0"
        other.mkdir()
        (other / "type").write_text("Battery")
        (other / "scope").write_text("System")
        (other / "charge_control_end_threshold").write_text("100")
        dev = battery.get_battery_device()
        self.assertIsInstance(dev, GenericKernelBatteryDevice)
        self.assertEqual(dev.batteries, ["BAT1"])
        self.assertEqual((other / "charge_control_end_threshold").read_text(), "100")

    def test_no_auto_fallback_to_vendor_when_generic_write_fails(self):
        dev = self.device()
        with patch("auto_cpufreq.battery_scripts.generic.read_upower_battery",
                   return_value=None), patch.object(
                   dev, "_write_value_to_file", return_value=False):
            self.assertFalse(dev.apply_threshold_settings_to_bat(
                "BAT1", dev.get_parsed_config()))
        # There is no code path that retries with Asus or Ideapad here.
        self.assertEqual(self.read_stop(), 100)
