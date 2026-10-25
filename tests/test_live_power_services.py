"""Regression tests for temporary systemd changes in Live mode.

All systemctl and powerprofilesctl calls are mocked. No host services are used.
"""
import subprocess
import unittest
from unittest.mock import patch

from auto_cpufreq import power_helper as helper


def result(args, code=0, output="", error=""):
    return subprocess.CompletedProcess(args, code, stdout=output, stderr=error)


class LiveServiceTests(unittest.TestCase):
    def setUp(self):
        self.services = {
            "power-profiles-daemon.service": "inactive",
            "tuned.service": "inactive",
        }
        self.actions = []
        self.snap = patch.object(helper, "IS_INSTALLED_WITH_SNAP", False)
        self.systemctl = patch.object(helper, "systemctl_exists", True)
        self.ppctl = patch.object(helper, "powerprofilesctl_exists", False)
        self.runner = patch.object(helper, "run", side_effect=self.fake_run)
        for patcher in (self.snap, self.systemctl, self.ppctl, self.runner):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.fail_stop = None

    def fake_run(self, args, **kwargs):
        self.actions.append(args)
        if args[:2] == ["systemctl", "show"]:
            unit = args[2]
            active = self.services.get(unit, "inactive")
            return result(args, output=f"LoadState=loaded\nActiveState={active}\n")
        if args[:2] == ["systemctl", "stop"]:
            if self.fail_stop == args[2]:
                return result(args, code=1, error="simulated failure")
            self.services[args[2]] = "inactive"
            return result(args)
        if args[:2] == ["systemctl", "start"]:
            self.services[args[2]] = "active"
            return result(args)
        raise AssertionError(f"Unexpected command: {args!r}")

    def test_inactive_services_are_not_modified(self):
        changed = helper.stop_conflicting_services_live()
        self.assertEqual(changed, [])
        helper.restore_conflicting_services_live(changed)
        self.assertFalse(any(args[1] in ("stop", "start") for args in self.actions))

    def test_only_originally_active_service_is_restored(self):
        self.services["tuned.service"] = "active"
        changed = helper.stop_conflicting_services_live()
        self.assertEqual(changed, ["tuned.service"])
        self.assertEqual(self.services["tuned.service"], "inactive")
        helper.restore_conflicting_services_live(changed)
        self.assertEqual(self.services["tuned.service"], "active")
        self.assertEqual(self.services["power-profiles-daemon.service"], "inactive")

    def test_partial_stop_failure_restores_previous_services(self):
        self.services = dict.fromkeys(self.services, "active")
        self.fail_stop = "tuned.service"
        with self.assertRaises(RuntimeError):
            helper.stop_conflicting_services_live()
        self.assertEqual(set(self.services.values()), {"active"})

    def test_snap_never_changes_host_services(self):
        with patch.object(helper, "IS_INSTALLED_WITH_SNAP", True):
            self.assertEqual(helper.stop_conflicting_services_live(), [])
        self.assertEqual(self.actions, [])

    def test_service_started_externally_is_not_restarted(self):
        self.services["tuned.service"] = "active"
        changed = helper.stop_conflicting_services_live()
        self.services["tuned.service"] = "active"
        helper.restore_conflicting_services_live(changed)
        self.assertEqual(
            len([a for a in self.actions if a[:2] == ["systemctl", "start"]]), 0
        )


if __name__ == "__main__":
    unittest.main()
