"""Systemd removal race checks using the real shell branch with sandboxed paths."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

REMOVE = Path("scripts/auto-cpufreq-remove.sh")


def extract(source, name):
    start = source.index(f"function {name} {{")
    end = source.index("\n}", start) + 2
    return source[start:end]


class SystemdRemovalRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.share = self.root / "share" / "scripts"
        self.share.mkdir(parents=True)
        self.template = self.share / "auto-cpufreq.service"
        self.template.write_text("[Service]\nExecStart=/usr/local/bin/auto-cpufreq --daemon\n")
        self.unit = self.root / "etc" / "systemd" / "system" / "auto-cpufreq.service"
        self.unit.parent.mkdir(parents=True)
        self.commands = self.root / "commands.txt"
        self.replace_on_stop = False

    def remove(self):
        source = REMOVE.read_text()
        function = extract(source, "service_file_is_managed")
        case = source[source.index('case "$(ps h -o comm 1)" in'):]
        case = case.replace("/etc/systemd/system/auto-cpufreq.service", str(self.unit))
        prelude = r'''
ps() { echo systemd; }
systemctl() {
    echo "$*" >> "$COMMANDS"
    if [ "$1" = stop ] && [ "$REPLACE_ON_STOP" = 1 ]; then
        printf 'foreign replacement\n' > "$SANDBOX_UNIT"
    fi
    return 0
}
'''
        return subprocess.run(
            ["bash", "-c", prelude + "\n" + function + "\n" + case],
            capture_output=True, text=True,
            env={**os.environ, "SHARE_DIR": str(self.share.parent),
                 "COMMANDS": str(self.commands),
                 "SANDBOX_UNIT": str(self.unit),
                 "REPLACE_ON_STOP": "1" if self.replace_on_stop else "0"},
        )

    def test_owned_service_removed_normally(self):
        self.unit.write_bytes(self.template.read_bytes())
        result = self.remove()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.unit.exists())
        self.assertIn("daemon-reload", self.commands.read_text())

    def test_missing_unit_still_reloads_on_retry(self):
        result = self.remove()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commands.read_text().strip(), "daemon-reload")

    def test_foreign_file_refused_before_stop(self):
        self.unit.write_text("foreign\n")
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.unit.read_text(), "foreign\n")
        self.assertFalse(self.commands.exists())

    def test_symlink_refused_before_stop(self):
        external = self.root / "foreign"
        external.write_bytes(self.template.read_bytes())
        self.unit.symlink_to(external)
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.unit.is_symlink())
        self.assertEqual(external.read_bytes(), self.template.read_bytes())
        self.assertFalse(self.commands.exists())

    def test_replaced_during_stop_is_preserved(self):
        self.unit.write_bytes(self.template.read_bytes())
        self.replace_on_stop = True
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.unit.read_text(), "foreign replacement\n")
        self.assertIn("stop", self.commands.read_text())


if __name__ == "__main__":
    unittest.main()
