"""Dinit lifecycle helper tests with real Bash functions, isolated from the host."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALL = Path("scripts/auto-cpufreq-install.sh")
REMOVE = Path("scripts/auto-cpufreq-remove.sh")


def extract(source, name):
    text = source.read_text()
    start = text.index(f"function {name} {{")
    return text[start:text.index("\n}", start) + 2]


class DinitRetries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.link = self.root / "boot.d" / "auto-cpufreq"
        self.link.parent.mkdir()

    def invoke(self, helper, scenario):
        source = INSTALL if helper == "dinit_enable" else REMOVE
        functions = extract(source, helper)
        mock = r'''
dinitctl() {
  case "$SCENARIO" in
    success) echo 'Operation succeeded.'; return 0 ;;
    enabled) echo 'dinitctl: service already enabled.' >&2; return 1 ;;
    disabled)
      echo "dinitctl: could not unlink dependency entry $BOOT_LINK: No such file or directory" >&2
      return 1 ;;
    permission) echo 'dinitctl: permission denied' >&2; return 3 ;;
    unexpected) echo 'dinitctl: service already disabled.' >&2; return 1 ;;
  esac
  return 7
}
'''
        return subprocess.run(
            ["bash", "-c", mock + functions + "\n" + helper],
            env={**os.environ, "SCENARIO": scenario, "BOOT_LINK": str(self.link)},
            text=True, capture_output=True,
        )

    def test_first_enable_succeeds(self):
        self.assertEqual(self.invoke("dinit_enable", "success").returncode, 0)

    def test_second_enable_is_idempotent(self):
        self.assertEqual(self.invoke("dinit_enable", "enabled").returncode, 0)

    def test_enable_permission_error_is_not_ignored(self):
        self.assertEqual(self.invoke("dinit_enable", "permission").returncode, 3)

    def test_first_disable_succeeds(self):
        self.assertEqual(self.invoke("dinit_disable", "success").returncode, 0)

    def test_second_disable_is_idempotent(self):
        self.assertEqual(self.invoke("dinit_disable", "disabled").returncode, 0)

    def test_missing_dependency_parent_is_not_ignored(self):
        self.link.parent.rmdir()
        self.assertEqual(self.invoke("dinit_disable", "disabled").returncode, 1)

    def test_symlink_at_dependency_entry_is_not_ignored(self):
        self.link.symlink_to(self.root / "other")
        self.assertEqual(self.invoke("dinit_disable", "disabled").returncode, 1)

    def test_unrelated_disable_error_is_not_ignored(self):
        self.assertEqual(self.invoke("dinit_disable", "permission").returncode, 3)

    def test_unknown_message_is_not_ignored(self):
        self.assertEqual(self.invoke("dinit_disable", "unexpected").returncode, 1)


if __name__ == "__main__":
    unittest.main()
