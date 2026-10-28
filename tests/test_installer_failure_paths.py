"""Isolated characterization of PR #975 installer failure paths.

The original Bash functions run with disposable paths and stubbed package
operations. No real service, system file or process is modified.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().parents[1] / "auto-cpufreq-installer"


def extract(name):
    lines = INSTALLER.read_text().splitlines(keepends=True)
    start = lines.index("function " + name + " {\n")
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("}"))
    return "".join(lines[start:end + 1])


FUNCTIONS = ("release_is_complete", "activate_release", "discard_release",
             "publish_file", "restore_public_file", "snapshot_public_files",
             "restore_public_files", "discard_public_file_backups",
             "verify_command_version", "tool_install")


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class InstallerFailureAuditTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.installroot = self.root / "opt" / "auto-cpufreq"
        self.releases = self.installroot / "releases"
        self.releases.mkdir(parents=True)
        self.current = self.installroot / "current"
        self.command = self.root / "bin" / "auto-cpufreq"
        self.gtk = self.root / "bin" / "auto-cpufreq-gtk"
        self.icon = self.root / "pixmaps" / "auto-cpufreq.png"
        self.policy = self.root / "polkit" / "org.auto-cpufreq.pkexec.policy"
        self.applications = self.root / "applications"
        self.applications.mkdir()
        self.desktop = self.applications / "auto-cpufreq-gtk.desktop"
        self.old = self.release("old", "1.0.0")
        self.candidate = self.release("candidate", "2.0.0")
        self.current.symlink_to("releases/old")
        executable(self.command, 'printf "auto-cpufreq version: 1.0.0\\n"\n')
        executable(self.gtk, "exit 0\n")
        for path, value in ((self.icon, "old-icon"),
                            (self.policy, "old-policy"),
                            (self.desktop, "old-desktop")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        self.before = {
            path: path.read_bytes()
            for path in (self.command, self.gtk, self.icon, self.policy, self.desktop)
        }
        self.fakebin = self.root / "fakebin"
        executable(self.fakebin / "desktop-file-install", "exit 0\n")
        executable(self.fakebin / "update-desktop-database", "exit 0\n")

    def release(self, name, version):
        path = self.releases / name
        scripts = path / "share" / "scripts"
        scripts.mkdir(parents=True)
        (path / "share" / "images").mkdir()
        (path / ".complete").touch()
        executable(path / "venv" / "bin" / "python",
                   'printf "' + version + '\\n"\n')
        executable(path / "venv" / "bin" / "auto-cpufreq",
                   'printf "auto-cpufreq version: ' + version + '\\n"\n')
        executable(path / "venv" / "bin" / "auto-cpufreq-gtk", "exit 0\n")
        executable(scripts / "auto-cpufreq-venv-wrapper",
                   'printf "auto-cpufreq version: ' + version + '\\n"\n')
        executable(scripts / "start_app", "exit 0\n")
        (scripts / "org.auto-cpufreq.pkexec.policy").write_text(name)
        (path / "share" / "images" / "icon.png").write_text(name)
        (scripts / "auto-cpufreq-gtk.desktop").write_text(name)
        return path

    def env(self, scenario):
        env = os.environ.copy()
        env.update({
            "TEST_SCENARIO": scenario,
            "TEST_CANDIDATE": str(self.candidate),
            "PATH": str(self.fakebin) + os.pathsep + env.get("PATH", ""),
            "INSTALL_ROOT": str(self.installroot),
            "RELEASES_PATH": str(self.releases),
            "CURRENT_PATH": str(self.current),
            "SOURCE_ROOT": str(self.root),
            "LEGACY_SHARE_DIR": str(self.root / "legacy-share"),
            "AUTO_CPUFREQ_FILE": str(self.command),
            "AUTO_CPUFREQ_GTK_FILE": str(self.gtk),
            "AUTO_CPUFREQ_GTK_DESKTOP_FILE": "auto-cpufreq-gtk.desktop",
            "IMG_FILE": str(self.icon),
            "ORG_FILE": str(self.policy),
            "APPLICATIONS_PATH": str(self.applications),
        })
        return env

    def installer(self, scenario):
        functions = "\n".join(extract(n) for n in FUNCTIONS)
        functions = functions.replace("function publish_file {",
                                      "function original_publish_file {", 1)
        functions = functions.replace("function activate_release {",
                                      "function original_activate_release {", 1)
        bash = functions + r"""
header() { :; }
os_release_matches() { [ "$1" = debian ]; }
apt-get() { :; }
verify_python_version() { :; }
verify_gtk3_runtime() { :; }
build_release() {
  PREPARED_RELEASE="$TEST_CANDIDATE"
  [ "$TEST_SCENARIO" != prepare ]
}
publish_file() {
  if [ "$TEST_SCENARIO" = policy ] && [ "$2" = "$ORG_FILE" ]; then
    return 17
  fi
  original_publish_file "$@"
}
activate_release() {
  if [ "$TEST_SCENARIO" = activate ] && [ "$1" = "$TEST_CANDIDATE" ]; then
    return 19
  fi
  original_activate_release "$@"
}
tool_install
"""
        return subprocess.run(["bash", "-c", bash], cwd=self.root,
                              env=self.env(scenario), capture_output=True,
                              text=True, timeout=25)

    def assert_restored(self):
        self.assertEqual(self.current.resolve(), self.old)
        for path, data in self.before.items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_bytes(), data)

    def test_preparation_failure_keeps_previous_install(self):
        result = self.installer("prepare")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_restored()

    def test_activation_failure_keeps_previous_install(self):
        result = self.installer("activate")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_restored()

    def test_integration_failure_keeps_previous_install(self):
        result = self.installer("policy")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assert_restored()

    def test_success_switches_active_release(self):
        result = self.installer("success")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.current.resolve(), self.candidate)
        output = subprocess.check_output([str(self.command)], text=True)
        self.assertIn("2.0.0", output)

    def test_foreign_public_file_is_deleted_on_source_uninstall(self):
        # Characterization only: deleting an arbitrary regular file at a
        # recognized public path is the current implementation's behavior.
        tool = extract("tool_remove")
        tool = tool.replace('"/usr/local/bin/cpufreqctl.auto-cpufreq"',
                            '"$TEST_FAKE_CPUFREQCTL"')
        tool = tool.replace('"/var/run/auto-cpufreq.stats"',
                            '"$TEST_FAKE_STATS"')
        self.assertNotIn('"/var/run/auto-cpufreq.stats"', tool)
        self.assertNotIn('"/usr/local/bin/cpufreqctl.auto-cpufreq"', tool)
        self.command.write_text("USER-CUSTOMIZED-UNRELATED-CONTENT")
        env = self.env("remove")
        env["TEST_FAKE_CPUFREQCTL"] = str(self.root / "none-cpufreqctl")
        env["TEST_FAKE_STATS"] = str(self.root / "none-stats")
        script = tool + "\n" + r"""
header() { :; }
pgrep() { return 1; }
kill() { :; }
tool_remove
"""
        result = subprocess.run(
            ["bash", "-c", script], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=25,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.command.exists())


if __name__ == "__main__":
    unittest.main()
