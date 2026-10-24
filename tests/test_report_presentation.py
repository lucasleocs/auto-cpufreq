"""Shared frontend semantics: synthetic data only, with no sysfs writes."""
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.report_presentation import (
    Availability, interpret_battery, interpret_turbo, present_report,
)


def battery(**overrides):
    data = dict(
        is_charging=False, is_ac_plugged=True,
        charging_start_threshold=0, charging_stop_threshold=80,
        battery_level=55, power_watts=8.65,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def report(**overrides):
    data = dict(
        current_gov="powersave",
        current_epp="balance_power",
        current_epb="normal",
        current_hwp_dynamic_boost=None,
        is_turbo_on=(True, None),
        battery_info=battery(),
        cpu_avg_temp=52.0,
        cores_info=[SimpleNamespace(temperature=51.0)],
    )
    data.update(overrides)
    return SimpleNamespace(**data)


class PresentationTests(unittest.TestCase):
    def test_explicit_turbo_on_off_and_driver_managed_are_distinct(self):
        for state, expected_text, expected_availability in (
            ((True, None), "On", Availability.AVAILABLE),
            ((False, None), "Off", Availability.AVAILABLE),
            ((None, True), "Driver managed", Availability.DRIVER_MANAGED),
            ((None, False), "Unavailable", Availability.UNAVAILABLE),
            ((None, None), "Unavailable", Availability.UNAVAILABLE),
        ):
            with self.subTest(state=state):
                out = interpret_turbo(state)
                self.assertEqual(out.text, expected_text)
                self.assertEqual(out.availability, expected_availability)

    def test_unknown_power_source_is_not_assumed_to_be_ac(self):
        p = present_report(report(
            battery_info=battery(is_charging=None, is_ac_plugged=None)
        ))
        self.assertEqual(p.battery.ac.availability, Availability.UNKNOWN)
        self.assertEqual(p.battery.status.text, "Unknown")

    def test_ac_battery_charging_and_no_battery(self):
        self.assertEqual(
            interpret_battery(battery(is_charging=True)).status.text, "Charging"
        )
        self.assertEqual(
            interpret_battery(battery(is_ac_plugged=False)).status.text,
            "Discharging",
        )
        self.assertEqual(
            interpret_battery(battery(is_ac_plugged=True)).status.text,
            "Not charging",
        )
        p = present_report(report(battery_info=None))
        self.assertEqual(p.battery.status.availability, Availability.UNAVAILABLE)
        self.assertEqual(p.battery.charge_stop.text, "Unavailable")

    def test_no_physical_battery_is_distinct_from_ac_connection(self):
        battery_without_device = battery(
            is_present=False, is_charging=None, is_ac_plugged=True,
            battery_level=None,
        )
        result = interpret_battery(battery_without_device)
        self.assertEqual(result.status.text, "No battery detected")
        self.assertEqual(result.status.availability, Availability.UNAVAILABLE)
        self.assertEqual(result.level.text, "Unavailable")
        self.assertIs(battery_without_device.is_ac_plugged, True)

        # Legacy BatteryInfo-like snapshots without this attribute still
        # follow their existing charging/AC semantics.
        self.assertEqual(
            interpret_battery(battery(is_ac_plugged=True)).status.text,
            "Not charging",
        )

    def test_zero_threshold_remains_valid(self):
        p = present_report(report())
        self.assertEqual(p.battery.charge_start.text, "0")
        self.assertEqual(p.battery.charge_stop.text, "80")
        self.assertEqual(p.battery.level.text, "55%")
        self.assertEqual(p.battery.power_watts.text, "8.65 W")

    def test_missing_governor_epp_epb_not_reported_as_disabled(self):
        p = present_report(report(
            current_gov=None, current_epp=None, current_epb=None,
            current_hwp_dynamic_boost=None, is_turbo_on=(None, True)
        ))
        self.assertEqual(p.governor.availability, Availability.UNAVAILABLE)
        self.assertEqual(p.epp.text, "Unavailable")
        self.assertEqual(p.epb.text, "Unavailable")
        self.assertEqual(p.hwp_dynamic_boost.text, "Unavailable")
        self.assertEqual(p.turbo.availability, Availability.DRIVER_MANAGED)

    def test_temperature_uses_fallback_or_explicit_reading(self):
        self.assertEqual(
            present_report(report()).average_temperature.text, "52.0 °C"
        )
        p = present_report(report(
            cpu_avg_temp=None,
            cores_info=[
                SimpleNamespace(temperature=40.0),
                SimpleNamespace(temperature=0),
                SimpleNamespace(temperature=60.0),
            ],
        ))
        self.assertEqual(p.average_temperature.text, "50.0 °C")
        self.assertEqual(
            present_report(report(
                cpu_avg_temp=None, cores_info=[SimpleNamespace(temperature=0)]
            )).average_temperature.availability,
            Availability.UNAVAILABLE,
        )

    def test_view_model_does_not_modify_its_report(self):
        original = report()
        before = vars(original).copy()
        result = present_report(original)
        self.assertEqual(vars(original), before)
        self.assertEqual(result.governor.text, "powersave")


if __name__ == "__main__":
    unittest.main(verbosity=2)
