"""UPower-first charging priority tests against temporary kernel files.

No actual sysfs or firmware writes. These tests exercise the daemon-facing
BatteryDevice.apply_threshold_settings() path and the pure resolver.
"""
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.battery_scripts.priority import resolve_charge_control
from auto_cpufreq.modules.battery_upower import UPowerBattery
from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe, ChargeCapabilities
from test_generic_kernel_threshold import GenericBatteryFixture


def upower(*, supported=True, enabled=True, kinds=("End threshold",),
           end=80, start=None):
    return UPowerBattery(
        status="Charging", percentage=55, power_watts=12,
        charge_protection_supported=supported,
        charge_protection_enabled=enabled,
        charge_control_types=kinds,
        configured_end_threshold=end,
        configured_start_threshold=start,
    )


class PriorityResolverTests(GenericBatteryFixture):
    def _upower(self, value):
        if value is None:
            probe = ChargeServiceProbe("unavailable", reason="UPower unavailable")
        else:
            probe = ChargeServiceProbe("ready", ChargeCapabilities(
                charge_protection_supported=value.charge_protection_supported,
                charge_protection_enabled=value.charge_protection_enabled,
                charge_control_types=value.charge_control_types,
                configured_start_threshold=value.configured_start_threshold,
                configured_end_threshold=value.configured_end_threshold,
            ))
        return patch("auto_cpufreq.battery_scripts.priority.probe_charge_service",
                     return_value=probe)

    def _apply_without_writes(self, snapshot):
        dev = self.device()
        with self._upower(snapshot):
            with patch.object(
                dev, "_write_value_to_file",
                side_effect=AssertionError("controller must not write")
            ):
                dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)

    def test_upower_absent_api_falls_back_to_standard_kernel(self):
        dev = self.device()
        with self._upower(upower(supported=False, enabled=None, kinds=(), end=None)):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 80)

    def test_upower_disabled_falls_back_to_standard_kernel_with_optin(self):
        dev = self.device()
        with self._upower(upower(enabled=False)):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 80)

    def test_upower_service_unavailable_fails_closed(self):
        self._apply_without_writes(None)
        with self._upower(None):
            decision = resolve_charge_control(str(self.bat), 80)
        self.assertEqual(decision.action, "blocked")
        self.assertIn("unavailable", decision.reason)

    def test_upower_enabled_state_unknown_fails_closed(self):
        self._apply_without_writes(upower(enabled=None))
        with self._upower(upower(enabled=None)):
            decision = resolve_charge_control(str(self.bat), 80)
        self.assertEqual(decision.action, "blocked")
        self.assertIn("unknown", decision.reason)

    def test_upower_active_conflicting_80_does_not_override_kernel_100(self):
        self._apply_without_writes(upower(enabled=True, end=80))
        with self._upower(upower(enabled=True, end=80)):
            self.assertEqual(
                resolve_charge_control(str(self.bat), 80).action, "blocked")

    def test_upower_active_matching_and_confirmed_is_satisfied_without_write(self):
        (self.bat / "charge_control_end_threshold").write_text("80")
        dev = self.device()
        with self._upower(upower(enabled=True, end=80)):
            with patch.object(dev, "_write_value_to_file",
                              side_effect=AssertionError("unnecessary write")):
                dev.apply_threshold_settings()
            result = resolve_charge_control(str(self.bat), 80)
        self.assertEqual(result.action, "satisfied")
        self.assertEqual(self.read_stop(), 80)

    def test_upower_active_matching_preset_but_unknown_readback_blocks(self):
        (self.bat / "charge_control_end_threshold").write_text("invalid")
        with self._upower(upower(enabled=True, end=80)):
            decision = resolve_charge_control(str(self.bat), 80)
        self.assertEqual(decision.action, "blocked")

    def test_upower_firmware_only_active_blocks_unconfigured_numeric_target(self):
        snapshot = upower(enabled=True, kinds=("Firmware managed",), end=None)
        self._apply_without_writes(snapshot)

    def test_upower_firmware_disabled_allows_explicit_kernel_stop(self):
        dev = self.device()
        with self._upower(upower(enabled=False, kinds=("Firmware managed",), end=None)):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 80)

    def test_upower_start_and_end_matching_are_satisfied(self):
        (self.bat / "charge_control_start_threshold").write_text("75")
        (self.bat / "charge_control_end_threshold").write_text("80")
        snapshot = upower(kinds=("Start threshold", "End threshold"),
                          start=75, end=80)
        with self._upower(snapshot):
            self.assertEqual(resolve_charge_control(
                str(self.bat), 80, 75).action, "satisfied")
            self.assertEqual(resolve_charge_control(
                str(self.bat), 80, 70).action, "blocked")

    def test_upower_active_wrong_preset_even_if_kernel_stop_matches_blocks(self):
        (self.bat / "charge_control_end_threshold").write_text("80")
        with self._upower(upower(end=90)):
            self.assertEqual(resolve_charge_control(
                str(self.bat), 80).action, "blocked")

    def test_numeric_kernel_selected_ahead_of_vendor_if_supported(self):
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name == "asus_wmi"):
            self.assertIsInstance(battery.get_battery_device(),
                                  GenericKernelBatteryDevice)

    def test_vendor_fallback_when_standard_kernel_attribute_missing(self):
        (self.bat / "charge_control_end_threshold").unlink()
        (self.bat / "charge_stop_threshold").write_text("100")
        (self.bat / "charge_start_threshold").write_text("75")
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name == "asus_wmi"):
            dev = battery.get_battery_device()
        self.assertIsInstance(dev, battery.AsusBatteryDevice)
        cfg = self.set_config(enabled=True, start="75", stop="80")
        snapshot = upower(enabled=True, end=80)
        with self._upower(snapshot):
            with patch.object(dev, "_write_value_to_file",
                              side_effect=AssertionError("vendor attempted write")):
                dev.apply_threshold_settings()
        self.assertEqual((self.bat / "charge_stop_threshold").read_text(), "100")

    def test_explicit_ideapad_conservation_prefers_vendor(self):
        cfg = self.set_config(enabled=True, start=None, stop=None)
        cfg["battery"]["ideapad_laptop_conservation_mode"] = "true"
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name == "ideapad_laptop"):
            self.assertIsInstance(
                battery.get_battery_device(), battery.IdeapadBatteryDevice)

    def test_explicit_conservation_with_multiple_lenovo_drivers_is_not_generic(self):
        cfg = self.set_config(enabled=True, start=None, stop=None)
        cfg["battery"]["ideapad_laptop_conservation_mode"] = "true"
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda name: name in ("ideapad_acpi", "ideapad_laptop")):
            self.assertIsInstance(
                battery.get_battery_device(), battery.IdeapadBatteryDevice)

    def test_firmware_ownership_unknown_blocks_conservation_action(self):
        with self._upower(upower(enabled=None, kinds=("Firmware managed",), end=None)):
            self.assertEqual(resolve_charge_control(
                str(self.bat), 100, firmware_operation=True).action, "blocked")

    def test_firmware_requested_when_upower_active_stays_blocked_even_if_stop_matches(self):
        (self.bat / "charge_control_end_threshold").write_text("80")
        with self._upower(upower(enabled=True, end=80)):
            result = resolve_charge_control(
                str(self.bat), 80, firmware_operation=True)
        self.assertEqual(result.action, "blocked")

    def test_no_standard_kernel_no_vendor_is_unsupported(self):
        (self.bat / "charge_control_end_threshold").unlink()
        self.assertIsNone(battery.get_battery_device())

    def test_no_alternate_writer_after_kernel_write_failure(self):
        dev = self.device()
        with self._upower(upower(supported=False, enabled=None)):
            with patch.object(dev, "_write_value_to_file", return_value=False):
                dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)

    def test_disabling_all_thresholds_blocks_preflight_calls(self):
        dev = self.device()
        cfg = self.set_config(enabled=False)
        with patch("auto_cpufreq.battery_scripts.priority.probe_charge_service",
                   side_effect=AssertionError("unexpected UPower call")):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(), 100)
