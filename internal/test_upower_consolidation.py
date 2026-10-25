"""Experimental regression coverage for shared UPower capabilities and presentation."""
import unittest
from dataclasses import asdict
from unittest.mock import patch

from auto_cpufreq.battery_scripts import probe
from auto_cpufreq.modules.battery_upower import (
    ChargeCapabilities, _decode, decode_charge_capabilities,
)
from auto_cpufreq.modules.report_presentation import Availability, interpret_battery
from auto_cpufreq.modules.system_info import BatteryInfo


PATH = "/sys/class/power_supply/BAT1"


def props(**updates):
    properties = {
        "Type": 2, "IsPresent": True, "PowerSupply": True,
        "NativePath": "BAT1", "State": 5, "Percentage": 75.0,
        "EnergyRate": 9.0, "ChargeThresholdSupported": True,
        "ChargeThresholdEnabled": True, "ChargeThresholdSettingsSupported": 3,
        "ChargeStartThreshold": 60, "ChargeEndThreshold": 80,
    }
    properties.update(updates)
    return properties


class UPowerConsolidationTests(unittest.TestCase):
    def test_probe_reexports_shared_capability_type(self):
        self.assertIs(probe.ChargeCapabilities, ChargeCapabilities)

    def test_decoding_is_identical_for_telemetry_and_ownership(self):
        samples = (
            props(),
            props(ChargeThresholdEnabled=False),
            props(ChargeThresholdSettingsSupported=4),
            props(ChargeThresholdSettingsSupported=2),
            props(ChargeThresholdSettingsSupported=0),
            props(
                ChargeThresholdSupported=False,
                ChargeThresholdEnabled=False,
                ChargeThresholdSettingsSupported=0,
                ChargeStartThreshold=0,
                ChargeEndThreshold=0,
            ),
            props(ChargeStartThreshold=2**32 - 1),
            props(ChargeEndThreshold=2**32 - 1),
        )
        for value in samples:
            with self.subTest(value=value):
                capabilities = decode_charge_capabilities(value)
                self.assertEqual(capabilities, probe._charge_capabilities(value))
                battery = _decode(value, PATH)
                self.assertEqual(
                    {
                        "charge_protection_supported": battery.charge_protection_supported,
                        "charge_protection_enabled": battery.charge_protection_enabled,
                        "charge_control_types": battery.charge_control_types,
                        "configured_start_threshold": battery.configured_start_threshold,
                        "configured_end_threshold": battery.configured_end_threshold,
                    },
                    asdict(capabilities),
                )

    def test_older_upower_with_missing_capability_api(self):
        value = props()
        for key in tuple(value):
            if key.startswith("Charge"):
                value.pop(key)
        self.assertFalse(decode_charge_capabilities(value).charge_protection_supported)
        self.assertFalse(_decode(value, PATH).charge_protection_supported)

    def test_newer_upower_with_missing_mask_does_not_infer_numeric_control(self):
        value = props()
        value.pop("ChargeThresholdSettingsSupported")
        caps = decode_charge_capabilities(value)
        self.assertTrue(caps.charge_protection_supported)
        self.assertTrue(caps.charge_protection_enabled)
        self.assertEqual(caps.charge_control_types, ())
        self.assertIsNone(caps.configured_end_threshold)

    def test_malformed_advertised_capabilities_ambiguous_but_preserve_telemetry(self):
        for changes in (
            {"ChargeThresholdSupported": "yes"},
            {"ChargeThresholdSettingsSupported": "3"},
            {"ChargeThresholdSettingsSupported": -1},
            {"ChargeThresholdSettingsSupported": True},
        ):
            with self.subTest(changes=changes):
                value = props(**changes)
                self.assertIsNone(decode_charge_capabilities(value))
                self.assertIsNone(probe._charge_capabilities(value))
                battery = _decode(value, PATH)
                self.assertIsNotNone(battery)
                self.assertIsNone(battery.charge_protection_supported)
                self.assertEqual(battery.percentage, 75)

    def test_matching_observed_and_upower_limit_identifies_kernel(self):
        battery = BatteryInfo(
            False, True, None, 80, 75, 9.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("End threshold",),
            upower_configured_end=80,
        )
        shown = interpret_battery(battery).charge_stop
        self.assertEqual(shown.availability, Availability.AVAILABLE)
        self.assertEqual(shown.text, "80% (kernel; UPower configured)")
        self.assertEqual(shown.value, "80% (kernel; UPower configured)")

    def test_configured_limit_without_kernel_readback_is_unverified(self):
        battery = BatteryInfo(
            False, True, None, None, 75, 9.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("End threshold",),
            upower_configured_end=80,
        )
        shown = interpret_battery(battery).charge_stop
        self.assertEqual(shown.availability, Availability.UNKNOWN)
        self.assertEqual(shown.value, 80)
        self.assertEqual(
            shown.text, "80% configured (UPower; not verified by kernel)"
        )

    def test_mismatch_keeps_two_independent_values(self):
        battery = BatteryInfo(
            False, True, None, 100, 75, 9.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("End threshold",),
            upower_configured_end=80,
        )
        shown = interpret_battery(battery).charge_stop
        self.assertEqual(
            shown.text, "100% (kernel) · 80% configured (UPower)"
        )

    def test_start_and_stop_are_both_provenance_aware(self):
        battery = BatteryInfo(
            False, True, 65, 80, 75, 9.0, status="Not charging",
            charge_protection_supported=True, charge_protection_enabled=True,
            charge_control_types=("Start threshold", "End threshold"),
            upower_configured_start=60, upower_configured_end=80,
        )
        shown = interpret_battery(battery)
        self.assertIn("65% (kernel) · 60% configured (UPower)", shown.charge_start.text)
        self.assertEqual(shown.charge_stop.text, "80% (kernel; UPower configured)")


if __name__ == "__main__":
    unittest.main()
