"""Read-only frontend contracts for the UPower telemetry integration."""

from dataclasses import replace
import unittest

import urwid

from auto_cpufreq.modules.battery_upower import _decode
from auto_cpufreq.modules.system_info import BatteryInfo, CoreInfo, SystemReport
from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType


def _battery(**changes):
    return replace(BatteryInfo(
        is_charging=False,
        is_ac_plugged=True,
        charging_start_threshold=None,
        charging_stop_threshold=100,
        battery_level=99,
        power_watts=0.0,
        status="Fully charged",
        source="upower",
    ), **changes)


def _report(battery):
    return SystemReport(
        distro_name="Test Linux",
        distro_ver="1",
        arch="x86_64",
        processor_model="Test CPU",
        total_core=1,
        kernel_version="6.0-test",
        current_gov="powersave",
        current_epp=None,
        current_epb=None,
        current_hwp_dynamic_boost=None,
        cpu_driver="test",
        cpu_fan_speed=None,
        cpu_usage=0.0,
        cpu_max_freq=2000,
        cpu_min_freq=400,
        load=0.0,
        avg_load=(0.0, 0.0, 0.0),
        cores_info=[CoreInfo(id=0, usage=0.0, temperature=35.0, frequency=800.0)],
        battery_info=battery,
        is_turbo_on=(False, False),
    )


def _text(walker):
    labels = []
    for item in walker:
        widget = item.original_widget if isinstance(item, urwid.AttrMap) else item
        labels.append(widget.get_text()[0])
    return "\n".join(labels)


class StatsFrontendTests(unittest.TestCase):
    def setUp(self):
        self.monitor = SystemMonitor(type=ViewType.STATS)

    def test_old_upower_renders_existing_fields_only(self):
        self.monitor.format_system_info(_report(_battery()))
        text = _text(self.monitor.right_content)
        self.assertIn("Battery status: Fully charged", text)
        self.assertIn("Battery percentage: 99%", text)
        self.assertIn("AC plugged: Yes", text)
        self.assertIn("Charging stop threshold: 100", text)
        self.assertIn("Battery power: 0.00 W", text)
        self.assertNotIn("Charge protection:", text)
        self.assertNotIn("Supported controls:", text)

    def test_new_upower_renders_protection_capabilities(self):
        b = _battery(
            charge_protection_supported=True,
            charge_protection_enabled=True,
            charge_control_types=("End threshold",),
        )
        self.monitor.format_system_info(_report(b))
        text = _text(self.monitor.right_content)
        self.assertIn("Charge protection: Enabled", text)
        self.assertIn("Supported controls: End threshold", text)
        self.assertNotIn("Supported percentages", text)
        self.assertNotIn("Select threshold", text)

    def test_firmware_control_no_invented_limit(self):
        upower = _decode({
            "Type": 2, "PowerSupply": True, "IsPresent": True, "NativePath": "BAT1",
            "State": 5, "Percentage": 70.0, "EnergyRate": 0.0,
            "ChargeThresholdSupported": True,
            "ChargeThresholdEnabled": True,
            "ChargeThresholdSettingsSupported": 4,
            "ChargeStartThreshold": 4294967295,
            "ChargeEndThreshold": 4294967295,
        }, "/sys/class/power_supply/BAT1")
        self.assertEqual(upower.charge_control_types, ("Firmware managed",))
        b = _battery(
            status=upower.status, battery_level=upower.percentage,
            charge_protection_supported=upower.charge_protection_supported,
            charge_protection_enabled=upower.charge_protection_enabled,
            charge_control_types=upower.charge_control_types,
            charging_stop_threshold=None,
        )
        self.monitor.format_system_info(_report(b))
        text = _text(self.monitor.right_content)
        self.assertIn("Charge protection: Enabled", text)
        self.assertIn("Supported controls: Firmware managed", text)
        self.assertNotIn("4294967295", text)
        self.assertNotIn("80%", text)

    def test_capabilities_do_not_leak_between_refreshes(self):
        modern = _battery(charge_protection_supported=True, charge_protection_enabled=False,
                          charge_control_types=("Start threshold", "End threshold"))
        self.monitor.format_system_info(_report(modern))
        self.assertIn("Supported controls: Start threshold, End threshold", _text(self.monitor.right_content))
        self.monitor.format_system_info(_report(_battery()))
        self.assertNotIn("Supported controls:", _text(self.monitor.right_content))


class GtkFrontendTests(unittest.TestCase):
    """Run under xvfb-run with distro-provided PyGObject; never start the daemon."""

    def test_optional_rows_follow_capability_after_show_all(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        from auto_cpufreq.gui.app import SystemReportView

        view = SystemReportView()
        window = Gtk.Window(title="Battery read-only test")
        window.add(view)
        try:
            view.apply_report(_report(_battery()))
            window.show_all()
            self.assertEqual(view.battery_values["status"].get_text(), "Fully charged")
            self.assertEqual(view.battery_values["power"].get_text(), "0.00 W")
            for key in ("protection", "controls"):
                self.assertFalse(view.battery_capability_names[key].get_visible())
                self.assertFalse(view.battery_capability_values[key].get_visible())

            modern = _battery(charge_protection_supported=True,
                              charge_protection_enabled=True,
                              charge_control_types=("End threshold",))
            view.apply_report(_report(modern))
            self.assertEqual(view.battery_capability_values["protection"].get_text(), "Enabled")
            self.assertEqual(view.battery_capability_values["controls"].get_text(), "End threshold")
            for key in ("protection", "controls"):
                self.assertTrue(view.battery_capability_names[key].get_visible())
                self.assertTrue(view.battery_capability_values[key].get_visible())

            view.apply_report(_report(_battery()))
            for key in ("protection", "controls"):
                self.assertFalse(view.battery_capability_names[key].get_visible())
                self.assertFalse(view.battery_capability_values[key].get_visible())
        finally:
            window.destroy()
