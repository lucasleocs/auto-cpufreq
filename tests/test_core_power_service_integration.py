"""Integration-level tests for source daemon installation/removal gates.

No subprocess, service manager or host path is modified in these tests.
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from auto_cpufreq import core


class CoreServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.stats_path = Path(temp.name) / "auto-cpufreq.stats"
        for name in (
            "cpufreqctl", "bluetooth_disable", "bluetooth_enable",
            "gnome_power_detect_install", "tlp_service_detect",
            "copy", "cpufreqctl_restore",
        ):
            patcher = patch.object(core, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        stats = patch.object(core, "auto_cpufreq_stats_path", self.stats_path)
        stats.start()
        self.addCleanup(stats.stop)
        self.install = patch.object(core, "disable_conflicting_services")
        self.remove = patch.object(core, "restore_conflicting_services")
        self.call = patch.object(core, "call")
        self.exists = patch.object(core.os.path, "exists")
        self.delete = patch.object(core.os, "remove")
        self.which = patch.object(core, "which", return_value="/usr/bin/systemctl")
        for patcher in (self.install, self.remove, self.call, self.exists, self.delete, self.which):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.installer_call = core.call
        self.exists_mock = core.os.path.exists
        self.disable_services = core.disable_conflicting_services
        self.restore_services = core.restore_conflicting_services

    def test_failed_state_capture_prevents_daemon_install_script(self):
        self.disable_services.return_value = False
        self.assertEqual(core.deploy_daemon(), 1)
        self.installer_call.assert_not_called()

    def test_failed_install_does_not_restore_when_service_active(self):
        self.disable_services.return_value = True
        self.installer_call.side_effect = [1, 0]
        self.assertEqual(core.deploy_daemon(), 1)
        self.assertEqual(self.installer_call.call_count, 2)
        self.restore_services.assert_not_called()

    def test_failed_install_restores_only_after_service_is_inactive(self):
        self.disable_services.return_value = True
        self.installer_call.side_effect = [1, 3]
        self.assertEqual(core.deploy_daemon(), 1)
        self.restore_services.assert_called_once_with()

    def test_failed_remove_script_preserves_existing_service_state(self):
        self.exists_mock.return_value = True
        self.installer_call.return_value = 1
        self.assertEqual(core.remove_daemon(), 1)
        self.restore_services.assert_not_called()
        core.os.remove.assert_not_called()

    def test_failed_state_restoration_keeps_retry_entrypoint(self):
        self.exists_mock.return_value = True
        self.installer_call.return_value = 0
        self.restore_services.return_value = False
        self.assertEqual(core.remove_daemon(), 1)
        core.os.remove.assert_not_called()

    def test_no_saved_state_keeps_legacy_removal_safe(self):
        self.exists_mock.return_value = True
        self.installer_call.return_value = 0
        self.restore_services.return_value = True
        self.assertEqual(core.remove_daemon(), 0)
        self.restore_services.assert_called_once_with()
        core.os.remove.assert_any_call("/usr/local/bin/auto-cpufreq-remove")


if __name__ == "__main__":
    unittest.main()
