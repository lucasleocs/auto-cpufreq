"""Run the real daemon lifecycle shell scripts against isolated service fixtures.

No host init system, service directory or privileged operation is touched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SourceGenerationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.current = self.root / "current"
        self.systemd_dir = self.root / "etc" / "systemd" / "system"
        self.systemd_dir.mkdir(parents=True)
        self.unit = self.systemd_dir / "auto-cpufreq.service"
        self.log = self.root / "systemctl.log"
        self.fake_bin = self.root / "fake-bin"
        self.fake_bin.mkdir()
        self.make_fake_command(
            "ps",
            """#!/bin/sh
if [ -n "$TEST_ACTIVATE" ]; then
    ln -s "$TEST_ACTIVATE" "$TEST_CURRENT.next" || exit 1
    mv -Tf "$TEST_CURRENT.next" "$TEST_CURRENT" || exit 1
fi
printf 'systemd\\n'
""",
        )
        self.make_fake_command(
            "systemctl",
            """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_LOG"
if [ "$TEST_FAIL" = "$1" ]; then
    exit 5
fi
exit 0
""",
        )
        self.make_fake_command("tput", "#!/bin/sh\nprintf '120\\n'\n")

    def make_fake_command(self, name, text):
        path = self.fake_bin / name
        path.write_text(text)
        path.chmod(0o755)

    def prepare(self, scripts, identity):
        scripts.mkdir(parents=True)
        (scripts / "auto-cpufreq.service").write_text(
            "[Unit]\nDescription=auto-cpufreq " + identity + "\n"
        )
        for action in ("install", "remove"):
            original = (ROOT / "scripts" / ("auto-cpufreq-" + action + ".sh"))
            source = original.read_text()
            # Only sandbox copies bypass root and point to sandbox systemd.
            self.assertIn("if ((EUID != 0)); then", source)
            self.assertIn(
                "systemd_unit=/etc/systemd/system/auto-cpufreq.service",
                source,
            )
            source = source.replace(
                "if ((EUID != 0)); then", "if ((0 != 0)); then", 1
            )
            source = source.replace(
                "/etc/systemd/system/auto-cpufreq.service", str(self.unit)
            ).replace("/etc/systemd/system", str(self.systemd_dir))
            (scripts / ("auto-cpufreq-" + action + ".sh")).write_text(source)
        return scripts

    def release(self, name):
        return self.prepare(
            self.releases / name / "share" / "scripts", name
        )

    def activate(self, name):
        link = self.root / ".current.new"
        link.symlink_to(self.releases / name, target_is_directory=True)
        link.replace(self.current)

    def invoke(self, path, activate=None, fail=None):
        env = os.environ.copy()
        env.update({
            "PATH": str(self.fake_bin) + os.pathsep + env.get("PATH", ""),
            "TERM": "xterm",
            "TEST_CURRENT": str(self.current),
            "TEST_ACTIVATE": str(self.releases / activate) if activate else "",
            "TEST_LOG": str(self.log),
            "TEST_FAIL": fail or "",
        })
        return subprocess.run(
            ["bash", str(path)], env=env, capture_output=True,
            text=True, timeout=12,
        )

    def commands(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_install_uses_original_generation_if_current_changes_mid_run(self):
        first = self.release("first")
        self.release("second")
        self.activate("first")
        result = self.invoke(first / "auto-cpufreq-install.sh", activate="second")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            self.unit.read_bytes(),
            (first / "auto-cpufreq.service").read_bytes(),
        )
        self.assertEqual(self.current.resolve(), self.releases / "second")
        self.assertIn("daemon-reload", self.commands())
        self.assertIn("start auto-cpufreq", self.commands())

    def test_remove_uses_original_template_after_current_changes(self):
        first = self.release("first")
        self.release("second")
        self.activate("first")
        self.unit.write_bytes((first / "auto-cpufreq.service").read_bytes())
        result = self.invoke(first / "auto-cpufreq-remove.sh", activate="second")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertFalse(self.unit.exists())
        self.assertIn("stop auto-cpufreq", self.commands())
        self.assertIn("disable auto-cpufreq", self.commands())

    def test_unversioned_source_uses_its_own_templates(self):
        local = self.prepare(
            self.root / "usr" / "local" / "share" / "auto-cpufreq" / "scripts",
            "local",
        )
        self.release("second")
        self.activate("second")
        result = self.invoke(local / "auto-cpufreq-install.sh")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            self.unit.read_bytes(),
            (local / "auto-cpufreq.service").read_bytes(),
        )

    def test_copied_legacy_helper_keeps_current_fallback(self):
        self.release("second")
        self.activate("second")
        copied = self.root / "bin" / "auto-cpufreq-install"
        copied.parent.mkdir()
        source = (self.releases / "second" / "share" / "scripts"
                  / "auto-cpufreq-install.sh").read_text()
        copied.write_text(source.replace(
            "SHARE_DIR=/opt/auto-cpufreq/current/share",
            'SHARE_DIR="' + str(self.current / "share") + '"',
        ).replace(
            "SHARE_DIR=/usr/local/share/auto-cpufreq",
            'SHARE_DIR="' + str(self.root / "local" / "share") + '"',
        ))
        result = self.invoke(copied)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            self.unit.read_bytes(),
            (self.releases / "second" / "share" / "scripts"
             / "auto-cpufreq.service").read_bytes(),
        )

    def test_failed_enable_keeps_unit_for_safe_retry(self):
        first = self.release("first")
        self.activate("first")
        script = first / "auto-cpufreq-install.sh"
        failed = self.invoke(script, fail="enable")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(
            self.unit.read_bytes(),
            (first / "auto-cpufreq.service").read_bytes(),
        )
        self.log.unlink()
        succeeded = self.invoke(script)
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertIn("enable auto-cpufreq", self.commands())
        self.assertIn("start auto-cpufreq", self.commands())

    def test_custom_systemd_unit_is_preserved(self):
        first = self.release("first")
        self.activate("first")
        self.unit.write_text("[Unit]\nDescription=custom administrator unit\n")
        result = self.invoke(first / "auto-cpufreq-install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.unit.read_text(),
            "[Unit]\nDescription=custom administrator unit\n",
        )
        self.assertEqual(self.commands(), [])


if __name__ == "__main__":
    unittest.main()
