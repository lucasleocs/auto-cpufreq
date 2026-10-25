"""Isolated source-release lifecycle and migration-guard regression.

Exercise the installer's real shell functions in a temporary root. No root,
service manager, /opt, /usr/local, /etc, or system packages are touched.
This is intentionally test-only and never copied to the production branch.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

INSTALLER = (Path(__file__).resolve().parents[1] / "auto-cpufreq-installer").read_text()


def source_function(name):
    beginning = re.search(r"(?m)^function " + re.escape(name) + r" \{\n", INSTALLER)
    if beginning is None:
        raise AssertionError("function missing: " + name)
    end = INSTALLER.find("\n}\n", beginning.end())
    if end == -1:
        raise AssertionError("function not terminated: " + name)
    return INSTALLER[beginning.start():end + 2]


FUNCTIONS = "\n\n".join(
    source_function(name)
    for name in (
        "release_is_complete", "activate_release", "discard_release",
        "publish_file", "restore_public_file", "snapshot_public_files",
        "restore_public_files", "discard_public_file_backups",
        "verify_command_version",
    )
)

SETUP = r"""
set -euo pipefail
ROOT="$1"
INSTALL_ROOT="$ROOT/install"
RELEASES_PATH="$INSTALL_ROOT/releases"
CURRENT_PATH="$INSTALL_ROOT/current"
mkdir -p "$RELEASES_PATH" "$ROOT/public"
make_complete() {
    local dir="$RELEASES_PATH/$1"
    mkdir -p "$dir/share/scripts" "$dir/share/images" "$dir/venv/bin"
    touch "$dir/.complete"
    for name in python auto-cpufreq auto-cpufreq-gtk; do
      printf '#!/bin/sh\nexit 0\n' > "$dir/venv/bin/$name"
      chmod +x "$dir/venv/bin/$name"
    done
}
"""


class VersionedInstallLifecycleTests(unittest.TestCase):
    def run_sandbox(self, actions):
        with tempfile.TemporaryDirectory() as tmp:
            # Fixed, isolated ROOT is passed as an argument to Bash, never
            # interpolated into the source snippets.
            script = FUNCTIONS + "\n" + SETUP + "\n" + actions
            result = subprocess.run(
                ["bash", "-c", script, "fixture", tmp],
                text=True, capture_output=True, timeout=15,
            )
            self.assertEqual(
                result.returncode, 0,
                f"Scenario failed with {result.returncode}:\n{result.stdout}\n{result.stderr}\n"
                f"Scenario:\n{actions}",
            )

    def test_first_install_and_versioned_upgrade_then_rollback(self):
        self.run_sandbox(r"""
make_complete v1
release_is_complete "$RELEASES_PATH/v1"
activate_release "$RELEASES_PATH/v1"
test "$(readlink "$CURRENT_PATH")" = "releases/v1"
make_complete v2
activate_release "$RELEASES_PATH/v2"
test "$(readlink "$CURRENT_PATH")" = "releases/v2"
test -e "$RELEASES_PATH/v1/.complete"
activate_release "$RELEASES_PATH/v1"
test "$(readlink "$CURRENT_PATH")" = "releases/v1"
discard_release "$RELEASES_PATH/v2"
test ! -e "$RELEASES_PATH/v2"
test -e "$RELEASES_PATH/v1/.complete"
""")

    def test_incomplete_candidate_never_changes_selected_release(self):
        self.run_sandbox(r"""
make_complete v1
activate_release "$RELEASES_PATH/v1"
mkdir -p "$RELEASES_PATH/incomplete/share/scripts"
if activate_release "$RELEASES_PATH/incomplete"; then exit 31; fi
test "$(readlink "$CURRENT_PATH")" = "releases/v1"
test -d "$RELEASES_PATH/incomplete"
""")

    def test_refuse_unmanaged_current_directory(self):
        self.run_sandbox(r"""
make_complete v1
mkdir "$CURRENT_PATH"
if activate_release "$RELEASES_PATH/v1"; then exit 32; fi
test -d "$CURRENT_PATH"
test ! -L "$CURRENT_PATH"
""")

    def test_refuse_foreign_or_symlinked_release(self):
        self.run_sandbox(r"""
