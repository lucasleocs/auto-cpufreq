import unittest
from unittest.mock import patch

import auto_cpufreq.power_helper as power_helper


class PowerProfilesInstallTests(unittest.TestCase):
    def test_active_ppd_is_balanced_then_disabled_without_snap_detection(self):
        with (
            patch.object(power_helper, "systemctl_exists", True),
            patch.object(power_helper, "gnome_power_status", 0),
            patch.object(power_helper, "powerprofilesctl_exists", True),
            patch.object(power_helper, "call") as call_mock,
            patch.object(power_helper, "disable_power_profiles_daemon") as disable_mock,
        ):
            power_helper.gnome_power_svc_disable()

        call_mock.assert_called_once_with(["powerprofilesctl", "set", "balanced"])
        disable_mock.assert_called_once_with()

    def test_inactive_ppd_is_left_unchanged(self):
        with (
            patch.object(power_helper, "systemctl_exists", True),
            patch.object(power_helper, "gnome_power_status", 3),
            patch.object(power_helper, "powerprofilesctl_exists", True),
            patch.object(power_helper, "call") as call_mock,
            patch.object(power_helper, "disable_power_profiles_daemon") as disable_mock,
        ):
            power_helper.gnome_power_svc_disable()

        call_mock.assert_not_called()
        disable_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
