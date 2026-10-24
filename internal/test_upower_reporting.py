"""Offline contract tests for optional UPower battery reporting."""

import asyncio
from pathlib import Path
import unittest
from unittest.mock import patch

from auto_cpufreq.modules.battery_upower import (
    _decode, _match_battery, _read_bus, read_upower_battery,
)


BAT1 = "/sys/class/power_supply/BAT1"
OBJ = "/org/freedesktop/UPower/devices/battery_BAT1"


def props(**changes):
    values = dict(
        Type=2, PowerSupply=True, IsPresent=True, NativePath="BAT1",
        State=4, Percentage=99.0, EnergyRate=0.0,
    )
    values.update(changes)
    return values


class Variant:
    def __init__(self, value):
        self.value = value


class Message:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class MessageType:
    METHOD_RETURN = 2


class Reply:
    def __init__(self, val):
        self.message_type = 2
        self.body = [val]


class Bus:
    def __init__(self, paths, by_path):
        self.paths = paths
        self.by_path = by_path
        self.seen = []

    async def call(self, message):
        call = message.kwargs
        self.seen.append(call["path"])
        if call["member"] == "EnumerateDevices":
            return Reply(self.paths)
        return Reply({name: Variant(value) for name, value in self.by_path[call["path"]].items()})


class UPowerBatteryTests(unittest.TestCase):
    def test_galaxy_book_real_hardware_fixture(self):
        value = _decode(props(), BAT1)
        self.assertEqual((value.status, value.percentage, value.power_watts), ("Fully charged", 99, 0))
        self.assertFalse(value.charge_protection_supported)

    def test_states(self):
        labels = ("Charging", "Discharging", "Empty", "Fully charged", "Pending charge", "Pending discharge")
        for state, expected in enumerate(labels, start=1):
            with self.subTest(state=state):
                self.assertEqual(_decode(props(State=state), BAT1).status, expected)

    def test_battery_matching(self):
        self.assertTrue(_match_battery("BAT1", BAT1))
        self.assertTrue(_match_battery(BAT1, BAT1))
        for p in ("BAT0", "/sys/class/power_supply/BAT0", "", None):
            self.assertFalse(_match_battery(p, BAT1))

    def test_no_peripherals_or_invalid_data(self):
        fixtures = [dict(Type=3), dict(PowerSupply=False), dict(IsPresent=False),
                    dict(NativePath="BAT0"), dict(State=0), dict(State="4"),
                    dict(Percentage=-1), dict(Percentage=101), dict(Percentage=float("nan")),
                    dict(EnergyRate=float("nan")), dict(EnergyRate=float("inf")),
                    dict(EnergyRate="0")]
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.assertIsNone(_decode(props(**fixture), BAT1))

    def test_signed_upower_power_is_magnitude(self):
        cases = [
            (1, -12.5, "Charging", 12.5),
            (2, 12.5, "Discharging", 12.5),
            (1, 12.5, "Charging", 12.5),
            (2, -12.5, "Discharging", 12.5),
            (4, 0, "Fully charged", 0.0),
        ]
        for state, rate, expected_state, expected_power in cases:
            with self.subTest(state=state, rate=rate):
                value = _decode(props(State=state, EnergyRate=rate), BAT1)
                self.assertEqual(value.status, expected_state)
                self.assertEqual(value.power_watts, expected_power)

    def test_optional_threshold_capabilities(self):
        value = _decode(props(ChargeThresholdSupported=True, ChargeThresholdEnabled=True,
                              ChargeThresholdSettingsSupported=6), BAT1)
        self.assertTrue(value.charge_protection_supported)
        self.assertTrue(value.charge_protection_enabled)
        self.assertEqual(value.charge_control_types, ("End threshold", "Firmware managed"))
        self.assertIsNone(value.configured_start_threshold)
        self.assertIsNone(value.configured_end_threshold)

    def test_valid_modern_configured_limits_are_filtered_by_capability(self):
        vals = props(ChargeThresholdSupported=True,
                     ChargeThresholdEnabled=True,
                     ChargeThresholdSettingsSupported=3,
                     ChargeStartThreshold=75, ChargeEndThreshold=80)
        value = _decode(vals, BAT1)
        self.assertEqual((value.configured_start_threshold, value.configured_end_threshold), (75, 80))
        value = _decode(dict(vals, ChargeThresholdSettingsSupported=2), BAT1)
        self.assertEqual((value.configured_start_threshold, value.configured_end_threshold), (None, 80))
        value = _decode(dict(vals, ChargeEndThreshold=4294967295), BAT1)
        self.assertIsNone(value.configured_end_threshold)
        self.assertEqual(_decode(props(ChargeThresholdSupported=True), BAT1).charge_control_types, ())

    def test_missing_upower_client_falls_back(self):
        with patch("auto_cpufreq.modules.battery_upower._read_upower", side_effect=ImportError):
            self.assertIsNone(read_upower_battery(BAT1))

    def test_bus_skips_display_device_and_wrong_battery(self):
        display = "/org/freedesktop/UPower/devices/DisplayDevice"
        bat0 = "/org/freedesktop/UPower/devices/battery_BAT0"
        bus = Bus([display, bat0, OBJ], {bat0: props(NativePath="BAT0"), OBJ: props()})
        result = asyncio.run(_read_bus(bus, BAT1, Message, MessageType))
        self.assertEqual(result.status, "Fully charged")
        self.assertNotIn(display, bus.seen)


if __name__ == "__main__":
    unittest.main()
