"""Behavioral regression of the post-activation source installer rollback.

Runs the actual production branch, with fake service-independent integration
commands and a temporary current symlink. Host paths are never modified.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "auto-cpufreq-installer").read_text()
START = "  # Candidate checks do not exercise the stable command users invoke."
END = "  if ! command -v desktop-file-install > /dev/null 2>&1; then"
SECTION = INSTALLER.split(START, 1)[1].split(END, 1)[0]
SECTION = START + SECTION

SETUP = r"""
set -u
ROOT="$1"
CURRENT_PATH="$ROOT/current"
PREPARED_RELEASE="$ROOT/candidate"
prepared_share_dir="$PREPARED_RELEASE/share"
AUTO_CPUFREQ_FILE="$ROOT/public/auto-cpufreq"
IMG_FILE="$ROOT/public/icon.png"
ORG_FILE="$ROOT/public/policy"
AUTO_CPUFREQ_GTK_FILE="$ROOT/public/auto-cpufreq-gtk"
APPLICATIONS_PATH="$ROOT/applications"
AUTO_CPUFREQ_GTK_DESKTOP_FILE="auto-cpufreq-gtk.desktop"
previous_release=""
if [ "$HAS_PREVIOUS" = yes ]; then previous_release="$ROOT/previous"; fi
mkdir -p "$PREPARED_RELEASE/venv/bin" "$ROOT/bin"
printf '#!/bin/sh\nprintf "3.2.0\\n"\n' > "$PREPARED_RELEASE/venv/bin/python"
chmod +x "$PREPARED_RELEASE/venv/bin/python"
printf '#!/bin/sh\nexit 19\n' > "$ROOT/bin/desktop-file-install"
chmod +x "$ROOT/bin/desktop-file-install"
export PATH="$ROOT/bin:$PATH"
ln -s "$PREPARED_RELEASE" "$CURRENT_PATH"
publish_file() {
  printf 'PUBLISH:%s\n' "$2" >> "$ROOT/trace"
  case "$STAGE:$2" in
    icon:"$IMG_FILE"|policy:"$ORG_FILE"|gtk:"$AUTO_CPUFREQ_GTK_FILE") return 5;;
  esac
  return 0
}
verify_command_version() {
  [ "$STAGE" != version ]
}
activate_release() {
  printf 'ACTIVATE:%s\n' "$1" >> "$ROOT/trace"
  if [ "$REACTIVATION_FAIL" = yes ]; then return 6; fi
  ln -sfn "$1" "$CURRENT_PATH"
}
restore_public_files() {
  printf 'RESTORE\n' >> "$ROOT/trace"
  [ "$RESTORE_FAIL" != yes ]
}
discard_release() {
  printf 'DISCARD:%s\n' "$1" >> "$ROOT/trace"
  rm -rf -- "$1"
}
if [ "$UNLINK_FAIL" = yes ]; then
  rm() {
    if [ "$1" = "-f" ] && [ "$2" = "--" ] && [ "$3" = "$CURRENT_PATH" ]; then
      return 8
    fi
    command rm "$@"
  }
