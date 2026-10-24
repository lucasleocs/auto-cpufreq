"""Integrated daemon reload + backend isolation tests.

No real sysfs, UPower, systemd or charging-policy writes are performed.
"""
import configparser
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from auto_cpufreq.battery_scripts import battery
from auto_cpufreq.battery_scripts.shared import BatteryDevice
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict
from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe
from test_generic_kernel_threshold import GenericBatteryFixture


class TestDaemonReload(GenericBatteryFixture):
    def test_change_numeric_stop_applies_next_tick_without_daemon_restart(self):
        cfg=self.set_config(enabled=True,stop="80")
        controller=battery.BatteryDaemonController()
        controller.tick(now=0)
        self.assertEqual(self.read_stop(),80)
        first=controller.device
        cfg["battery"]["stop_threshold"]="75"
        controller.tick(now=1)
        self.assertEqual(self.read_stop(),75)
        self.assertIs(controller.device,first)

    def test_does_not_reapply_within_hour_without_policy_change(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        with patch.object(ctl.device,"apply_threshold_settings",
                          side_effect=AssertionError("unnecessary apply")):
            ctl.tick(now=1)
            ctl.tick(now=3599)

    def test_other_cpu_settings_do_not_reapply_battery_policy(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        cfg["charger"]={"governor":"performance"}
        with patch.object(ctl.device,"apply_threshold_settings",
                          side_effect=AssertionError("battery policy unchanged")):
            ctl.tick(now=3)

    def test_regular_hourly_revalidation_uses_same_device_instance(self):
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        old=ctl.device
        ctl.tick(now=3600)
        self.assertIs(ctl.device,old)

    def test_disable_never_resets_hardware_threshold(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        cfg["battery"]["enable_thresholds"]="false"
        with patch("auto_cpufreq.battery_scripts.battery.get_battery_device",
                   side_effect=AssertionError("disabled: don't select a writer")):
            ctl.tick(now=2)
        self.assertEqual(self.read_stop(),80)

    def test_no_initial_device_remains_recoverable_without_restart(self):
        (self.bat/"charge_control_end_threshold").unlink()
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertIsNone(ctl.device)
        (self.bat/"charge_control_end_threshold").write_text("100")
        ctl.tick(now=3600)
        self.assertIsNotNone(ctl.device)
        self.assertEqual(self.read_stop(),80)

    def test_disabled_initially_can_be_enabled_later(self):
        cfg=self.set_config(enabled=False,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertIsNone(ctl.device)
        cfg["battery"]["enable_thresholds"]="true"
        ctl.tick(now=1)
        self.assertEqual(self.read_stop(),80)

    def test_switch_numeric_to_explicit_ideapad_then_back(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        self.assertEqual(self.read_stop(),80)
        from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
        from auto_cpufreq.battery_scripts.ideapad_laptop import IdeapadBatteryDevice
        self.assertIsInstance(ctl.device,GenericKernelBatteryDevice)
        mode_path=Path(self.dir.name)/"conservation_mode"
        mode_path.write_text("0")
        with patch("auto_cpufreq.battery_scripts.battery.lsmod",
                   side_effect=lambda m:m=="ideapad_laptop"),patch(
                   "auto_cpufreq.battery_scripts.ideapad_laptop.IdeapadBatteryDevice._find_conservation_mode_path",
                   return_value=str(mode_path)):
            cfg["battery"]["ideapad_laptop_conservation_mode"]="true"
            ctl.tick(now=1)
            self.assertIsInstance(ctl.device,IdeapadBatteryDevice)
            self.assertEqual(mode_path.read_text(),"1")
            del cfg["battery"]["ideapad_laptop_conservation_mode"]
            ctl.tick(now=2)
            self.assertIsInstance(ctl.device,GenericKernelBatteryDevice)
            self.assertEqual(self.read_stop(),80)

    def test_no_tlp_bypass_when_switching_backend(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        cfg["battery"]["stop_threshold"]="75"
        with patch("auto_cpufreq.battery_scripts.shared.competing_charge_manager",
                   return_value=ChargeConflict(True,"TLP active")):
            ctl.tick(now=1)
        self.assertEqual(self.read_stop(),80)

    def test_missing_upower_after_config_change_fails_closed(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        cfg["battery"]["stop_threshold"]="75"
        with patch("auto_cpufreq.battery_scripts.priority.probe_charge_service",
                   return_value=ChargeServiceProbe("unavailable",reason="timeout")):
            ctl.tick(now=1)
        self.assertEqual(self.read_stop(),80)

    def test_drift_after_apply_survives_forced_backend_recreation(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        (self.bat/"charge_control_end_threshold").write_text("90")
        ctl._identity=None
        ctl.tick(now=3600)
        self.assertEqual(self.read_stop(),90)
        self.assertIsNotNone(ctl.device)
        self.assertEqual(len(ctl._drift_guard._suspended),1)

    def test_thread_is_started_even_when_config_disabled(self):
        cfg=self.set_config(enabled=False,stop="80")
        with patch.object(battery,"Thread") as thread:
            battery.start_battery_daemon()
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_changes_in_battery_device_option_trigger_selection(self):
        cfg=self.set_config(enabled=True,stop="80")
        ctl=battery.BatteryDaemonController()
        ctl.tick(now=0)
        cfg["battery"]["battery_device"]="BAT1"
        with patch("auto_cpufreq.battery_scripts.battery.get_battery_device",
                   wraps=battery.get_battery_device) as select:
            ctl.tick(now=1)
        select.assert_called_once()

    def test_real_config_event_reload_updates_stop_and_optout(self):
        """Exercise the production inotify handler and config parser, not mocks."""
        from auto_cpufreq.config.config import _Config
        from types import SimpleNamespace

        path = Path(self.dir.name) / "battery-policy.conf"
        path.write_text("""[battery]
enable_thresholds = true
stop_threshold = 80
""")
        local_config = _Config()
        with patch("auto_cpufreq.battery_scripts.battery.config", local_config), patch(
            "auto_cpufreq.battery_scripts.shared.config", local_config
        ):
            local_config.set_path(str(path))
            controller = battery.BatteryDaemonController()
            controller.tick(now=0)
            self.assertEqual(self.read_stop(), 80)

            path.write_text("""[battery]
enable_thresholds = true
stop_threshold = 75
""")
            local_config.config_handler.process_IN_MODIFY(
                SimpleNamespace(pathname=str(path)))
            controller.tick(now=1)
            self.assertEqual(self.read_stop(), 75)

            path.write_text("""[battery]
enable_thresholds = false
stop_threshold = 100
""")
            local_config.config_handler.process_IN_MODIFY(
                SimpleNamespace(pathname=str(path)))
            controller.tick(now=2)
            self.assertEqual(self.read_stop(), 75)

    def test_failed_partial_legacy_write_is_suspended_on_following_poll(self):
        (self.bat/"charge_control_start_threshold").write_text("80")
        (self.bat/"charge_control_end_threshold").write_text("90")
        cfg=self.set_config(enabled=True,start="50",stop="70")
        legacy=BatteryDevice()
        ctl=battery.BatteryDaemonController()
        with patch("auto_cpufreq.battery_scripts.battery.get_battery_device",
                   return_value=legacy):
            # The second step fails and the attempted restoration fails.
            calls=[0]
            def failed_transaction(path,value):
                calls[0]+=1
                if calls[0] == 1:
                    Path(path).write_text(str(value))
                    return True
                return False
            with patch.object(legacy,"_write_value_to_file",
                              side_effect=failed_transaction):
                ctl.tick(now=0)
                self.assertEqual(calls[0],3)
                self.assertEqual((self.bat/"charge_control_start_threshold").read_text(),"50")
                ctl.tick(now=3600)
                self.assertEqual(calls[0],3)
                cfg["battery"]["start_threshold"]="45"
                ctl.tick(now=3601)
                self.assertGreater(calls[0],3)


if __name__=="__main__":
    import unittest
    unittest.main()
