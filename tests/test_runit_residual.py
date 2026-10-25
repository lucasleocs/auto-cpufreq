"""Observe stale runit supervision states with the exact production rm_sv shell.

This diagnostic intentionally reports return codes without asserting success,
so the baseline v2 behavior can be seen before proposing a fix.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def extract_function(source, name):
    start = source.index("function " + name + " {")
    return source[start: source.index("\n}", start) + 2]


def run_case(kind):
    source = Path("scripts/auto-cpufreq-remove.sh").read_text()
    start = source.index("    rm_sv() {")
    end = source.index("\n    }\n\n    if [ -f /etc/os-release ]", start) + 6
    code = "\n\n".join((
        extract_function(source, "directory_contains_only"),
        extract_function(source, "runit_service_is_managed"),
        source[start:end],
        'rm_sv "$ROOT" "$ROOT/active"',
    ))
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        service = root / "sv/auto-cpufreq"
        service.mkdir(parents=True)
        (root / "active/service").mkdir(parents=True)
        template = root / "share/scripts/auto-cpufreq-runit"
        template.parent.mkdir(parents=True)
        data = "#!/bin/sh\nexec sleep 60\n"
        template.write_text(data)
        (service / "run").write_text(data)
        if kind == "empty_supervise":
            (service / "supervise").mkdir()
        if kind == "stale_fifos":
            (service / "supervise").mkdir()
            os.mkfifo(service / "supervise/ok")
            os.mkfifo(service / "supervise/control")
        result = subprocess.run(
            ["bash", "-c", code], capture_output=True, text=True,
            env={**os.environ, "ROOT": str(root), "SHARE_DIR": str(root / "share")},
            timeout=20)
        print(f"RUNIT_BASELINE kind={kind} status={result.returncode} "
              f"remaining={service.exists()} stderr={result.stderr.strip()} "
              f"stdout={result.stdout.strip()}", flush=True)
        return result.returncode


if __name__ == "__main__":
    if not shutil.which("sv"):
        raise RuntimeError("Real runit sv executable not on PATH")
    for kind in ("no_supervise", "empty_supervise", "stale_fifos"):
        run_case(kind)
