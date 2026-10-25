"""Never writes physical sysfs; tests drift guard and TLP checks."""
import subprocess
from unittest.mock import patch
from types import SimpleNamespace

from auto_cpufreq.battery_scripts.conflicts import (
    ChargeConflict, ChargeDriftGuard, competing_charge_manager,
)
from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe, ChargeCapabilities
from test_generic_kernel_threshold import GenericBatteryFixture


class ChargeConflictTests(GenericBatteryFixture):
    def test_installed_but_inactive_tlp_does_not_block(self):
        with patch("auto_cpufreq.battery_scripts.conflicts.which", return_value=None), \
             patch("auto_cpufreq.battery_scripts.conflicts.subprocess.run",
                   return_value=SimpleNamespace(returncode=3)):
            self.assertFalse(competing_charge_manager().blocked)

    def test_running_tlp_blocks(self):
        with patch("auto_cpufreq.battery_scripts.conflicts.which", return_value=None), \
             patch("auto_cpufreq.battery_scripts.conflicts.subprocess.run",
                   return_value=SimpleNamespace(returncode=0)):
            self.assertTrue(competing_charge_manager().blocked)

    def test_tlp_probe_failure_does_not_assert_tlp_active(self):
        with patch("auto_cpufreq.battery_scripts.conflicts.subprocess.run",
                   side_effect=FileNotFoundError()):
            self.assertFalse(competing_charge_manager().blocked)

    def test_integration_active_tlp_never_writes(self):
        dev=self.device()
        with patch("auto_cpufreq.battery_scripts.shared.competing_charge_manager",
                   return_value=ChargeConflict(True, "TLP active")),patch.object(
                   dev,"_write_value_to_file",side_effect=AssertionError("must not write")):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(),100)

    def test_track_our_success_and_suspend_after_external_change(self):
        dev=self.device()
        with patch("auto_cpufreq.battery_scripts.priority.probe_charge_service",
                   return_value=ChargeServiceProbe("absent")):
            dev.apply_threshold_settings()
            self.assertEqual(self.read_stop(),80)
            (self.bat/"charge_control_end_threshold").write_text("90")
            with patch.object(dev,"_write_value_to_file",
                              side_effect=AssertionError("must not fight external writer")):
                dev.apply_threshold_settings()
                dev.apply_threshold_settings()
            self.assertEqual(self.read_stop(),90)

    def test_explicit_config_change_lifts_drift_suspension(self):
        guard=ChargeDriftGuard()
        guard.record("BAT1",(None,80,None),(None,80))
        self.assertTrue(guard.ready("BAT1",(None,80,None),(None,90)).blocked)
        self.assertTrue(guard.ready("BAT1",(None,80,None),(None,90)).blocked)
        self.assertFalse(guard.ready("BAT1",(None,75,None),(None,90)).blocked)

    def test_rounding_consistent_readback_is_not_drift(self):
        guard=ChargeDriftGuard()
        guard.record("BAT1",(None,79,None),(None,80))
        self.assertFalse(guard.ready("BAT1",(None,79,None),(None,80)).blocked)

    def test_disabled_config_never_checks_active_tlp(self):
        dev=self.device()
        cfg=self.set_config(enabled=False)
        with patch("auto_cpufreq.battery_scripts.shared.competing_charge_manager",
                   side_effect=AssertionError("not called")):
            dev.apply_threshold_settings()
        self.assertEqual(self.read_stop(),100)
