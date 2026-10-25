"""Systemd-state preservation tests; never call the host's systemctl."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_cpufreq import power_service_state as state


class SystemdSimulator:
    def __init__(self):
        self.units = {
            "power-profiles-daemon.service": dict(unit_file="enabled", active=True),
            "tuned.service": dict(unit_file="disabled", active=False),
            "tuned-ppd.service": None,
        }
        self.commands = []
        self.fail_once = None

    def __call__(self, command, **kwargs):
        assert command[0] == "systemctl", command
        args = command[1:]
        self.commands.append(args)
        action = args[0]
        unit = args[1]
        if action == "show":
            info = self.units.get(unit)
            if info is None:
                return subprocess.CompletedProcess(
                    command, 0, stdout="LoadState=not-found\nActiveState=inactive\nUnitFileState=\n", stderr=""
                )
            loaded = "masked" if info["unit_file"] == "masked" else "loaded"
            return subprocess.CompletedProcess(
                command, 0,
                stdout=f"LoadState={loaded}\nActiveState={'active' if info['active'] else 'inactive'}\nUnitFileState={info['unit_file']}\n",
                stderr="",
            )
        if self.fail_once == (action, unit):
            self.fail_once = None
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="simulated failure")
        info = self.units[unit]
        if action == "disable":
            info["unit_file"] = "disabled"
        elif action == "enable":
            info["unit_file"] = "enabled"
        elif action == "mask":
            info["unit_file"] = "masked"
        elif action == "unmask":
            info["unit_file"] = "disabled"
        elif action == "stop":
            info["active"] = False
        elif action == "start":
            info["active"] = True
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class PowerServiceStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "power-services.json"
        self.sim = SystemdSimulator()
        self.manager = state.PowerServiceState(self.file, runner=self.sim)
        use_systemd = patch.object(state, "which", return_value="/usr/bin/systemctl")
        use_systemd.start()
        self.addCleanup(use_systemd.stop)

    def test_restore_original_enabled_active_and_disabled_inactive(self):
        self.assertTrue(self.manager.disable())
        self.assertEqual(self.sim.units["power-profiles-daemon.service"]["unit_file"], "masked")
        self.assertEqual(self.sim.units["tuned.service"]["unit_file"], "masked")
        self.assertTrue(self.manager.restore())
        self.assertFalse(self.file.exists())
        self.assertEqual(self.sim.units["power-profiles-daemon.service"], {"unit_file": "enabled", "active": True})
        self.assertEqual(self.sim.units["tuned.service"], {"unit_file": "disabled", "active": False})

    def test_no_snapshot_means_no_changes_to_services(self):
        self.assertTrue(self.manager.restore())
        self.assertEqual(self.sim.commands, [])
        self.assertEqual(self.sim.units["tuned.service"]["unit_file"], "disabled")

    def test_preexisting_mask_is_not_managed(self):
        self.sim.units["tuned.service"]["unit_file"] = "masked"
        self.assertTrue(self.manager.disable())
        snapshot = json.loads(self.file.read_text())
        self.assertNotIn("tuned.service", snapshot["services"])
        self.assertTrue(self.manager.restore())
        self.assertEqual(self.sim.units["tuned.service"]["unit_file"], "masked")

    def test_external_change_is_not_overwritten(self):
        self.assertTrue(self.manager.disable())
        self.sim.units["tuned.service"]["unit_file"] = "enabled"
        self.assertFalse(self.manager.restore())
        self.assertEqual(self.sim.units["tuned.service"]["unit_file"], "enabled")
        self.assertIn("tuned.service", json.loads(self.file.read_text())["services"])

    def test_failed_stop_rolls_back_partial_install(self):
        self.sim.fail_once = ("stop", "power-profiles-daemon.service")
        self.assertFalse(self.manager.disable())
        self.assertEqual(self.sim.units["power-profiles-daemon.service"], {"unit_file": "enabled", "active": True})
        self.assertFalse(self.file.exists())

    def test_interrupted_restore_can_resume_after_unmask(self):
        self.assertTrue(self.manager.disable())
        self.sim.fail_once = ("enable", "power-profiles-daemon.service")
        self.assertFalse(self.manager.restore())
        record = json.loads(self.file.read_text())["services"]
        self.assertTrue(record["power-profiles-daemon.service"]["restoring"])
        self.assertTrue(self.manager.restore())
        self.assertFalse(self.file.exists())
        self.assertEqual(
            self.sim.units["power-profiles-daemon.service"],
            {"unit_file": "enabled", "active": True},
        )

    def test_repeated_disable_keeps_first_snapshot(self):
        self.assertTrue(self.manager.disable())
        first = self.file.read_bytes()
        self.assertTrue(self.manager.disable())
        self.assertEqual(self.file.read_bytes(), first)

    def test_untrusted_symlink_is_not_loaded(self):
        self.file.symlink_to(Path(self.temp.name) / "not-real.json")
        self.assertFalse(self.manager.disable())
        self.assertTrue(self.file.is_symlink())


if __name__ == "__main__":
    unittest.main()
