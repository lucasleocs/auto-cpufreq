import os
import unittest
from unittest.mock import patch

from auto_cpufreq.modules.system_info import SystemInfo


class BatteryPowerReportingTests(unittest.TestCase):
    BATTERY_PATH = "/sys/class/power_supply/BAT0"

    def collect_power(self, **overrides):
        values = {
            "status": "Discharging",
            "capacity": "97",
            "power_now": None,
            "current_now": None,
            "voltage_now": None,
            "charge_start_threshold": None,
            "charge_control_start_threshold": None,
            "charge_stop_threshold": None,
            "charge_control_end_threshold": None,
        }
        values.update(overrides)

        def read_file(path):
            return values.get(os.path.basename(path))

        with (
            patch.object(SystemInfo, "get_battery_path", return_value=self.BATTERY_PATH),
            patch.object(SystemInfo, "external_power_state", return_value=False),
            patch.object(SystemInfo, "read_file", side_effect=read_file),
        ):
            return SystemInfo.battery_info().power_consumption

    def test_power_now_is_reported_in_watts(self):
        self.assertAlmostEqual(self.collect_power(power_now="8670000"), 8.67)

    def test_direct_zero_power_is_preserved(self):
        self.assertEqual(self.collect_power(power_now="0"), 0.0)

    def test_current_voltage_fallback_is_reported_in_watts(self):
        self.assertAlmostEqual(
            self.collect_power(current_now="1475000", voltage_now="10000000"),
            14.75,
        )

    def test_zero_current_fallback_is_preserved(self):
        self.assertEqual(
            self.collect_power(current_now="0", voltage_now="10000000"),
            0.0,
        )

    def test_negative_discharge_current_is_supported(self):
        self.assertAlmostEqual(
            self.collect_power(current_now="-867000", voltage_now="10000000"),
            8.67,
        )

    def test_missing_power_telemetry_is_unavailable(self):
        self.assertIsNone(self.collect_power())


if __name__ == "__main__":
    unittest.main()
