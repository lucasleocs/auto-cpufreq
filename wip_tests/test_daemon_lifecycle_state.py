import unittest
from unittest.mock import Mock, patch

import click

from auto_cpufreq import core


class DaemonLifecycleStateTests(unittest.TestCase):
    def _deploy_patches(self, daemon_running):
        return (
            patch.object(core, "cpufreqctl"),
            patch.object(core, "bluetooth_disable"),
            patch.object(core, "auto_cpufreq_stats_path", Mock()),
            patch.object(core, "copy"),
            patch.object(core, "call"),
            patch.object(core, "gnome_power_detect_install"),
            patch.object(core, "gnome_power_svc_disable"),
            patch.object(core, "tuned_svc_disable"),
            patch.object(core, "tlp_service_detect"),
            patch.object(core, "_run_daemon_lifecycle_script"),
            patch.object(core, "daemon_is_running", return_value=daemon_running),
        )

    def test_install_rejects_success_status_when_daemon_is_not_running(self):
        patches = self._deploy_patches(daemon_running=False)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8], patches[9], patches[10]:
            with self.assertRaises(click.ClickException):
                core.deploy_daemon()

    def test_remove_does_not_restore_competing_managers_if_daemon_remains_running(self):
        bluetooth_enable = Mock()
        gnome_power_svc_enable = Mock()
        tuned_svc_enable = Mock()

        with (
            patch.object(core.os.path, "exists", return_value=True),
            patch.object(core, "_run_daemon_lifecycle_script"),
            patch.object(core, "daemon_is_running", return_value=True),
            patch.object(core, "bluetooth_enable", bluetooth_enable),
            patch.object(core, "gnome_power_rm_reminder"),
            patch.object(core, "gnome_power_svc_enable", gnome_power_svc_enable),
            patch.object(core, "tuned_svc_enable", tuned_svc_enable),
        ):
            with self.assertRaises(click.ClickException):
                core.remove_daemon()

        bluetooth_enable.assert_not_called()
        gnome_power_svc_enable.assert_not_called()
        tuned_svc_enable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
