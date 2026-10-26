"""Isolated Dinit/Runit lifecycle regressions for the production shell helpers.

Only explicitly extracted shell functions execute. All source paths that
might refer to host service configuration are confined to a TemporaryDirectory;
dinitctl, sv and ps are stubbed and no real host service is modified.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "scripts/auto-cpufreq-install.sh").read_text()
REMOVE = (ROOT / "scripts/auto-cpufreq-remove.sh").read_text()


def function(source, signature, end="\n}\n"):
    start = source.index(signature)
    finish = source.index(end, start + len(signature))
    return source[start:finish + len(end)]


def sandbox(source, action, env=None):
    with tempfile.TemporaryDirectory(prefix="auto-cpufreq-lifecycle-") as directory:
        script = source + "\n" + action
        proc = subprocess.run(
            ["bash", "-c", script, "fixture", directory],
            env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=12,
        )
        return proc, directory


ENABLE = (
    function(INSTALL, "function dinit_enable_link_is_managed {") + "\n"
    + function(INSTALL, "function dinit_enable {")
)
# Rewrite ONLY the hardcoded Dinit configuration roots used by the extracted
# helpers. Assert original layout before the test silently accepts updates.
for old, new in [
    ("/etc/dinit.d", "$ROOT/etc/dinit.d"),
    ("/usr/local/lib/dinit.d", "$ROOT/usr/local/lib/dinit.d"),
    ("/usr/lib/dinit.d", "$ROOT/usr/lib/dinit.d"),
]:
    assert old in ENABLE, old
    ENABLE = ENABLE.replace(old, new)

DISABLE = function(REMOVE, "function dinit_disable {")
PREFLIGHT = function(INSTALL, "function daemon_install_preflight {")
assert "/usr/local/bin/auto-cpufreq-remove" in PREFLIGHT
PREFLIGHT = PREFLIGHT.replace("/usr/local/bin/auto-cpufreq-remove", "$ROOT/daemon-remove")

RUNIT = "\n".join([
    function(REMOVE, "function directory_contains_only {"),
    function(REMOVE, "function runit_service_is_managed {"),
    function(REMOVE, "    rm_sv() {", "\n    }\n"),
])


class DinitEnableTests(unittest.TestCase):
    def run_enable(self, target="managed", boot="relative", rc="1", output=None):
        msg = "dinitctl: service already enabled." if output is None else output
        script = ENABLE + r'''
set -u
ROOT="$1"
mkdir -p "$ROOT/etc/dinit.d" "$ROOT/other"
printf 'type = scripted\n' > "$ROOT/etc/dinit.d/auto-cpufreq"
case "$BOOT" in
  relative)
    printf 'waits-for.d = boot.d\n' > "$ROOT/etc/dinit.d/boot"
    mkdir -p "$ROOT/etc/dinit.d/boot.d"
    link="$ROOT/etc/dinit.d/boot.d/auto-cpufreq"
    ;;
  absolute)
    printf 'waits-for.d = %s\n' "$ROOT/links" > "$ROOT/etc/dinit.d/boot"
    mkdir -p "$ROOT/links"
    link="$ROOT/links/auto-cpufreq"
    ;;
  missing)
    link="$ROOT/other/auto-cpufreq"
    ;;
  ambiguous)
    printf 'waits-for.d = boot.d\nwaits-for.d = other.d\n' > "$ROOT/etc/dinit.d/boot"
    mkdir -p "$ROOT/etc/dinit.d/boot.d"
    link="$ROOT/etc/dinit.d/boot.d/auto-cpufreq"
    ;;
esac
case "$TARGET" in
  managed)
    if [ "$BOOT" = absolute ]; then
      ln -s "$ROOT/etc/dinit.d/auto-cpufreq" "$link"
    else
      ln -s ../auto-cpufreq "$link"
    fi
    ;;
  foreign) printf 'foreign\n' > "$ROOT/other/alien"; ln -s "$ROOT/other/alien" "$link" ;;
  regular) printf 'foreign\n' > "$link" ;;
  absent) ;;
esac
dinitctl() { printf '%s\n' "$DINIT_OUTPUT" >&2; return "$DINIT_RC"; }
dinit_enable
'''
        proc, _ = sandbox(script, "", {
            "TARGET": target, "BOOT": boot, "DINIT_RC": rc, "DINIT_OUTPUT": msg
        })
        return proc

    def test_managed_link_allows_repeat_enable(self):
        for boot in ("relative", "absolute"):
            with self.subTest(boot=boot):
                p = self.run_enable(boot=boot)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_foreign_regular_missing_and_ambiguous_links_are_rejected(self):
        for target, boot in (
            ("foreign", "relative"), ("regular", "relative"),
            ("absent", "relative"), ("managed", "ambiguous"),
            ("managed", "missing"),
        ):
            with self.subTest(target=target, boot=boot):
                p = self.run_enable(target=target, boot=boot)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertIn("unverified", p.stderr)

    def test_dinit_success_and_other_errors_preserve_status(self):
        self.assertEqual(self.run_enable(target="absent", rc="0", output="").returncode, 0)
        self.assertEqual(self.run_enable(rc="29", output="permission denied").returncode, 29)


class DinitDisableTests(unittest.TestCase):
    def run_disable(self, target="absent", rc="1", note="", path_kind="normal"):
        script = DISABLE + r'''
set -u
ROOT="$1"
mkdir -p "$ROOT/boot.d"
entry="$ROOT/boot.d/auto-cpufreq"
case "$TARGET" in
  regular) printf 'foreign\n' > "$entry" ;;
  symlink) ln -s "$ROOT/foreign" "$entry" ;;
  absent) ;;
esac
case "$PATH_KIND" in
  normal) path="$entry" ;;
  unknown) path="$ROOT/not-existing/auto-cpufreq" ;;
esac
DINIT_OUTPUT="dinitctl: could not unlink dependency entry $path: No such file or directory"
[ -z "$NOTE" ] || DINIT_OUTPUT="$DINIT_OUTPUT
$NOTE"
dinitctl() { printf '%s\n' "$DINIT_OUTPUT" >&2; return "$DINIT_RC"; }
dinit_disable
'''
        p, _ = sandbox(script, "", {
            "TARGET": target, "DINIT_RC": rc, "NOTE": note, "PATH_KIND": path_kind
        })
        return p

    def test_repeated_disable_accepts_exact_dinit_diagnostics(self):
        p = self.run_disable()
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.run_disable(note="dinitctl: note: service was disabled for now; persistent disable failed.")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_present_link_unrecognized_message_and_missing_parent_not_accepted(self):
        for opts in (
            {"target": "symlink"}, {"target": "regular"},
            {"note": "unexpected trailing note"},
            {"path_kind": "unknown"},
        ):
            with self.subTest(opts=opts):
                p = self.run_disable(**opts)
                self.assertEqual(p.returncode, 1, p.stderr)


class RunitShutdownRaceTests(unittest.TestCase):
    def run_remove(self, mode):
        script = RUNIT + r'''
set -u
ROOT="$1"
SHARE_DIR="$ROOT/share"
service_dir="$ROOT/etc/sv/auto-cpufreq"
active_link="$ROOT/var/service/auto-cpufreq"
mkdir -p "$SHARE_DIR/scripts" "$ROOT/etc/sv" "$ROOT/var/service" "$service_dir/supervise"
printf '#!/bin/sh\nexit 0\n' > "$SHARE_DIR/scripts/auto-cpufreq-runit"
cp "$SHARE_DIR/scripts/auto-cpufreq-runit" "$service_dir/run"
chmod +x "$service_dir/run"
ln -s "$service_dir" "$active_link"
if [ "$MODE" != inactive ]; then mkfifo "$service_dir/supervise/ok"; fi
sv() {
    case "$1" in
       stop) return 0 ;;
       shutdown)
         case "$MODE" in
           race) rm -f -- "$service_dir/supervise/ok"; return 4 ;;
           stuck) return 4 ;;
           normal) rm -f -- "$service_dir/supervise/ok"; return 0 ;;
           *) return 55 ;;
         esac
         ;;
    esac
}
rm_sv "$ROOT/etc" "$ROOT/var"
status=$?
echo "RESULT=$status"
test -e "$service_dir/run" && echo "RUN_RETAINED=1" || echo "RUN_RETAINED=0"
test -p "$service_dir/supervise/ok" && echo "FIFO_RETAINED=1" || echo "FIFO_RETAINED=0"
'''
        return sandbox(script, "", {"MODE": mode})[0]

    def test_supervisor_disappears_between_fifo_check_and_shutdown(self):
        p = self.run_remove("race")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("RESULT=0", p.stdout)
        self.assertIn("RUN_RETAINED=0", p.stdout)

    def test_real_shutdown_failure_keeps_service_files(self):
        p = self.run_remove("stuck")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("RESULT=1", p.stdout)
        self.assertIn("RUN_RETAINED=1", p.stdout)
        self.assertIn("FIFO_RETAINED=1", p.stdout)

    def test_normal_and_uninitialized_supervisor_cleanup(self):
        for mode in ("normal", "inactive"):
            with self.subTest(mode=mode):
                p = self.run_remove(mode)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn("RESULT=0", p.stdout)


class MarkerPreflightTests(unittest.TestCase):
    def run_preflight(self, marker):
        script = PREFLIGHT + r'''
set -u
ROOT="$1"
case "$MARKER" in
  symlink) ln -s "$ROOT/foreign-file" "$ROOT/daemon-remove" ;;
  directory) mkdir "$ROOT/daemon-remove" ;;
  regular) printf '#!/bin/sh\n' > "$ROOT/daemon-remove" ;;
  absent) ;;
esac
ps() { echo unsupported-init; }
daemon_install_preflight
'''
        return sandbox(script, "", {"MARKER": marker})[0]

    def test_symlink_and_nonfile_marker_are_rejected_before_host_changes(self):
        for marker in ("symlink", "directory"):
            with self.subTest(marker=marker):
                p = self.run_preflight(marker)
                self.assertEqual(p.returncode, 1)
                self.assertIn("unexpected daemon-removal helper", p.stderr)

    def test_existing_regular_marker_reaches_normal_preflight(self):
        p = self.run_preflight("regular")
        self.assertEqual(p.returncode, 1)
        self.assertIn("Unsupported init system", p.stderr)
        self.assertNotIn("unexpected daemon-removal helper", p.stderr)


if __name__ == "__main__":
    unittest.main()
