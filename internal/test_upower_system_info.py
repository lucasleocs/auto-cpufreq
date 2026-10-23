"""End-to-end battery provider selection without real hardware writes."""

from unittest import TestCase
from unittest.mock import patch

from auto_cpufreq.modules.battery_upower import UPowerBattery
from auto_cpufreq.modules.system_info import SystemInfo, battery_status_label, battery_power_label

BAT1 = "/sys/class/power_supply/BAT1"
SYSFS = {
    f"{BAT1}/status": "Discharging",
    f"{BAT1}/capacity": "99",
    f"{BAT1}/charge_control_end_threshold": "100",
}

class BatteryIntegrationTests(TestCase):
    def setUp(self):
        self.battery = patch.object(SystemInfo, "get_battery_path", return_value=BAT1)
        self.ac = patch.object(SystemInfo, "external_power_state", return_value=True)
        self.files = patch.object(SystemInfo, "read_file", side_effect=lambda path: SYSFS.get(path))
        self.watts = patch.object(SystemInfo, "_battery_power_watts", return_value=0.405288)
        for mocked in (self.battery, self.ac, self.files, self.watts):
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_upower_first_preserves_kernel_ac_and_threshold(self):
        upower = UPowerBattery(
            status="Fully charged", percentage=99, power_watts=0.0,
        )
        with patch("auto_cpufreq.modules.system_info.read_upower_battery", return_value=upower):
            info = SystemInfo.battery_info()
        self.assertEqual(info.source, "upower")
        self.assertEqual(info.status, "Fully charged")
        self.assertEqual(battery_status_label(info), "Fully charged")
        self.assertEqual(info.power_watts, 0.0)
        self.assertEqual(info.charging_stop_threshold, 100)
        self.assertIs(info.is_ac_plugged, True)
        self.assertEqual(battery_power_label(info), "Battery power")

    def test_missing_upower_uses_existing_sysfs(self):
        with patch("auto_cpufreq.modules.system_info.read_upower_battery", return_value=None):
            info = SystemInfo.battery_info()
        self.assertEqual(info.source, "sysfs")
        self.assertEqual(info.status, "Discharging")
        self.assertEqual(battery_status_label(info), "Discharging")
        self.assertAlmostEqual(info.power_watts, 0.405288)
        self.assertEqual(info.charging_stop_threshold, 100)
        self.assertIs(info.is_ac_plugged, True)
        self.assertEqual(battery_power_label(info), "Discharge power")

    def test_modern_upower_reports_read_only_capabilities(self):
        upower = UPowerBattery(
            status="Charging", percentage=79, power_watts=5.5,
            charge_protection_supported=True,
            charge_protection_enabled=True,
            charge_control_types=("End threshold",),
        )
        with patch("auto_cpufreq.modules.system_info.read_upower_battery", return_value=upower):
            info = SystemInfo.battery_info()
        self.assertTrue(info.charge_protection_supported)
        self.assertTrue(info.charge_protection_enabled)
        self.assertEqual(info.charge_control_types, ("End threshold",))
        self.assertEqual(info.charging_stop_threshold, 100)
        self.assertEqual(battery_status_label(info), "Charging")