fi
"""

def run_case(*, stage, previous=True, reactivation_fail=False, unlink_fail=False,
             restore_fail=False):
    with tempfile.TemporaryDirectory() as directory:
        code = SETUP + "\nexercise() {\n" + (
            "local active_version install_error=\"\" candidate_is_active\n"
            + SECTION + "\n}\nexercise\nstatus=$?\n"
            + "printf 'FINAL_STATUS=%s\\n' \"$status\"\n"
        )
        env = {
            **os.environ, "STAGE": stage,
            "HAS_PREVIOUS": "yes" if previous else "no",
            "REACTIVATION_FAIL": "yes" if reactivation_fail else "no",
            "UNLINK_FAIL": "yes" if unlink_fail else "no",
            "RESTORE_FAIL": "yes" if restore_fail else "no",
        }
        result = subprocess.run(
            ["bash", "-c", code, "fixture", directory],
            text=True, capture_output=True, timeout=15, env=env,
        )
        output = result.stdout + result.stderr
        if result.returncode or "FINAL_STATUS=" not in output:
            raise AssertionError(f"Broken fixture: {result.returncode} {output}")
        status = int(output.split("FINAL_STATUS=")[-1].splitlines()[0])
        trace_file = Path(directory, "trace")
        trace = trace_file.read_text().splitlines() if trace_file.exists() else []
        current = Path(directory, "current")
        candidate = Path(directory, "candidate")
        return status, trace, output, current.is_symlink(), (
            str(current.readlink()) if current.is_symlink() else None
        ), candidate.exists()


class InstallerRollbackTests(unittest.TestCase):
    def test_success_publishes_all_integrations_without_rollback(self):
        status, trace, _, _, target, candidate = run_case(stage="success")
        self.assertEqual(status, 0)
        self.assertEqual(sum(t.startswith("PUBLISH:") for t in trace), 3)
        self.assertFalse(any(t.startswith(("RESTORE", "DISCARD", "ACTIVATE")) for t in trace))
        self.assertTrue(target.endswith("/candidate"))
        self.assertTrue(candidate)

    def test_each_failure_uses_one_rollback_to_previous_generation(self):
        for stage in ("version", "icon", "policy", "gtk", "desktop"):
            with self.subTest(stage=stage):
                status, trace, output, _, target, candidate = run_case(stage=stage)
                self.assertEqual(status, 1, output)
                self.assertEqual(trace.count("RESTORE"), 1)
                self.assertEqual(sum(t.startswith("DISCARD:") for t in trace), 1)
                self.assertEqual(sum(t.startswith("ACTIVATE:") for t in trace), 1)
                self.assertTrue(target.endswith("/previous"), trace)
                self.assertFalse(candidate)
                self.assertIn("Error:", output)

    def test_first_install_failure_unlinks_current_before_discard(self):
        status, trace, output, is_link, _, candidate = run_case(
            stage="policy", previous=False,
        )
        self.assertEqual(status, 1, output)
        self.assertFalse(is_link)
        self.assertFalse(candidate)
        self.assertEqual(trace.count("RESTORE"), 1)
        self.assertEqual(sum(t.startswith("DISCARD:") for t in trace), 1)

    def test_failed_reactivation_keeps_candidate(self):
        status, trace, output, _, target, candidate = run_case(
            stage="gtk", reactivation_fail=True,
        )
        self.assertEqual(status, 1, output)
        self.assertTrue(candidate)
        self.assertTrue(target.endswith("/candidate"))
        self.assertIn("RESTORE", trace)
        self.assertFalse(any(t.startswith("DISCARD:") for t in trace))
        self.assertIn("Failed to restore the previous active source release", output)

    def test_failed_unlink_keeps_candidate(self):
        status, trace, output, is_link, _, candidate = run_case(
            stage="icon", previous=False, unlink_fail=True,
        )
        self.assertEqual(status, 1, output)
        self.assertTrue(is_link)
        self.assertTrue(candidate)
        self.assertFalse(any(t.startswith("DISCARD:") for t in trace))
        self.assertIn("Failed to undo the first source release activation", output)

    def test_failed_public_restore_does_not_skip_safe_release_discard(self):
        status, trace, output, _, target, candidate = run_case(
            stage="icon", restore_fail=True,
        )
        self.assertEqual(status, 1, output)
        self.assertTrue(target.endswith("/previous"))
        self.assertFalse(candidate)
        self.assertEqual(trace.count("RESTORE"), 1)
        self.assertIn("Failed to restore public auto-cpufreq files", output)

    def test_single_failure_path_remains_in_source(self):
        self.assertEqual(SECTION.count('restore_public_files || echo'), 1)
        self.assertEqual(SECTION.count("    return 1"), 1)
        self.assertNotIn("integration_files_published", SECTION)


if __name__ == "__main__":
    unittest.main()
