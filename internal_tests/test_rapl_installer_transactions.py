"""Source installer transaction regression in temporary paths, no host changes.

Uses the genuine tool_install/activate_release/publish_file/rollback shell
functions; stubs system dependency setup and constructs fake complete releases.
Not equivalent to a full pip build, daemon install, or --update CLI test.
"""
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "auto-cpufreq-installer"

SCRIPT = r"""
set -e
source <(sed '/^# root check$/,$d' "$INSTALLER")
SOURCE_ROOT="$REPO_ROOT"
cd "$SOURCE_ROOT"
INSTALL_ROOT="$LAB_ROOT/opt/auto-cpufreq"
RELEASES_PATH="$INSTALL_ROOT/releases"
CURRENT_PATH="$INSTALL_ROOT/current"
OPERATION_LOCK_DIR="$LAB_ROOT/run/auto-cpufreq"
AUTO_CPUFREQ_FILE="$LAB_ROOT/usr/local/bin/auto-cpufreq"
AUTO_CPUFREQ_GTK_FILE="$AUTO_CPUFREQ_FILE-gtk"
AUTO_CPUFREQ_GTK_DESKTOP_FILE="auto-cpufreq-gtk.desktop"
APPLICATIONS_PATH="$LAB_ROOT/usr/share/applications"
IMG_FILE="$LAB_ROOT/usr/share/pixmaps/auto-cpufreq.png"
ORG_FILE="$LAB_ROOT/usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy"
LEGACY_SHARE_DIR="$LAB_ROOT/usr/local/share/auto-cpufreq"
SHARE_DIR="$CURRENT_PATH/share"
export MOCK_VERSION="3.2.0"
MOCK_FAIL_ACTIVATE=0
MOCK_FAIL_ICON=0
MOCK_FAIL_FINAL_VERIFY=0
apt-get() { return 0; }
verify_gtk3_runtime() { return 0; }
desktop-file-install() { return 0; }
update-desktop-database() { return 0; }

build_release() {
    ensure_install_layout || return 1
    local release
    release="$(mktemp -d "$RELEASES_PATH/mock.XXXXXXXX")" || return 1
    mkdir -p "$release/venv/bin" "$release/share/scripts" "$release/share/images"
    cat > "$release/venv/bin/python" <<EOS
#!/bin/sh
[ "\$1" = "-c" ] || exit 41
printf '%s\n' "$MOCK_VERSION"
EOS
    cat > "$release/venv/bin/auto-cpufreq" <<EOS
#!/bin/sh
[ "\$1" = "--version" ] || exit 42
printf 'auto-cpufreq version: $MOCK_VERSION\n'
EOS
    printf '#!/bin/sh\nexit 0\n' > "$release/venv/bin/auto-cpufreq-gtk"
    chmod 755 "$release/venv/bin/"*
    sed "s@/opt/auto-cpufreq/current@$CURRENT_PATH@" \
        "$SOURCE_ROOT/scripts/auto-cpufreq-venv-wrapper" \
        > "$release/share/scripts/auto-cpufreq-venv-wrapper"
    printf 'icon\n' > "$release/share/images/icon.png"
    printf 'policy\n' > "$release/share/scripts/$(basename "$ORG_FILE")"
    printf '#!/bin/sh\nexit 0\n' > "$release/share/scripts/start_app"
    printf '[Desktop Entry]\nType=Application\nName=Lab\n' \
        > "$release/share/scripts/$AUTO_CPUFREQ_GTK_DESKTOP_FILE"
    touch "$release/.complete"
    PREPARED_RELEASE="$release"
}

mv() {
    local last="" arg
    for arg in "$@"; do last="$arg"; done
    if [ "$MOCK_FAIL_ACTIVATE" = 1 ] && [ "$last" = "$CURRENT_PATH" ]; then
        return 75
    fi
    command mv "$@"
}
cp() {
    if [ "$MOCK_FAIL_ICON" = 1 ] && [[ "$1" == */share/images/icon.png ]]; then
        return 76
    fi
    command cp "$@"
}
eval "$(declare -f verify_command_version |
    sed '1s/verify_command_version/real_verify_command_version/')"
verify_command_version() {
    if [ "$MOCK_FAIL_FINAL_VERIFY" = 1 ] && [ "$1" = "$AUTO_CPUFREQ_FILE" ]; then
        return 77
    fi
    real_verify_command_version "$@"
}
assert_active() {
    local expected="$1"
    [ -L "$CURRENT_PATH" ] || return 81
    [ "$("$AUTO_CPUFREQ_FILE" --version)" = "auto-cpufreq version: $expected" ] || return 82
    [ -x "$AUTO_CPUFREQ_GTK_FILE" ] || return 83
    [ -f "$ORG_FILE" ] || return 84
    [ -f "$IMG_FILE" ] || return 85
}
mkdir -p "$APPLICATIONS_PATH"
tool_install > "$LAB_ROOT/first.log" 2>&1 || {
  cat "$LAB_ROOT/first.log"
  exit 101
}
assert_active "3.2.0" || exit 102
first="$(readlink -f "$CURRENT_PATH")"
[ -f "$first/.complete" ] || exit 103

export MOCK_VERSION="3.2.1"
tool_install >/dev/null || exit 104
assert_active "3.2.1" || exit 105
second="$(readlink -f "$CURRENT_PATH")"
[ "$first" != "$second" ] || exit 106
[ -f "$first/.complete" ] || exit 107

export MOCK_VERSION="3.2.2"
MOCK_FAIL_ACTIVATE=1
if tool_install >/dev/null 2>&1; then exit 108; fi
MOCK_FAIL_ACTIVATE=0
assert_active "3.2.1" || exit 109
[ "$(readlink -f "$CURRENT_PATH")" = "$second" ] || exit 110

export MOCK_VERSION="3.2.3"
MOCK_FAIL_ICON=1
if tool_install >/dev/null 2>&1; then exit 111; fi
MOCK_FAIL_ICON=0
assert_active "3.2.1" || exit 112
[ "$(readlink -f "$CURRENT_PATH")" = "$second" ] || exit 113

export MOCK_VERSION="3.2.4"
MOCK_FAIL_FINAL_VERIFY=1
if tool_install >/dev/null 2>&1; then exit 114; fi
MOCK_FAIL_FINAL_VERIFY=0
assert_active "3.2.1" || exit 115
[ "$(readlink -f "$CURRENT_PATH")" = "$second" ] || exit 116

printf 'still installed\n' > "$AUTO_CPUFREQ_FILE-remove"
export MOCK_VERSION="3.2.5"
if tool_install >/dev/null 2>&1; then exit 117; fi
assert_active "3.2.1" || exit 118
[ "$(readlink -f "$CURRENT_PATH")" = "$second" ] || exit 119
echo "SOURCE INSTALL TRANSACTION PASS: install/switch/rollback/refusal"
"""

class SourceInstallerTransactions(unittest.TestCase):
    def test_real_transaction_with_fake_release_payloads(self):
        with TemporaryDirectory(prefix="auto-cpufreq-install-lab-") as root:
            environment = {**os.environ,
                           "LAB_ROOT": root, "INSTALLER": str(INSTALLER),
                           "REPO_ROOT": str(ROOT), "TERM": "xterm"}
            result = subprocess.run(["bash", "-c", SCRIPT],
                                    cwd=ROOT, env=environment, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=60)
            self.assertEqual(result.returncode, 0,
                             f"stdout: {result.stdout[-4000:]}\n"
                             f"stderr: {result.stderr[-4000:]}")
            self.assertIn("SOURCE INSTALL TRANSACTION PASS", result.stdout)

if __name__ == "__main__":
    unittest.main()
