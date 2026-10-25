"""Policy safety tests on isolated fake power_supply trees. No physical writes."""
import configparser
from contextlib import ExitStack
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict
from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe, ChargeCapabilities
from auto_cpufreq.modules.system_info import SystemInfo


class ChargePolicySafety(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "power_supply"
        self.root.mkdir()
        for name in ("BAT0", "BAT1"):
            p = self.root / name
            p.mkdir()
            (p / "type").write_text("Battery")
            (p / "scope").write_text("System")
            (p / "charge_control_end_threshold").write_text("100")
        self.cfg = configparser.ConfigParser()
        self.cfg["battery"] = {"enable_thresholds": "true", "stop_threshold": "80"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module in (
            "auto_cpufreq.modules.system_info",
            "auto_cpufreq.battery_scripts.shared",
            "auto_cpufreq.battery_scripts.generic",
        ):
            self.stack.enter_context(
                patch(module + ".POWER_SUPPLY_DIR", str(self.root))
            )
        self.stack.enter_context(patch.object(
            battery.config, "get_config", return_value=self.cfg
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.shared.competing_charge_manager",
            return_value=ChargeConflict(False)
        ))
        self.stack.enter_context(patch(
            "auto_cpufreq.battery_scripts.priority.probe_charge_service",
            return_value=ChargeServiceProbe(
                "ready", ChargeCapabilities(charge_protection_supported=False)
            )
        ))
        self.stack.enter_context(patch.object(battery, "lsmod", return_value=False))

    def read(self, name):
        return int((self.root / name / "charge_control_end_threshold").read_text())

    def test_invalid_explicit_target_never_falls_back_to_bat0(self):
        self.cfg["battery"]["battery_device"] = "BAT9"
        self.assertIsNone(SystemInfo.get_battery_path(
            self.cfg, strict_configured=True
        ))
        self.assertEqual(
            SystemInfo.get_battery_path(self.cfg),
            str(self.root / "BAT0"),
        )
        ctl = battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertEqual((self.read("BAT0"), self.read("BAT1")), (100, 100))
        self.assertIsNone(ctl.device)

    def test_rejects_invalid_explicit_paths_and_peripherals(self):
        for name in ("../BAT0", "/tmp/BAT0", ".", "", "BAT9"):
            with self.subTest(name=name):
                self.cfg["battery"]["battery_device"] = name
                self.assertIsNone(SystemInfo.get_battery_path(
                    self.cfg, strict_configured=True
                ))
                self.assertIsNone(battery.get_battery_device())
        self.cfg["battery"]["battery_device"] = "BAT1"
        (self.root / "BAT1" / "scope").write_text("Device")
        self.assertIsNone(SystemInfo.get_battery_path(
            self.cfg, strict_configured=True
        ))
        self.assertIsNone(battery.get_battery_device())

    def test_selected_generic_battery_writes_only_bat1(self):
        self.cfg["battery"]["battery_device"] = "BAT1"
        ctl = battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertEqual((self.read("BAT0"), self.read("BAT1")), (100, 80))
        self.assertEqual(ctl.device.batteries, ["BAT1"])

    def test_selected_vendor_backend_writes_only_bat1(self):
        self.cfg["battery"]["battery_device"] = "BAT1"
        for name in ("BAT0", "BAT1"):
            (self.root / name / "charge_control_end_threshold").unlink()
            (self.root / name / "charge_stop_threshold").write_text("100")
            (self.root / name / "charge_start_threshold").write_text("70")
        self.cfg["battery"]["start_threshold"] = "70"
        with patch.object(battery, "lsmod",
                          side_effect=lambda m: m == "asus_wmi"):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
        self.assertIsInstance(ctl.device, battery.AsusBatteryDevice)
        self.assertEqual(ctl.device.batteries, ["BAT1"])
        self.assertEqual(
            (self.root / "BAT0" / "charge_stop_threshold").read_text(), "100"
        )
        self.assertEqual(
            (self.root / "BAT1" / "charge_stop_threshold").read_text(), "80"
        )

    def test_legacy_vendor_without_explicit_target_keeps_multibattery(self):
        for name in ("BAT0", "BAT1"):
            (self.root / name / "charge_control_end_threshold").unlink()
            (self.root / name / "charge_stop_threshold").write_text("100")
            (self.root / name / "charge_start_threshold").write_text("70")
        self.cfg["battery"]["start_threshold"] = "70"
        with patch.object(battery, "lsmod",
                          side_effect=lambda m: m == "asus_wmi"):
            dev = battery.get_battery_device()
        self.assertEqual(dev.batteries, ["BAT0", "BAT1"])

    def test_ignore_list_change_triggers_immediate_new_selection(self):
        ctl = battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertEqual((self.read("BAT0"), self.read("BAT1")), (80, 100))
        self.cfg["power_supply_ignore_list"] = {"peripheral": "BAT0"}
        ctl.tick(now=1)
        self.assertEqual((self.read("BAT0"), self.read("BAT1")), (80, 80))
        self.assertEqual(ctl.device.batteries, ["BAT1"])

    def test_transient_upower_retry_is_bounded_and_recovers(self):
        import auto_cpufreq.battery_scripts.priority as priority
        state = {"ready": False, "calls": 0}
        def lookup(_path):
            state["calls"] += 1
            if not state["ready"]:
                return ChargeServiceProbe("unavailable", reason="timeout")
            return ChargeServiceProbe(
                "ready", ChargeCapabilities(charge_protection_supported=False)
            )
        with patch.object(priority, "probe_charge_service",
                          side_effect=lookup):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
            self.assertEqual(self.read("BAT0"), 100)
            self.assertEqual(ctl._retry_at, battery.BATTERY_RETRY_INTERVAL)
            state["ready"] = True
            ctl.tick(now=299)
            self.assertEqual(self.read("BAT0"), 100)
            self.assertEqual(state["calls"], 1)
            ctl.tick(now=300)
            self.assertEqual(self.read("BAT0"), 80)
            self.assertIsNone(ctl._retry_at)
            ctl.tick(now=301)
            self.assertEqual(state["calls"], 2)

    def test_missing_device_retry_does_not_wait_one_hour(self):
        for name in ("BAT0", "BAT1"):
            (self.root / name / "charge_control_end_threshold").unlink()
        ctl = battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertIsNone(ctl.device)
        (self.root / "BAT0" / "charge_control_end_threshold").write_text("100")
        ctl.tick(now=299)
        self.assertIsNone(ctl.device)
        ctl.tick(now=300)
        self.assertEqual(self.read("BAT0"), 80)

    def test_disabling_during_retry_performs_no_writes(self):
        from auto_cpufreq.battery_scripts import priority
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe("unavailable")):
            ctl = battery.BatteryDaemonController()
            ctl.tick(now=0)
            self.cfg["battery"]["enable_thresholds"] = "false"
            ctl.tick(now=1)
        self.assertIsNone(ctl._retry_at)
        self.assertEqual((self.read("BAT0"), self.read("BAT1")), (100, 100))

    def test_vendor_respects_ignore_list_without_explicit_target(self):
        for name in ("BAT0", "BAT1"):
            (self.root / name / "charge_control_end_threshold").unlink()
            (self.root / name / "charge_stop_threshold").write_text("100")
            (self.root / name / "charge_start_threshold").write_text("70")
        self.cfg["power_supply_ignore_list"] = {"exclude": "BAT0"}
        with patch.object(battery, "lsmod",
                          side_effect=lambda m: m == "asus_wmi"):
            dev = battery.get_battery_device()
        self.assertEqual(dev.batteries, ["BAT1"])

    def test_snap_upower_observe_is_read_only_plug_on_all_apps(self):
        snap = (Path(__file__).parents[1] / "snap" / "snapcraft.yaml").read_text()
        for app, next_app in (
            ("auto-cpufreq:", "auto-cpufreq-gtk:"),
            ("auto-cpufreq-gtk:", "service:"),
            ("service:", None),
        ):
            with self.subTest(app=app):
                start = snap.index("  " + app, snap.index("\napps:"))
                stop = snap.index("  " + next_app, start) if next_app else len(snap)
                self.assertIn("      - upower-observe", snap[start:stop])
        self.assertNotIn("power-control", snap)


if __name__ == "__main__":
    unittest.main()
