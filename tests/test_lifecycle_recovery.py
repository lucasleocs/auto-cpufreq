"""Recovery regressions: runit interruption and daemon-install side effects.

Runs without root privileges or touching host init services.
"""
import ast
from pathlib import Path
import os
import subprocess
import tempfile
import types
import unittest

SCRIPT = Path("scripts/auto-cpufreq-remove.sh")


def extract_function(source, name):
    start = source.index(f"function {name} {{")
    end = source.index("\n}", start) + 2
    return source[start:end]


class RunitRetrySafety(unittest.TestCase):
    def test_failure_cleaning_supervise_preserves_run_for_retry(self):
        source = SCRIPT.read_text()
        start = source.index("    rm_sv() {")
        end = source.index("\n    }\n\n    if [ -f /etc/os-release ]", start) + 6
        function = source[start:end]
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            directory = root / "sv" / "auto-cpufreq"
            directory.mkdir(parents=True)
            (directory / "run").write_text("#!/bin/sh\nexec auto-cpufreq --daemon\n")
            (directory / "supervise").mkdir()
            share = root / "share" / "scripts"
            share.mkdir(parents=True)
            (share / "auto-cpufreq-runit").write_bytes((directory / "run").read_bytes())
            shell = "\n".join([
                extract_function(source, "directory_contains_only"),
                extract_function(source, "runit_service_is_managed"),
                """
sv() { return 0; }
rm() {
  if [ "$FAIL_SUPERVISE" = 1 ] && [[ "$*" == *"/supervise"* ]]; then
    return 5
  fi
  command rm "$@"
}
""",
                function,
                """
FAIL_SUPERVISE=1
rm_sv "$ROOT" "$ROOT/active"
first=$?
[ "$first" -ne 0 ] || exit 20
[ -f "$ROOT/sv/auto-cpufreq/run" ] || exit 21
FAIL_SUPERVISE=0
rm_sv "$ROOT" "$ROOT/active" || exit 22
[ ! -e "$ROOT/sv/auto-cpufreq" ] || exit 23
exit 0
""",
            ])
            proc = subprocess.run(["bash", "-c", shell],
                env={**os.environ, "ROOT": str(root), "SHARE_DIR": str(root / "share")},
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + f" exit={proc.returncode}")


class DaemonPreflightSafety(unittest.TestCase):
    def test_failed_preflight_does_not_disable_other_services_or_bluetooth(self):
        source = Path("auto_cpufreq/core.py").read_text()
        parsed = ast.parse(source)
        fn = next(node for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name == "deploy_daemon")
        events = []

        def effect(name):
            def callback(*args, **kwargs):
                events.append(name)
                return None
            return callback

        def call(*args, **kwargs):
            cmd = args[0]
            if isinstance(cmd, (list, tuple)) and "--preflight" in cmd:
                return 27
            events.append("installer_started")
            return 0

        names = ["cpufreqctl", "bluetooth_disable", "gnome_power_detect_install",
                 "gnome_power_svc_disable", "tuned_svc_disable", "tlp_service_detect"]
        scope = {name: effect(name) for name in names}
        scope.update({
            "SCRIPTS_DIR": Path("/tmp/sandbox/share/scripts"),
            "copy": effect("copy"),
            "call": call,
            "auto_cpufreq_stats_path": types.SimpleNamespace(touch=effect("touch")),
        })
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
                     "auto_cpufreq/core.py", "exec"), scope)
        result = scope["deploy_daemon"]()
        self.assertEqual(result, 27)
        self.assertEqual(events, [], "Preflight rejection must have no host-state side effects")


if __name__ == "__main__":
    unittest.main()
