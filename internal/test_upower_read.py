"""Read-only UPower integration checks (no hardware writes or live system bus)."""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from auto_cpufreq.modules.battery_upower import _decode, _read_bus
from auto_cpufreq.modules.report_presentation import interpret_battery
from auto_cpufreq.modules.system_info import BatteryInfo, SystemInfo, battery_power_label


PATH = "/sys/class/power_supply/BAT1"


def props(**kwargs):
    data = {
        "Type": 2, "PowerSupply": True, "IsPresent": True,
        "NativePath": "BAT1", "State": 5, "Percentage": 79.5,
        "EnergyRate": 12.75, "ChargeThresholdSupported": True,
        "ChargeThresholdEnabled": True,
        "ChargeThresholdSettingsSupported": 2,
        "ChargeEndThreshold": 80,
    }
    data.update(kwargs)
    return data


class BatteryReadTests(unittest.TestCase):
    def test_upower_pending_charge_and_end_only(self):
        battery = _decode(props(), PATH)
        self.assertEqual(battery.status, "Not charging")
        self.assertEqual(battery.percentage, 80)
        self.assertEqual(battery.power_watts, 12.75)
        self.assertEqual(battery.charge_control_types, ("End threshold",))
        self.assertEqual(battery.configured_end_threshold, 80)
        self.assertIsNone(battery.configured_start_threshold)

    def test_older_upower_without_threshold_api(self):
        attributes = props()
        for name in tuple(attributes):
            if name.startswith("Charge"):
                attributes.pop(name)
        battery = _decode(attributes, PATH)
        self.assertIsNotNone(battery)
        self.assertFalse(battery.charge_protection_supported)

    def test_firmware_only_protection(self):
        battery = _decode(props(ChargeThresholdSettingsSupported=4), PATH)
        self.assertEqual(battery.charge_control_types, ("Firmware managed",))
        self.assertIsNone(battery.configured_end_threshold)

    def test_rejects_unrelated_or_invalid_batteries(self):
        self.assertIsNone(_decode(props(NativePath="BAT0"), PATH))
        self.assertIsNone(_decode(props(IsPresent=False), PATH))
        self.assertIsNone(_decode(props(Type=1), PATH))
        self.assertIsNone(_decode(props(Percentage=float("nan")), PATH))

    def test_legacy_sysfs_fallback_and_physical_presence(self):
        observed = {
            "status": "Discharging", "capacity": "67",
            "charge_control_end_threshold": "100",
        }
        def read(path):
            return observed.get(os.path.basename(path))
        with patch.object(SystemInfo, "get_battery_path", return_value=PATH), \
             patch.object(SystemInfo, "read_file", side_effect=read), \
             patch.object(SystemInfo, "external_power_state", return_value=False), \
             patch.object(SystemInfo, "_battery_power_watts", return_value=7.0), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery", return_value=None):
            info = SystemInfo.battery_info()
        self.assertTrue(info.is_present)
        self.assertEqual(info.source, "sysfs")
        self.assertEqual(info.battery_level, 67)
        self.assertEqual(info.charging_stop_threshold, 100)
        self.assertEqual(interpret_battery(info).status.text, "Discharging")

    def test_upower_overlay_does_not_replace_kernel_or_ac_data(self):
        observed = {
            "status": "Not charging", "capacity": "78",
            "charge_control_end_threshold": "100",
        }
        with patch.object(SystemInfo, "get_battery_path", return_value=PATH), \
             patch.object(SystemInfo, "read_file", side_effect=lambda p: observed.get(os.path.basename(p))), \
             patch.object(SystemInfo, "external_power_state", return_value=True), \
             patch.object(SystemInfo, "_battery_power_watts", return_value=10), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery", return_value=_decode(props(), PATH)):
            info = SystemInfo.battery_info()
        self.assertEqual(info.source, "upower")
        self.assertEqual(info.battery_level, 80)
        self.assertEqual(info.charging_stop_threshold, 100)
        self.assertTrue(info.is_ac_plugged)
        display = interpret_battery(info)
        self.assertEqual(display.status.text, "Not charging")
        self.assertIn("100% (kernel)", display.charge_stop.text)
        self.assertIn("80% configured (UPower)", display.charge_stop.text)

    def test_no_physical_battery_preserves_desktop_ac_assumption(self):
        with patch.object(SystemInfo, "get_battery_path", return_value=None), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery",
                   side_effect=AssertionError("must not query UPower without system battery")):
            info = SystemInfo.battery_info()
        self.assertFalse(info.is_present)
        self.assertTrue(info.is_ac_plugged)
        self.assertEqual(interpret_battery(info).status.text, "No battery detected")

    def test_full_and_firmware_managed_display(self):
        info = BatteryInfo(False, True, None, 80, 80, 0.0,
                           status="Fully charged", source="upower",
                           charge_protection_supported=True,
                           charge_protection_enabled=True,
                           charge_control_types=("Firmware managed",))
        display = interpret_battery(info)
        self.assertEqual(display.status.text, "Fully charged")
        self.assertTrue(display.firmware_only)
        self.assertEqual(display.charge_limit.text, "Firmware managed (active)")
        self.assertEqual(battery_power_label(info), "Battery power")

    def test_disabled_charge_protection_reporting(self):
        info = BatteryInfo(False, True, 70, 80, 78, 0.0,
                           charge_protection_supported=True,
                           charge_protection_enabled=False)
        self.assertEqual(
            interpret_battery(info).charge_limit.text, "Supported (disabled)"
        )

    def test_bus_only_issues_read_operations(self):
        class Message:
            def __init__(self, *, member, **kwargs):
                self.member = member

        class Bus:
            def __init__(self):
                self.members = []

            async def call(self, message):
                self.members.append(message.member)
                if message.member == "EnumerateDevices":
                    return SimpleNamespace(
                        message_type="ok",
                        body=[["/org/freedesktop/UPower/devices/battery_BAT1"]],
                    )
                if message.member == "GetAll":
                    return SimpleNamespace(
                        message_type="ok",
                        body=[{name: SimpleNamespace(value=value)
                               for name, value in props().items()}],
                    )
                raise AssertionError(message.member)

        bus = Bus()
        result = asyncio.run(_read_bus(
            bus, PATH, Message, SimpleNamespace(METHOD_RETURN="ok")
        ))
        self.assertIsNotNone(result)
        self.assertEqual(bus.members, ["EnumerateDevices", "GetAll"])


if __name__ == "__main__":
    unittest.main()
