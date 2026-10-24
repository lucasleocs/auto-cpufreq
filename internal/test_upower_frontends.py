"""GTK, --stats and threshold formatting contracts (temporary only)."""

from dataclasses import replace
import unittest

import urwid

from auto_cpufreq.modules.system_info import (
    BatteryInfo, CoreInfo, SystemReport, battery_threshold_display,
)
from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType


def battery(**changes):
    original = BatteryInfo(
        is_charging=False, is_ac_plugged=True,
        charging_start_threshold=None, charging_stop_threshold=100,
        battery_level=99, power_watts=0.0,
        status="Fully charged", source="upower",
    )
    return replace(original, **changes)


def report(b):
    return SystemReport(
        distro_name="Test Linux", distro_ver="1", arch="x86_64",
        processor_model="Test CPU", total_core=1, kernel_version="6.0-test",
        current_gov="powersave", current_epp=None, current_epb=None,
        current_hwp_dynamic_boost=None, cpu_driver="test",
        cpu_fan_speed=None, cpu_usage=0.0, cpu_max_freq=2000,
        cpu_min_freq=400, load=0.0, avg_load=(0.0, 0.0, 0.0),
        cores_info=[CoreInfo(id=0, usage=0.0, temperature=35.0, frequency=800.0)],
        battery_info=b, is_turbo_on=(False, False),
    )


def stats_lines(b):
    monitor = SystemMonitor(type=ViewType.STATS)
    monitor.format_system_info(report(b))
    return [
        (entry.original_widget if isinstance(entry, urwid.AttrMap) else entry).get_text()[0]
        for entry in monitor.right_content
    ]


END = ("End threshold",)
BOTH = ("Start threshold", "End threshold")
FIRMWARE = ("Firmware managed",)


class CompactBatteryPresentationTests(unittest.TestCase):
    def test_legacy_retains_simple_stop_without_source_suffix(self):
        b = battery()
        display = battery_threshold_display(b)
        self.assertEqual(display.start, "Unavailable")
        self.assertEqual(display.stop, "100%")
        self.assertIsNone(display.charge_limit)
        lines = stats_lines(b)
        self.assertIn("Charging start threshold: Unavailable", lines)
        self.assertIn("Charging stop threshold: 100%", lines)
        self.assertNotIn("Charge protection: Enabled", lines)

    def test_55_percent_charging_with_confirmed_80_percent_stop(self):
        b = battery(battery_level=55, status="Charging",
                    power_watts=12.5, charging_stop_threshold=80,
                    charge_protection_supported=True,
                    charge_protection_enabled=True,
                    charge_control_types=END, upower_configured_end=80)
        display = battery_threshold_display(b)
        self.assertEqual(display.stop, "80% (UPower)")
        self.assertIsNone(display.charge_limit)
        lines = stats_lines(b)
        self.assertIn("Battery percentage: 55%", lines)
        self.assertIn("Battery status: Charging", lines)
        self.assertIn("Charging power: 12.50 W", lines)
        self.assertIn("Charging stop threshold: 80% (UPower)", lines)
        self.assertFalse(any("Charge protection:" in x or "Supported controls:" in x for x in lines))

    def test_80_percent_reached_preserves_reported_state(self):
        b = battery(battery_level=80, status="Pending charge",
                    charging_stop_threshold=80,
                    charge_protection_supported=True, charge_protection_enabled=True,
                    charge_control_types=END, upower_configured_end=80)
        lines = stats_lines(b)
        self.assertIn("Battery status: Pending charge", lines)
        self.assertIn("Battery percentage: 80%", lines)
        self.assertIn("Charging stop threshold: 80% (UPower)", lines)

    def test_100_percent_after_enable_does_not_discharge_itself(self):
        b = battery(battery_level=100, status="Fully charged",
                    charging_stop_threshold=80,
                    charge_protection_supported=True, charge_protection_enabled=True,
                    charge_control_types=END, upower_configured_end=80)
        lines = stats_lines(b)
        self.assertIn("Battery percentage: 100%", lines)
        self.assertIn("Battery status: Fully charged", lines)
        self.assertIn("Charging stop threshold: 80% (UPower)", lines)
        self.assertIn("Battery power: 0.00 W", lines)

    def test_mismatch_does_not_hide_kernel_reading(self):
        b = battery(charge_protection_supported=True, charge_protection_enabled=True,
                    charge_control_types=END, upower_configured_end=80)
        result = battery_threshold_display(b)
        self.assertEqual(result.stop, "100% (kernel) · 80% configured (UPower)")
        self.assertIn("Charging stop threshold: " + result.stop, stats_lines(b))

    def test_start_end_inline_when_consistent(self):
        b = battery(charging_start_threshold=75, charging_stop_threshold=80,
                    charge_protection_supported=True, charge_protection_enabled=True,
                    charge_control_types=BOTH, upower_configured_start=75,
                    upower_configured_end=80)
        result = battery_threshold_display(b)
        self.assertEqual(result.start, "75% (UPower)")
        self.assertEqual(result.stop, "80% (UPower)")
        self.assertIsNone(result.charge_limit)

    def test_missing_start_is_not_invented(self):
        b = battery(charging_stop_threshold=80,
                    charge_protection_supported=True, charge_protection_enabled=True,
                    charge_control_types=BOTH, upower_configured_start=75,
                    upower_configured_end=80)
        self.assertEqual(battery_threshold_display(b).start, "75% (UPower)")
        self.assertEqual(battery_threshold_display(b).stop, "80% (UPower)")

    def test_disabled_numeric_uses_kernel_value_and_single_description(self):
        b = battery(charge_protection_supported=True, charge_protection_enabled=False,
                    charge_control_types=END, upower_configured_end=80)
        result = battery_threshold_display(b)
        self.assertEqual(result.stop, "100%")
        self.assertEqual(result.charge_limit, "Supported (disabled)")
        self.assertIn("Charge limit: Supported (disabled)", stats_lines(b))
        self.assertNotIn("Charging stop threshold: 80% (UPower)", stats_lines(b))

    def test_unknown_enabled_does_not_assume_active(self):
        b = battery(charge_protection_supported=True, charge_protection_enabled=None,
                    charge_control_types=END, upower_configured_end=80)
        self.assertEqual(battery_threshold_display(b).charge_limit, "Supported (state unknown)")

    def test_firmware_active_disabled_unknown(self):
        for flag, expected in [(True, "Firmware managed (active)"),
                               (False, "Firmware managed (disabled)"),
                               (None, "Firmware managed (state unknown)")]:
            for percent in (55, 80, 100):
                with self.subTest(flag=flag,percent=percent):
                    b=battery(battery_level=percent, charge_protection_supported=True,
                              charge_protection_enabled=flag, charge_control_types=FIRMWARE)
                    result=battery_threshold_display(b)
                    self.assertTrue(result.firmware_only)
                    self.assertEqual(result.charge_limit, expected)
                    lines=stats_lines(b)
                    self.assertIn("Charge limit: "+expected,lines)
                    self.assertNotIn("Charging stop threshold: 100%", lines)
                    self.assertFalse(any("80% (UPower)" in x for x in lines))

    def test_firmware_plus_end_numeric_uses_numeric_if_known(self):
        b=battery(charging_stop_threshold=80, charge_protection_supported=True,
                  charge_protection_enabled=True,
                  charge_control_types=("End threshold", "Firmware managed"),
                  upower_configured_end=80)
        self.assertEqual(battery_threshold_display(b).stop,"80% (UPower)")
        self.assertFalse(battery_threshold_display(b).firmware_only)

    def test_hysteresis_does_not_change_reported_state(self):
        b=battery(status="Pending charge",battery_level=79,
                  charging_start_threshold=75,charging_stop_threshold=80,
                  charge_protection_supported=True,charge_protection_enabled=True,
                  charge_control_types=BOTH,upower_configured_start=75,upower_configured_end=80)
        self.assertIn("Battery status: Pending charge",stats_lines(b))


