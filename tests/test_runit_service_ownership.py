"""Check Runit ownership predicates without touching host init services.

The install and remove shell functions are extracted from the actual scripts,
then executed against disposable directories.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def shell_function(script, name):
    lines = script.read_text().splitlines(keepends=True)
    start = lines.index("function " + name + " {\n")
    end = next(index for index in range(start + 1, len(lines))
               if lines[index].strip() == "}")
    return "".join(lines[start:end + 1])


class RunitServiceOwnershipTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = self.root / "sv" / "auto-cpufreq"
        self.scripts = self.root / "share" / "scripts"
        self.scripts.mkdir(parents=True)
        self.run_template = self.scripts / "auto-cpufreq-runit"
        self.run_template.write_text("#!/bin/sh\nexec auto-cpufreq --daemon\n")

    def check_both_scripts(self, expected):
        for action in ("install", "remove"):
            with self.subTest(action=action):
                script = ROOT / "scripts" / ("auto-cpufreq-" + action + ".sh")
                functions = (
                    shell_function(script, "directory_contains_only") + "\n" +
                    shell_function(script, "runit_service_is_managed") + "\n"
                )
                env = os.environ.copy()
                env["SHARE_DIR"] = str(self.root / "share")
                env["TEST_SERVICE"] = str(self.service)
                result = subprocess.run(
                    ["bash", "-c",
                     functions + '\nrunit_service_is_managed "$TEST_SERVICE"\n'],
                    env=env, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(
                    result.returncode, expected,
                    "%s: stdout=%r stderr=%r" %
                    (action, result.stdout, result.stderr),
                )

    def test_missing_or_empty_service_is_retryable(self):
        self.check_both_scripts(0)
        self.service.mkdir(parents=True)
        self.check_both_scripts(0)

    def test_supervise_only_directory_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "supervise").mkdir()
        self.check_both_scripts(1)

    def test_recognized_run_script_and_supervise_directory_are_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_bytes(self.run_template.read_bytes())
        (self.service / "supervise").mkdir()
        self.check_both_scripts(0)

    def test_foreign_run_script_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_text("#!/bin/sh\nexec foreign-service\n")
        self.check_both_scripts(1)

    def test_service_directory_symlink_is_not_owned(self):
        other = self.root / "foreign"
        other.mkdir()
        self.service.parent.mkdir(parents=True)
        self.service.symlink_to(other, target_is_directory=True)
        self.check_both_scripts(1)


if __name__ == "__main__":
    unittest.main()