make_complete good
mkdir -p "$ROOT/foreign"
if activate_release "$ROOT/foreign"; then exit 33; fi
ln -s "$RELEASES_PATH/good" "$RELEASES_PATH/alias"
if activate_release "$RELEASES_PATH/alias"; then exit 34; fi
if discard_release "$RELEASES_PATH/alias"; then exit 35; fi
test -L "$RELEASES_PATH/alias"
test -e "$RELEASES_PATH/good/.complete"
test ! -e "$CURRENT_PATH"
""")

    def test_public_files_reverted_with_modes_and_new_targets_removed(self):
        self.run_sandbox(r"""
cli="$ROOT/public/cli"
icon="$ROOT/public/icon"
new="$ROOT/public/gtk"
printf 'old cli\n' > "$cli"
chmod 700 "$cli"
printf 'old icon\n' > "$icon"
chmod 640 "$icon"
PUBLIC_FILE_TARGETS=("$cli" "$icon" "$new")
PUBLIC_FILE_BACKUPS=()
PUBLIC_FILE_MODES=()
snapshot_public_files
printf 'candidate cli\n' > "$ROOT/candidate"
publish_file "$ROOT/candidate" "$cli" 755
publish_file "$ROOT/candidate" "$icon" 644
publish_file "$ROOT/candidate" "$new" 755
test "$(cat "$cli")" = "candidate cli"
restore_public_files
test "$(cat "$cli")" = "old cli"
test "$(stat -c '%a' "$cli")" = 700
test "$(cat "$icon")" = "old icon"
test "$(stat -c '%a' "$icon")" = 640
test ! -e "$new"
discard_public_file_backups
test "$(find "$INSTALL_ROOT" -name '.public-file.*' | wc -l)" -eq 0
""")

    def test_public_symlink_is_rejected_before_any_publication(self):
        self.run_sandbox(r"""
cli="$ROOT/public/cli"
icon="$ROOT/public/icon"
printf 'original\n' > "$cli"
printf 'target\n' > "$ROOT/target"
ln -s "$ROOT/target" "$icon"
PUBLIC_FILE_TARGETS=("$cli" "$icon")
PUBLIC_FILE_BACKUPS=()
PUBLIC_FILE_MODES=()
if snapshot_public_files; then exit 36; fi
test "$(cat "$cli")" = "original"
test -L "$icon"
test "$(cat "$ROOT/target")" = "target"
test "$(find "$INSTALL_ROOT" -name '.public-file.*' | wc -l)" -eq 0
""")

    def test_version_verifier_checks_source_revision(self):
        self.run_sandbox(r"""
cli="$ROOT/public/cli"
printf '#!/bin/sh\nprintf "auto-cpufreq version: 3.2.0 (git: abcdef)\\n"\n' > "$cli"
chmod +x "$cli"
verify_command_version "$cli" "3.2.0+abcdef"
if verify_command_version "$cli" "3.2.0+wrong"; then exit 37; fi
if verify_command_version "$cli" "3.2.0"; then exit 38; fi
""")

    def test_removal_keeps_legacy_and_versioned_guards(self):
        # Removal is deliberately NOT executed: tool_remove() uses hardcoded
        # real host paths and process termination. Verify the critical guards
        # instead; a disposable VM is required for end-to-end removal.
        remove = source_function("tool_remove")
        self.assertIn('"$INSTALL_ROOT/venv"', remove)
        self.assertIn('"$RELEASES_PATH"', remove)
        self.assertIn('remove_directory "$LEGACY_SHARE_DIR"', remove)
        self.assertIn('if $daemon_installed; then', remove)
        self.assertIn('if ! "$AUTO_CPUFREQ_FILE" --remove; then', remove)
        self.assertIn('remove_directory "$INSTALL_ROOT"', remove)
        self.assertLess(
            remove.index('if ! "$AUTO_CPUFREQ_FILE" --remove; then'),
            remove.index('remove_directory "$INSTALL_ROOT"'),
        )
        self.assertLess(
            remove.index('remove_directory "$LEGACY_SHARE_DIR"'),
            remove.index('remove_directory "$INSTALL_ROOT"'),
        )


if __name__ == "__main__":
    unittest.main()