class GtkCompactBatteryTests(unittest.TestCase):
    """Requires Xvfb and GTK3. Never writes battery settings or launches daemon."""

    def test_preview_rows_and_visibility_through_transitions(self):
        import gi
        gi.require_version("Gtk","3.0")
        from gi.repository import Gtk
        from auto_cpufreq.gui.app import SystemReportView

        view=SystemReportView()
        window=Gtk.Window(title="Battery layout test")
        window.add(view)
        try:
            view.apply_report(report(battery()))
            window.show_all()
            self.assertEqual(view.battery_values["stop"].get_text(),"100%")
            self.assertFalse(view.charge_limit_name.get_visible())

            consistent=battery(charge_protection_supported=True,charge_protection_enabled=True,
                               charge_control_types=END,charging_stop_threshold=80,
                               upower_configured_end=80)
            view.apply_report(report(consistent))
            self.assertEqual(view.battery_values["stop"].get_text(),"80% (UPower)")
            self.assertFalse(view.charge_limit_name.get_visible())

            mismatch=replace(consistent,charging_stop_threshold=100)
            view.apply_report(report(mismatch))
            self.assertEqual(view.battery_values["stop"].get_text(),
                             "100% (kernel) · 80% configured (UPower)")

            disabled=replace(consistent,charge_protection_enabled=False)
            view.apply_report(report(disabled))
            self.assertEqual(view.battery_values["stop"].get_text(),"80%")
            self.assertTrue(view.charge_limit_name.get_visible())
            self.assertEqual(view.charge_limit_value.get_text(),"Supported (disabled)")

            firmware=replace(consistent,charge_control_types=FIRMWARE,
                             upower_configured_end=None)
            view.apply_report(report(firmware))
            self.assertFalse(view.battery_names["start"].get_visible())
            self.assertFalse(view.battery_values["stop"].get_visible())
            self.assertEqual(view.charge_limit_value.get_text(),"Firmware managed (active)")

            view.apply_report(report(battery()))
            self.assertFalse(view.charge_limit_name.get_visible())
            self.assertTrue(view.battery_names["start"].get_visible())
            self.assertTrue(view.battery_names["stop"].get_visible())
            window.show_all()
            self.assertFalse(view.charge_limit_name.get_visible())
        finally:
            window.destroy()
