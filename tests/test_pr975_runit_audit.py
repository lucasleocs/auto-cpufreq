"""Isolated runit ownership parity regressions (no root or host service changes).

Run: python -m unittest discover -s tests -p test_pr975_runit_audit.py -v
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


INSTALL = Path("scripts/auto-cpufreq-install.sh")
REMOVE = Path("scripts/auto-cpufreq-remove.sh")


def shell_function(source, name):
    text = source.read_text()
    opening = f"function {name} {{"
    start = text.index(opening)
    end = text.index("\n}", start) + 2
    return text[start:end]


class RunitOwnershipParity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.share = self.root / "share"
        self.managed = self.share / "scripts" / "auto-cpufreq-runit"
        self.managed.parent.mkdir(parents=True)
        self.managed.write_text("#!/bin/sh\nexec auto-cpufreq --daemon\n")
        self.service = self.root / "sv" / "auto-cpufreq"

    def check(self, source):
        script = "\n\n".join((
            shell_function(source, "directory_contains_only"),
            shell_function(source, "runit_service_is_managed"),
            'runit_service_is_managed "$1"',
        ))
        return subprocess.run(
            ["bash", "-c", script, "runit-test", str(self.service)],
            env={**os.environ, "SHARE_DIR": str(self.share)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).returncode == 0

    def assert_both(self, expected):
        for source in (INSTALL, REMOVE):
            with self.subTest(script=str(source)):
                self.assertEqual(self.check(source), expected)

    def test_missing_service_is_retryable(self):
        self.assert_both(True)

    def test_empty_partial_service_is_retryable(self):
        self.service.mkdir(parents=True)
        self.assert_both(True)

    def test_managed_run_only_can_be_removed(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_bytes(self.managed.read_bytes())
        self.assert_both(True)

    def test_managed_run_with_supervise_directory(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_bytes(self.managed.read_bytes())
        (self.service / "supervise").mkdir()
        self.assert_both(True)

    def test_supervise_only_directory_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "supervise").mkdir()
        self.assert_both(False)

    def test_supervise_only_symlink_is_not_owned(self):
        self.service.mkdir(parents=True)
        external = self.root / "external-state"
        external.mkdir()
        (self.service / "supervise").symlink_to(external, target_is_directory=True)
        self.assert_both(False)
        self.assertTrue(external.is_dir())

    def test_changed_run_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_text("#!/bin/sh\nexec other-daemon\n")
        self.assert_both(False)

    def test_extra_custom_file_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "run").write_bytes(self.managed.read_bytes())
        (self.service / "local.conf").write_text("admin settings")
        self.assert_both(False)

    def test_run_symlink_is_not_owned(self):
        self.service.mkdir(parents=True)
        (self.service / "run").symlink_to(self.managed)
        self.assert_both(False)

    def test_service_symlink_is_not_owned(self):
        (self.root / "sv").mkdir()
        self.service.symlink_to(self.share)
        self.assert_both(False)


if __name__ == "__main__":
    unittest.main()
