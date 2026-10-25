"""Exercise a real, isolated runsvdir instance without touching host services.

The workflow unpacks Ubuntu's runit package into RUNNER_TEMP. No package is
installed on the runner, no /etc path is modified, and the entire supervisor
process group is terminated in cleanup.
"""
from pathlib import Path
import os
import signal
import shutil
import subprocess
import tempfile
import time


def main():
    for binary in ("runsvdir", "runsv", "sv"):
        if not shutil.which(binary):
            raise RuntimeError(f"Missing disposable runit binary: {binary}")
    with tempfile.TemporaryDirectory() as name:
        base = Path(name)
        svroot = base / "sv"
        service = svroot / "auto-cpufreq"
        service.mkdir(parents=True)
        run = service / "run"
        run.write_text("#!/bin/sh\nexec sleep 60\n")
        run.chmod(0o755)
        active = base / "active"
        active.mkdir()
        active_link = active / "auto-cpufreq"
        active_link.symlink_to(service, target_is_directory=True)
        parent = subprocess.Popen(
            ["runsvdir", str(active)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            end = time.monotonic() + 12
            while not (service / "supervise/control").exists():
                if time.monotonic() >= end:
                    raise AssertionError("runsvdir did not initialize its disposable service")
                if parent.poll() is not None:
                    raise AssertionError(f"runsvdir exited unexpectedly: {parent.returncode}")
                time.sleep(0.1)

            stopped = subprocess.run(["sv", "stop", str(active_link)],
                                     capture_output=True, text=True, timeout=12)
            if stopped.returncode:
                raise AssertionError(f"sv stop: {stopped.stdout} {stopped.stderr}")
            active_link.unlink()
            # Once the link is absent, runsvdir will not re-spawn this
            # supervisor. 'shutdown' waits for supervisor exit, unlike 'stop'.
            result = subprocess.run(["sv", "shutdown", str(service)],
                                    capture_output=True, text=True, timeout=12)
            print(f"runit: after unlink, sv shutdown status={result.returncode}: {result.stdout.strip()} {result.stderr.strip()}", flush=True)

            # The manager's scan may race with sv shutdown; either mechanism
            # is legitimate. Verify the disposable child supervisor exits.
            deadline = time.monotonic() + 12
            while True:
                output = subprocess.run(
                    ["ps", "-o", "pid=", "--ppid", str(parent.pid)],
                    capture_output=True, text=True, check=True).stdout.strip()
                if not output:
                    break
                if time.monotonic() >= deadline:
                    raise AssertionError(f"Supervisor still running after removal: {output}")
                time.sleep(0.1)
            print("runit: supervisor terminated, safe to clean supervision directory", flush=True)
        finally:
            try:
                os.killpg(parent.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                parent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(parent.pid, signal.SIGKILL)
                parent.wait(timeout=3)


if __name__ == "__main__":
    main()
