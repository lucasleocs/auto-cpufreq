"""Exercise the installer's real removal function against disposable layouts.

No root access, daemon killing, system service, or real host path is involved.
The two literal host paths are replaced with temporary fixture paths, and
the pgrep command is stubbed before the extracted function is executed.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "auto-cpufreq-installer").read_text()
match = re.search(r"(?m)^function tool_remove \{\n", SOURCE)
if not match:
    raise AssertionError("Missing tool_remove function")
end = SOURCE.find("\n}\n", match.end())
if end == -1:
    raise AssertionError("Unterminated tool_remove")
REMOVE = SOURCE[match.start():end + 2]

# The installed source removal function also deletes two historical absolute
# service artifacts. Rewrite ONLY those two arguments to sandbox paths.
REPLACEMENTS = {
    '"/usr/local/bin/cpufreqctl.auto-cpufreq"':
        '"$ROOT/managed/cpufreqctl.auto-cpufreq"',
    '"/var/run/auto-cpufreq.stats"':
        '"$ROOT/managed/auto-cpufreq.stats"',
}
for original, fixture in REPLACEMENTS.items():
    if REMOVE.count(original) != 1:
        raise AssertionError("Unexpected host path in tool_remove: " + original)
    REMOVE = REMOVE.replace(original, fixture)
if re.search(r'"/(?:opt|usr|var|etc)/', REMOVE):
    raise AssertionError("Unisolated absolute host path in tool_remove")

SETUP = r'''
set -uo pipefail
ROOT="$1"
INSTALL_ROOT="$ROOT/source"
RELEASES_PATH="$INSTALL_ROOT/releases"
LEGACY_SHARE_DIR="$ROOT/legacy-share"
CURRENT_PATH="$INSTALL_ROOT/current"
AUTO_CPUFREQ_FILE="$ROOT/public/auto-cpufreq"
AUTO_CPUFREQ_GTK_FILE="$ROOT/public/auto-cpufreq-gtk"
IMG_FILE="$ROOT/public/icon.png"
ORG_FILE="$ROOT/public/policy"
APPLICATIONS_PATH="$ROOT/applications"
AUTO_CPUFREQ_GTK_DESKTOP_FILE="auto-cpufreq-gtk.desktop"
mkdir -p "$ROOT/public" "$ROOT/managed" "$APPLICATIONS_PATH"
# Never inspect or kill host processes.
pgrep() { return 0; }
# Keep desktop database refresh confined to this shell.
update-desktop-database() { return 0; }

case "$STAGE" in
  legacy|marker-symlink|daemon-failure|public-directory)
    mkdir -p "$INSTALL_ROOT/venv"
    ;;
  versioned|daemon-success)
    mkdir -p "$RELEASES_PATH/v1"
    ln -s releases/v1 "$CURRENT_PATH"
    ;;
  mixed)
    mkdir -p "$RELEASES_PATH/v1" "$INSTALL_ROOT/venv"
    ln -s releases/v1 "$CURRENT_PATH"
    ;;
  partial)
    mkdir -p "$INSTALL_ROOT"
    printf 'leave me\n' > "$INSTALL_ROOT/foreign"
    ;;
  absent)
    ;;
  *)
    exit 90
    ;;
esac
if [ "$STAGE" != absent ] && [ "$STAGE" != partial ]; then
  mkdir -p "$LEGACY_SHARE_DIR"
  printf 'obsolete\n' > "$LEGACY_SHARE_DIR/old-script"
  printf '#!/bin/sh\nexit 0\n' > "$AUTO_CPUFREQ_FILE"
  chmod +x "$AUTO_CPUFREQ_FILE"
  printf 'icon\n' > "$IMG_FILE"
  printf 'stats\n' > "$ROOT/managed/auto-cpufreq.stats"
fi
case "$STAGE" in
  marker-symlink)
    printf 'sentinel\n' > "$ROOT/foreign-marker"
    ln -s "$ROOT/foreign-marker" "$AUTO_CPUFREQ_FILE-remove"
    ;;
  daemon-failure)
    printf '#!/bin/sh\nexit 22\n' > "$AUTO_CPUFREQ_FILE"
    chmod +x "$AUTO_CPUFREQ_FILE"
    : > "$AUTO_CPUFREQ_FILE-remove"
    ;;
  daemon-success)
    : > "$AUTO_CPUFREQ_FILE-remove"
    ;;
  public-directory)
    mkdir -p "$AUTO_CPUFREQ_GTK_FILE"
    printf 'unmanaged\n' > "$AUTO_CPUFREQ_GTK_FILE/sentinel"
    ;;
esac
tool_remove
printf 'REMOVE_STATUS=%s\n' "$?"
'''


def run_case(stage):
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            ["bash", "-c", REMOVE + "\n" + SETUP, "fixture", directory],
            env={**os.environ, "STAGE": stage},
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode or "REMOVE_STATUS=" not in result.stdout:
            raise AssertionError(
                f"{stage}: bad test harness: rc={result.returncode}\n"
                f"stdout={result.stdout}\nstderr={result.stderr}"
            )
        status = int(result.stdout.rsplit("REMOVE_STATUS=", 1)[1].splitlines()[0])
        root = Path(directory)
        return (
            status, result.stdout + result.stderr,
            (root / "source").exists(),
            (root / "legacy-share").exists(),
            (root / "public/auto-cpufreq-remove").is_symlink(),
            (root / "public/auto-cpufreq-gtk/sentinel").exists(),
            (root / "foreign-marker").read_text() if
            (root / "foreign-marker").exists() else None,
        )


class LegacyAndVersionedRemovalTests(unittest.TestCase):
    def test_complete_removal_of_recognized_layouts(self):
        for stage in ("legacy", "versioned", "mixed", "daemon-success"):
            with self.subTest(stage=stage):
                status, output, source, legacy, _, _, _ = run_case(stage)
                self.assertEqual(status, 0, output)
                self.assertFalse(source, output)
                self.assertFalse(legacy, output)

    def test_missing_installation_is_safe_noop(self):
        status, output, source, legacy, _, _, _ = run_case("absent")
        self.assertEqual(status, 0, output)
        self.assertFalse(source)
        self.assertFalse(legacy)

    def test_unknown_partially_removed_layout_requires_inspection(self):
        status, output, source, _, _, _, _ = run_case("partial")
        self.assertEqual(status, 1, output)
        self.assertTrue(source)
        self.assertIn("Unrecognized or partially removed", output)

    def test_symbolic_daemon_marker_does_not_remove_source(self):
        status, output, source, legacy, marker, _, foreign = run_case("marker-symlink")
        self.assertEqual(status, 1, output)
        self.assertTrue(source)
        self.assertTrue(legacy)
        self.assertTrue(marker)
        self.assertEqual(foreign, "sentinel\n")

    def test_failed_daemon_removal_keeps_source_retryable(self):
        status, output, source, legacy, _, _, _ = run_case("daemon-failure")
        self.assertEqual(status, 1, output)
        self.assertTrue(source)
        self.assertTrue(legacy)
        self.assertIn("Failed to remove the auto-cpufreq daemon", output)

    def test_unmanaged_public_directory_prevents_final_source_cleanup(self):
        status, output, source, _, _, sentinel, _ = run_case("public-directory")
        self.assertEqual(status, 1, output)
        self.assertTrue(source)
        self.assertTrue(sentinel)
        self.assertIn("removal is incomplete", output)


if __name__ == "__main__":
    unittest.main()
