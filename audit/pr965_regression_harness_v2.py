#!/usr/bin/env python3
"""Work-branch wrapper that tightens the PPD regression check."""

from pathlib import Path
import subprocess
import pr965_regression_harness as harness


def test_native_ppd_disable_does_not_infer_snap_state():
    calls = []
    disabled = []
    output = []

    def fake_call(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd and cmd[0] == "snap":
            return 1
        return 0

    namespace = {
        "systemctl_exists": True,
        "powerprofilesctl_exists": True,
        "gnome_power_status": 0,
        "call": fake_call,
        "DEVNULL": subprocess.DEVNULL,
        "STDOUT": subprocess.STDOUT,
        "disable_power_profiles_daemon": lambda: disabled.append(True),
        "print": lambda *args, **kwargs: output.append(" ".join(str(arg) for arg in args)),
    }
    disable = harness.load_function(
        "auto_cpufreq/power_helper.py", "gnome_power_svc_disable", namespace
    )
    disable()
    rendered = "\n".join(output).lower()
    assert disabled, "active PPD was not disabled"
    assert not any(isinstance(cmd, list) and cmd and cmd[0] == "snap" for cmd in calls), (
        "native PPD handling still tries to infer auto-cpufreq Snap installation"
    )
    assert "snap package" not in rendered, (
        "native PPD handling still reports a fabricated Snap installation state"
    )


harness.TESTS = [
    test_native_ppd_disable_does_not_infer_snap_state
    if test.__name__ == "test_native_ppd_disable_does_not_infer_snap_state"
    else test
    for test in harness.TESTS
]

raise SystemExit(harness.main())
