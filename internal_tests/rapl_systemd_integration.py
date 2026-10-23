"""Real PID-1 systemd lifecycle test, GitHub Actions VM only.

Never installed or executed on a user workstation by default.
The unique unit is written solely to /run/systemd/system; Powercap is fake.
No real auto-cpufreq daemon, real /sys file, installer or other unit is touched.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parent
PREFIX = "auto-cpufreq-rapl-lifecycle-lab-"
BASELINE = 15_000_000
TARGET = 12_000_000


def cmd(*argv, check=True):
    p = subprocess.run(argv, text=True, capture_output=True,
                       env={**os.environ, "PYTHONPATH": str(REPOSITORY)})
    if check and p.returncode:
        raise AssertionError(
            "Command failed: " + repr(argv) + "\nstdout:\n" +
            p.stdout + "\nstderr:\n" + p.stderr
        )
    return p


def poll(description, condition, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.12)
    raise AssertionError("Timed out: " + description)


def lines(root):
    logfile = root / "events.log"
    return logfile.read_text().splitlines() if logfile.exists() else []


def value(root):
    return int((root / "fake-powercap/intel-rapl/intel-rapl:0"
                "/constraint_0_power_limit_uw").read_text())


def journal(root):
    return root / "power-state.json"


def assert_restored(root):
    assert value(root) == BASELINE, f"Not restored: {value(root)}"
    assert not journal(root).exists(), "Ownership journal not cleared"


def change_release(root, version):
    name = root / (".candidate-" + str(os.getpid()))
    name.symlink_to("releases/" + version)
    os.replace(name, root / "current")


def write_unit(unitfile, name, root, *, restore_mode="normal",
               restart="on-failure", start_mode="normal"):
    """Substitute only absolute paths in the lab copy of the ACTUAL unit."""
    template = (REPOSITORY / "scripts/auto-cpufreq.service").read_text()
    updates = (
        (
            "WorkingDirectory=/opt/auto-cpufreq/current/venv",
            f"WorkingDirectory={root}",
        ),
        (
            "Environment=PYTHONPATH=/opt/auto-cpufreq/current",
            f"Environment=PYTHONPATH={REPOSITORY}",
        ),
        (
            "ExecStart=/opt/auto-cpufreq/current/venv/bin/python "
            "/opt/auto-cpufreq/current/venv/bin/auto-cpufreq --daemon",
            f"ExecStart=/usr/bin/python3 {root}/current/fixture.py "
            f"daemon {root} {start_mode}",
        ),
        (
            "RuntimeDirectory=auto-cpufreq",
            f"RuntimeDirectory={name}",
        ),
        (
            "ExecStopPost=-/opt/auto-cpufreq/current/venv/bin/"
            "auto-cpufreq-restore-rapl",
            f"ExecStopPost=-/usr/bin/python3 {root}/current/fixture.py "
            f"restore {root} {restore_mode}",
        ),
        ("Restart=on-failure", "Restart=" + restart),
    )
    for old, new in updates:
        if template.count(old) != 1:
            raise AssertionError("Production systemd template changed: " + old)
        template = template.replace(old, new)

    assert "RuntimeDirectoryMode=0700" in template
    assert "RuntimeDirectoryPreserve=yes" in template
    unitfile.write_text(
        template.replace(
            "\n[Install]\n",
            "\nRestartSec=1s\nTimeoutStopSec=15s\n\n[Install]\n",
        )
    )
    cmd("systemd-analyze", "verify", str(unitfile))
    cmd("systemctl", "daemon-reload")


def test_lab():
    assert os.geteuid() == 0, "Requires root inside disposable CI VM"
    assert Path("/proc/1/comm").read_text().strip() == "systemd", (
        "Requires real systemd PID1; a syntax-only check is insufficient"
    )
    assert Path("/run/systemd/system").is_dir(), (
        "Systemd runtime unit directory unavailable"
    )

    name = PREFIX + str(os.getpid())
    unit = name + ".service"
    root = Path("/run") / name
    unitfile = Path("/run/systemd/system") / unit

    if root.exists() or unitfile.exists():
        raise AssertionError("Ephemeral lab path already exists")
    try:
        releases = root / "releases"
        releases.mkdir(parents=True, mode=0o700)
        for version in ("v1", "v2"):
            folder = releases / version
            folder.mkdir()
            shutil.copyfile(HERE / "rapl_systemd_fixture.py",
                            folder / "fixture.py")
        change_release(root, "v1")
        cmd(sys.executable, str(root / "current/fixture.py"), "initialize",
            str(root))
        write_unit(unitfile, name, root)

        print("Case 1: normal systemd stop invokes the restore helper", flush=True)
        cmd("systemctl", "start", unit)
        poll("first apply", lambda: value(root) == TARGET)
        assert journal(root).exists()
        cmd("systemctl", "stop", unit)
        poll("restoration on stop", lambda: value(root) == BASELINE)
        assert_restored(root)
        assert "restore:0:v1" in lines(root)

        print("Case 2: restart does not strand limits or state", flush=True)
        before = lines(root).count("restore:0:v1")
        cmd("systemctl", "start", unit)
        poll("apply before restart", lambda: value(root) == TARGET)
        cmd("systemctl", "restart", unit)
        poll("reapply after restart",
             lambda: lines(root).count("restore:0:v1") > before
             and lines(root).count("daemon-applied:v1") >= 3)
        assert value(root) == TARGET
        assert lines(root)[-2:] == ["restore:0:v1", "daemon-applied:v1"], (
            "Restoration must precede daemon re-application on restart"
        )
        cmd("systemctl", "stop", unit)
        assert_restored(root)

        print("Case 3: killed daemon triggers cleanup before auto-restart",
              flush=True)
        before = lines(root).count("daemon-applied:v1")
        cmd("systemctl", "start", unit)
        poll("apply before kill", lambda: value(root) == TARGET)
        pid = int(cmd("systemctl", "show", "-p", "MainPID",
                      "--value", unit).stdout.strip())
        assert pid > 1
        os.kill(pid, signal.SIGKILL)
        poll("cleanup and recovery after SIGKILL", lambda: (
            lines(root).count("daemon-applied:v1") >= before + 2
            and lines(root)[-2:] == ["restore:0:v1", "daemon-applied:v1"]
        ), timeout=30)
        cmd("systemctl", "stop", unit)
        assert_restored(root)

        print("Case 4: atomic release switch and rollback leave cleanup intact",
              flush=True)
        change_release(root, "v2")
        cmd("systemctl", "start", unit)
        poll("v2 applied", lambda: lines(root)[-1:] == ["daemon-applied:v2"])
        assert value(root) == TARGET
        cmd("systemctl", "stop", unit)
        assert_restored(root)
        assert lines(root)[-1:] == ["restore:0:v2"]
        change_release(root, "v1")
        cmd("systemctl", "start", unit)
        poll("v1 rollback applied", lambda: lines(root)[-1:] ==
             ["daemon-applied:v1"])
        cmd("systemctl", "stop", unit)
        assert_restored(root)

        print("Case 5: ignored optional restore failure keeps journal",
              flush=True)
        write_unit(unitfile, name, root, restore_mode="fail")
        cmd("systemctl", "start", unit)
        poll("apply before restore-failure", lambda: value(root) == TARGET)
        cmd("systemctl", "stop", unit)  # Must succeed despite '-'
        assert value(root) == TARGET, "Injected failure should leave limit active"
        assert journal(root).exists(), "Unresolved journal must survive stop"
        assert lines(root)[-1:] == ["restore:1:v1"]
        assert root.exists(), "RuntimeDirectoryPreserve=yes must survive stop"

        print("Case 6: restart stays operational but refuses new RAPL writes",
              flush=True)
        cmd("systemctl", "start", unit)
        poll("blocked recovery on next start",
             lambda: lines(root)[-1:] == ["daemon-blocked:v1"])
        assert value(root) == TARGET
        cmd("systemctl", "stop", unit)
        assert journal(root).exists()

        print("Case 7: recovery after failure re-arms only after cleanup",
              flush=True)
        write_unit(unitfile, name, root, restore_mode="normal")
        cmd("systemctl", "start", unit)
        poll("still blocked before cleanup",
             lambda: lines(root)[-1:] == ["daemon-blocked:v1"])
        cmd("systemctl", "stop", unit)
        assert_restored(root)
        cmd("systemctl", "start", unit)
        poll("apply after resolved recovery",
             lambda: lines(root)[-1:] == ["daemon-applied:v1"])
        cmd("systemctl", "stop", unit)
        assert_restored(root)

        print("SYSTEMD RAPL LAB PASS: 7 real-manager lifecycle scenarios",
              flush=True)
    finally:
        # Always dismantle ONLY this unique fixture/unit.
        cmd("systemctl", "stop", unit, check=False)
        cmd("systemctl", "reset-failed", unit, check=False)
        unitfile.unlink(missing_ok=True)
        cmd("systemctl", "daemon-reload", check=False)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    test_lab()
