"""Test actual production runit removal against disposable runsvdir processes.

The CI job unpacks runit in RUNNER_TEMP. This test uses no system services,
no privileges, and no writes outside TemporaryDirectory.
"""
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile
import time


def extract(source, name):
    start = source.index(f"function {name} {{")
    return source[start:source.index("\n}", start) + 2]


def main():
    for binary in ("runsvdir", "runsv", "sv"):
        if not shutil.which(binary):
            raise RuntimeError(f"Missing isolated runit binary: {binary}")

    source = Path("scripts/auto-cpufreq-remove.sh").read_text()
    start = source.index("    rm_sv() {")
    end = source.index("\n    }\n\n    if [ -f /etc/os-release ]", start) + 6
    functions = "\n\n".join((
        extract(source, "directory_contains_only"),
        extract(source, "runit_service_is_managed"),
        source[start:end],
    ))

    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        service = root / "sv/auto-cpufreq"
        service.mkdir(parents=True)
        run = service / "run"
        run.write_text("#!/bin/sh\nexec sleep 60\n")
        run.chmod(0o755)
        template = root / "share/scripts/auto-cpufreq-runit"
        template.parent.mkdir(parents=True)
        template.write_bytes(run.read_bytes())
        active = root / "active/service"
        active.mkdir(parents=True)
        (active / "auto-cpufreq").symlink_to(service, target_is_directory=True)

        parent = subprocess.Popen(
            ["runsvdir", str(active)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 12
            while not (service / "supervise/control").exists():
                if time.monotonic() >= deadline:
                    raise AssertionError("Disposable runsvdir did not initialize")
                if parent.poll() is not None:
                    raise AssertionError(f"runsvdir exited: {parent.returncode}")
                time.sleep(0.1)

            commands_log = root / "commands.log"
            shell = "\n".join((
                """sv() {
    printf '%s\n' "$*" >> "$COMMANDS_LOG"
    command sv "$@"
}""",
                functions,
                'rm_sv "$ROOT" "$ROOT/active"',
            ))
            result = subprocess.run(
                ["bash", "-c", shell],
                env={**os.environ, "ROOT": str(root),
                     "SHARE_DIR": str(root / "share"),
                     "COMMANDS_LOG": str(commands_log)},
                capture_output=True, text=True, timeout=24,
            )
            print(f"runit real removal: exit={result.returncode}\n{result.stdout}\n{result.stderr}", flush=True)
            if result.returncode:
                raise AssertionError("Real runit service removal failed")
            if service.exists() or (active / "auto-cpufreq").is_symlink():
                raise AssertionError("Removal left a service definition or link")
            recorded = commands_log.read_text()
            if "shutdown " not in recorded:
                raise AssertionError(f"Removal never waited for runsv shutdown: {recorded}")

            # runsvdir remains alive, but its child supervisor must have exited.
            deadline = time.monotonic() + 12
            while True:
                ps = subprocess.run(
                    ["ps", "-o", "pid=", "--ppid", str(parent.pid)],
                    capture_output=True, text=True)
                if ps.returncode not in (0, 1):
                    raise AssertionError(f"Unable to inspect isolated process tree: {ps.stderr}")
                if not ps.stdout.strip():
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Supervisor still present: {ps.stdout.strip()}")
                time.sleep(0.1)
            print("runit real removal: supervisor exited before cleanup, PASS", flush=True)
        finally:
            try:
                os.killpg(parent.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                parent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(parent.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                parent.wait(timeout=3)


if __name__ == "__main__":
    main()
