"""Executable regression tests for source-install daemon lifecycle paths.

These tests do not access the host's services or /usr/local/bin.
"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_cpufreq.modules.daemon_script_paths import resolve_daemon_script


ROOT = Path(__file__).resolve().parents[1]


class DaemonScriptPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.releases = self.root / "releases"
        self.releases.mkdir()

    def make_release(self, name, text):
        scripts = self.releases / name / "share" / "scripts"
        scripts.mkdir(parents=True)
        for action in ("install", "remove"):
            (scripts / ("auto-cpufreq-" + action + ".sh")).write_text(
                "#!/bin/sh\nprintf '%s\\n' " + text + "\n"
            )
        return scripts

    def test_pinned_path_does_not_follow_later_current_changes(self):
        first = self.make_release("first", "first")
        second = self.make_release("second", "second")
        current = self.root / "current"
        current.symlink_to(self.releases / "first", target_is_directory=True)
        selected = resolve_daemon_script(current / "share" / "scripts", "remove")
        current.unlink()
        current.symlink_to(self.releases / "second", target_is_directory=True)

        self.assertEqual(selected, first / "auto-cpufreq-remove.sh")
        result = subprocess.run(
            ["bash", str(selected)], capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.strip(), "first")
        self.assertEqual(
            resolve_daemon_script(current / "share" / "scripts", "remove"),
            second / "auto-cpufreq-remove.sh",
        )

    def test_unversioned_local_install_remains_supported(self):
        local = self.root / "usr" / "local" / "share" / "auto-cpufreq" / "scripts"
        local.mkdir(parents=True)
        (local / "auto-cpufreq-install.sh").write_text("#!/bin/sh\nexit 0\n")
        selected = resolve_daemon_script(local, "install")
        self.assertEqual(selected, local / "auto-cpufreq-install.sh")
        self.assertEqual(subprocess.call(["bash", str(selected)]), 0)

    def test_missing_or_nonfile_script_fails_before_execution(self):
        scripts = self.make_release("first", "first")
        (scripts / "auto-cpufreq-remove.sh").unlink()
        with self.assertRaises(FileNotFoundError):
            resolve_daemon_script(scripts, "remove")
        (scripts / "auto-cpufreq-remove.sh").mkdir()
        with self.assertRaises(FileNotFoundError):
            resolve_daemon_script(scripts, "remove")

    def test_unknown_action_is_rejected(self):
        scripts = self.make_release("first", "first")
        for action in ("status", "../remove", "remove;exit 0"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    resolve_daemon_script(scripts, action)

    def test_legacy_remove_marker_is_preserved_for_updater(self):
        # Stage one changes script execution, not the installed-daemon marker.
        core = (ROOT / "auto_cpufreq/core.py").read_text()
        cli = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
        installer = (ROOT / "auto-cpufreq-installer").read_text()
        self.assertIn('os.path.exists("/usr/local/bin/auto-cpufreq-remove")', cli)
        self.assertIn('$AUTO_CPUFREQ_FILE-remove', installer)
        self.assertIn('os.remove("/usr/local/bin/auto-cpufreq-remove")', core)
        self.assertIn('resolve_daemon_script(SCRIPTS_DIR, "install")', core)
        self.assertIn('resolve_daemon_script(SCRIPTS_DIR, "remove")', core)


if __name__ == "__main__":
    unittest.main()
