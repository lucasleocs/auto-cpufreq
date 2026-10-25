"""Lab-only tests for ABI consistency, conflict diagnostics, and retry classification."""
import configparser
from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.conflicts import (
    competing_charge_manager, ChargeConflict,
)
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.battery_scripts.probe import ChargeCapabilities, ChargeServiceProbe
from auto_cpufreq.battery_scripts.shared import (
    BatteryApplyResult, writable_threshold_file,
)


class ChargeRefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/"power_supply"
        self.root.mkdir()
        self.bat = self.root/"BAT1"
        self.bat.mkdir()
        (self.bat/"type").write_text("Battery")
        (self.bat/"scope").write_text("System")
        (self.bat/"charge_control_end_threshold").write_text("100")
        self.config = configparser.ConfigParser()
        self.config["battery"] = {
            "enable_thresholds": "true", "stop_threshold": "80",
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (
            "auto_cpufreq.battery_scripts.shared",
            "auto_cpufreq.battery_scripts.generic",
            "auto_cpufreq.modules.system_info",
        ):
            self.stack.enter_context(patch(
                module + ".POWER_SUPPLY_DIR", str(self.root)
            ))
        self.stack.enter_context(patch.object(
            battery.config, "get_config", return_value=self.config
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.competing_charge_manager",
            return_value=ChargeConflict(False)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe("ready", ChargeCapabilities(False))
        ))
        self.stack.enter_context(patch.object(battery, "lsmod", return_value=False))

    def value(self):
        return (self.bat/"charge_control_end_threshold").read_text().strip()

    def test_standard_writable_preferred_to_legacy(self):
        (self.bat/"charge_stop_threshold").write_text("99")
        with patch.object(battery, "lsmod",
                          side_effect=lambda name:name=="asus_wmi"):
            device = battery.get_battery_device()
        self.assertIsInstance(device, GenericKernelBatteryDevice)
        self.assertEqual(
            device.stop_paths["BAT1"],
            str(self.bat/"charge_control_end_threshold")
        )
        self.assertEqual(
            writable_threshold_file("BAT1", ("charge_control_end_threshold",)),
            str(self.bat/"charge_control_end_threshold")
        )

    def test_nonwritable_standard_never_selects_generic_legacy_fallback(self):
        (self.bat/"charge_stop_threshold").write_text("100")
        (self.bat/"charge_start_threshold").write_text("65")
        orig = os.access
        def access(path, mode):
            if os.fspath(path) == str(self.bat/"charge_control_end_threshold"):
                return False
            return orig(path, mode)
        with patch("auto_cpufreq.battery_scripts.shared.os.access", side_effect=access):
            direct = GenericKernelBatteryDevice(self.config)
            self.assertNotIn("BAT1", direct.stop_paths)
            with patch.object(battery, "lsmod",
                              side_effect=lambda name:name=="asus_wmi"):
                selected = battery.get_battery_device()
        self.assertIsInstance(selected, battery.AsusBatteryDevice)
        self.assertEqual(
            selected.stop_paths["BAT1"], str(self.bat/"charge_stop_threshold")
        )

    def test_generic_never_mixes_legacy_start_into_standard_pair(self):
        (self.bat/"charge_stop_threshold").write_text("90")
        (self.bat/"charge_start_threshold").write_text("60")
        device = GenericKernelBatteryDevice(self.config)
        self.assertNotIn("BAT1", device.start_paths)
        self.assertEqual(
            device.stop_paths["BAT1"],
            str(self.bat/"charge_control_end_threshold")
        )

    def test_invalid_policy_is_not_retried(self):
        self.config["battery"]["stop_threshold"] = "garbage"
        device = battery.get_battery_device()
        self.assertEqual(
            device.apply_threshold_settings(self.config), BatteryApplyResult.INVALID
        )
        ctl = battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertIsNone(ctl._retry_at)
        self.assertEqual(self.value(), "100")

    def test_success_and_unchanged_are_distinct(self):
        dev = battery.get_battery_device()
        self.assertEqual(
            dev.apply_threshold_settings(self.config), BatteryApplyResult.APPLIED
        )
        self.assertEqual(self.value(), "80")
        self.assertEqual(
            dev.apply_threshold_settings(self.config), BatteryApplyResult.SATISFIED
        )

    def test_transient_upower_unknown_is_retryable(self):
        from auto_cpufreq.battery_scripts import priority
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe(
                              "unavailable", reason="connection lost")):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
        self.assertEqual(ctl._retry_at, battery.BATTERY_RETRY_INTERVAL)
        self.assertEqual(self.value(), "100")

    def test_active_upower_mismatch_blocks_without_short_retry(self):
        from auto_cpufreq.battery_scripts import priority
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe(
                              "ready", ChargeCapabilities(
                                  True, charge_protection_enabled=True,
                                  charge_control_types=("End threshold",),
                                  configured_end_threshold=90,
                              ))):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
        self.assertIsNone(ctl._retry_at)
        self.assertEqual(self.value(), "100")

    def test_tlp_active_blocks_without_short_retry(self):
        with patch("auto_cpufreq.battery_scripts.shared.competing_charge_manager",
                   return_value=ChargeConflict(True, "active", "active")):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
        self.assertIsNone(ctl._retry_at)
        self.assertEqual(self.value(), "100")

    def test_incomplete_upower_enabled_state_retries(self):
        from auto_cpufreq.battery_scripts import priority
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe(
                              "ready", ChargeCapabilities(
                                  True, charge_protection_enabled=None,
                              ))):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
        self.assertEqual(ctl._retry_at, battery.BATTERY_RETRY_INTERVAL)

    def test_tlp_status_codes_have_distinct_diagnostics(self):
        for code, state, blocked in (
            (0,"active",True), (3,"unknown",False),
            (4,"absent",False), (1,"unknown",False),
        ):
            with self.subTest(code=code), patch(
                "auto_cpufreq.battery_scripts.conflicts.which", return_value=None
            ), patch(
                "auto_cpufreq.battery_scripts.conflicts.subprocess.run",
                return_value=SimpleNamespace(returncode=code)
            ):
                result=competing_charge_manager()
                self.assertEqual(result.status, state)
                self.assertEqual(result.blocked, blocked)
        with patch(
            "auto_cpufreq.battery_scripts.conflicts.subprocess.run",
            side_effect=OSError("no systemd")
        ):
            self.assertEqual(competing_charge_manager().status, "unknown")

    def test_indeterminate_tlp_does_not_disable_explicit_policy(self):
        with patch("auto_cpufreq.battery_scripts.shared.competing_charge_manager",
                   return_value=ChargeConflict(False, "systemctl unavailable", "unknown")):
            dev = battery.get_battery_device()
            result = dev.apply_threshold_settings(self.config)
        self.assertEqual(result, BatteryApplyResult.APPLIED)
        self.assertEqual(self.value(), "80")


if __name__ == "__main__":
    unittest.main()
