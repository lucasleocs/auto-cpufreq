#!/usr/bin/env python3
"""Temporary focused regression harness for PR #965 work.

This file is intentionally work-branch-only. It is removed from the final PR.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class HarnessError(RuntimeError):
    pass


FAKE_CLICK = SimpleNamespace(ClickException=HarnessError)


def load_function(relative_path: str, name: str, namespace: dict):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(), filename=str(path))
    node = next(
        (item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name),
        None,
    )
    if node is None:
        raise AssertionError(f"missing function {name} in {relative_path}")
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def extract_bash_function(relative_path: str, name: str) -> str:
    text = (ROOT / relative_path).read_text()
    marker = f"function {name} {{"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"missing bash function {name}")
    pos = start
    depth = 0
    seen_open = False
    while pos < len(text):
        char = text[pos]
        if char == "{":
            depth += 1
            seen_open = True
        elif char == "}" and seen_open:
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
        pos += 1
    raise AssertionError(f"unterminated bash function {name}")


def test_update_check_returns_exact_release_tag():
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "v9.9.9"}

    request_errors = SimpleNamespace(
        ConnectionError=type("ConnectionError", (Exception,), {}),
        Timeout=type("Timeout", (Exception,), {}),
        RequestException=type("RequestException", (Exception,), {}),
        HTTPError=type("HTTPError", (Exception,), {}),
    )
    namespace = {
        "GITHUB": "https://github.com/AdnanHodzic/auto-cpufreq",
        "get": lambda _url: Response(),
        "exceptions": request_errors,
        "check_output": lambda _cmd: b"auto-cpufreq version: 1.2.3\n",
        "search": re.search,
    }
    check_for_update = load_function("auto_cpufreq/core.py", "check_for_update", namespace)
    target = check_for_update()
    assert target == "v9.9.9", f"expected exact release tag, got {target!r}"


def test_update_preparation_clones_exact_tag_without_installing():
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]:
            destination = Path(cmd[-1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
        return SimpleNamespace(returncode=0)

    namespace = {
        "Path": Path,
        "GITHUB": "https://github.com/AdnanHodzic/auto-cpufreq",
        "run": fake_run,
        "rmtree": shutil.rmtree,
        "click": FAKE_CLICK,
    }
    prepare_update = load_function("auto_cpufreq/core.py", "prepare_update", namespace)
    with tempfile.TemporaryDirectory() as tmp:
        installer = prepare_update(tmp, "v9.9.9")
        assert Path(installer).name == "auto-cpufreq-installer"
    clone = next((cmd for cmd in calls if isinstance(cmd, list) and cmd[:2] == ["git", "clone"]), None)
    assert clone is not None, "prepare_update did not clone the release"
    assert "--branch" in clone and clone[clone.index("--branch") + 1] == "v9.9.9", clone
    assert "--depth" in clone and clone[clone.index("--depth") + 1] == "1", clone
    assert not any(
        isinstance(cmd, list) and any("auto-cpufreq-installer" in str(part) for part in cmd)
        for cmd in calls
        if cmd is not clone
    ), "preparation must not install before daemon removal"


def test_prepared_installer_failure_is_propagated():
    namespace = {
        "run": lambda *_args, **_kwargs: SimpleNamespace(returncode=7),
        "click": FAKE_CLICK,
    }
    install_prepared_update = load_function(
        "auto_cpufreq/core.py", "install_prepared_update", namespace
    )
    try:
        install_prepared_update(Path("/tmp/auto-cpufreq-installer"))
    except HarnessError:
        return
    raise AssertionError("installer failure was not propagated")


def test_final_version_mismatch_is_reported():
    namespace = {
        "check_output": lambda _cmd: b"auto-cpufreq version: 1.2.3\n",
        "search": re.search,
        "click": FAKE_CLICK,
    }
    verify_update = load_function("auto_cpufreq/core.py", "verify_update", namespace)
    try:
        verify_update("v9.9.9")
    except HarnessError:
        return
    raise AssertionError("version mismatch was accepted as a successful update")


def test_update_prepares_source_before_daemon_removal_and_preserves_daemon_state():
    source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
    start = source.index("        elif update:")
    end = source.index("        elif remove:", start)
    block = source[start:end]
    assert "prepare_update(" in block, "update path does not prepare a replacement source"
    assert block.index("prepare_update(") < block.index("remove_daemon()"), (
        "current installation is removed before the replacement is prepared"
    )
    assert "daemon_was_installed" in block, "update does not preserve prior daemon state"
    assert "if daemon_was_installed" in block, "daemon reinstall is not conditional"


def test_native_ppd_disable_does_not_infer_snap_state():
    calls = []
    disabled = []

    def fake_call(cmd, *args, **kwargs):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[:3] == ["systemctl", "is-active", "--quiet"]:
            return 0
        if isinstance(cmd, list) and cmd and cmd[0] == "snap":
            return 1
        return 0

    namespace = {
        "systemctl_exists": True,
        "powerprofilesctl_exists": True,
        "gnome_power_status": 0,
        "gnome_power_svc_active": lambda: True,
        "call": fake_call,
        "DEVNULL": subprocess.DEVNULL,
        "STDOUT": subprocess.STDOUT,
        "disable_power_profiles_daemon": lambda: disabled.append(True),
    }
    disable = load_function("auto_cpufreq/power_helper.py", "gnome_power_svc_disable", namespace)
    disable()
    assert disabled, "active PPD was not disabled"
    assert not any(isinstance(cmd, list) and cmd and cmd[0] == "snap" for cmd in calls), (
        "native PPD handling still tries to infer auto-cpufreq Snap installation"
    )


def test_daemon_install_shell_propagates_start_failure():
    function = extract_bash_function("scripts/auto-cpufreq-install.sh", "auto_cpufreq_install")
    script = f"""
{function}
start_cmd() {{ return 7; }}
enable_cmd() {{ return 0; }}
auto_cpufreq_install systemd start_cmd enable_cmd
"""
    result = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode != 0, "failed daemon start was masked by a later successful command"


def test_daemon_remove_shell_stops_on_stop_failure():
    function = extract_bash_function("scripts/auto-cpufreq-remove.sh", "auto_cpufreq_remove")
    with tempfile.TemporaryDirectory() as tmp:
        unit = Path(tmp) / "auto-cpufreq.service"
        unit.write_text("unit")
        script = f"""
{function}
stop_cmd() {{ return 7; }}
disable_cmd() {{ return 0; }}
auto_cpufreq_remove systemd stop_cmd disable_cmd {unit}
"""
        result = subprocess.run(["bash", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert result.returncode != 0, "failed daemon stop was masked"
        assert unit.exists(), "unit file was removed even though daemon stop failed"


def test_python_install_propagates_daemon_script_failure():
    class StatsPath:
        @staticmethod
        def touch(**_kwargs):
            return None

    def fake_call(cmd, *args, **kwargs):
        if cmd == "/usr/local/bin/auto-cpufreq-install":
            return 7
        return 0

    namespace = {
        "cpufreqctl": lambda: None,
        "bluetooth_disable": lambda: None,
        "auto_cpufreq_stats_path": StatsPath(),
        "copy": lambda *_args, **_kwargs: None,
        "SCRIPTS_DIR": Path("/src/scripts"),
        "call": fake_call,
        "gnome_power_detect_install": lambda: None,
        "gnome_power_svc_disable": lambda: None,
        "tuned_svc_disable": lambda: None,
        "tlp_service_detect": lambda: None,
        "click": FAKE_CLICK,
    }
    deploy_daemon = load_function("auto_cpufreq/core.py", "deploy_daemon", namespace)
    try:
        deploy_daemon()
    except HarnessError:
        return
    raise AssertionError("Python install path ignored daemon installer failure")


def test_python_remove_does_not_restore_competitors_before_success():
    events = []

    class FakePathOps:
        @staticmethod
        def exists(path):
            return path == "/usr/local/bin/auto-cpufreq-remove"

    class FakeOS:
        path = FakePathOps()

        @staticmethod
        def remove(path):
            events.append(("remove-file", path))

    class StatsPath:
        @staticmethod
        def exists():
            return False

    def fake_call(cmd, *args, **kwargs):
        events.append(("call", cmd))
        if cmd == "/usr/local/bin/auto-cpufreq-remove":
            return 7
        return 0

    namespace = {
        "os": FakeOS,
        "sys": SimpleNamespace(exit=lambda code: (_ for _ in ()).throw(SystemExit(code))),
        "bluetooth_enable": lambda: events.append(("restore", "bluetooth")),
        "gnome_power_rm_reminder": lambda: events.append(("restore", "ppd-reminder")),
        "gnome_power_svc_enable": lambda: events.append(("restore", "ppd")),
        "tuned_svc_enable": lambda: events.append(("restore", "tuned")),
        "call": fake_call,
        "governor_override_state": "/tmp/override",
        "auto_cpufreq_stats_path": StatsPath(),
        "auto_cpufreq_stats_file": None,
        "cpufreqctl_restore": lambda: events.append(("restore", "cpufreqctl")),
        "click": FAKE_CLICK,
    }
    remove_daemon = load_function("auto_cpufreq/core.py", "remove_daemon", namespace)
    try:
        remove_daemon()
    except HarnessError:
        pass
    else:
        raise AssertionError("Python remove path ignored daemon removal failure")
    assert not any(event[0] == "restore" for event in events), events


TESTS = [
    test_update_check_returns_exact_release_tag,
    test_update_preparation_clones_exact_tag_without_installing,
    test_prepared_installer_failure_is_propagated,
    test_final_version_mismatch_is_reported,
    test_update_prepares_source_before_daemon_removal_and_preserves_daemon_state,
    test_native_ppd_disable_does_not_infer_snap_state,
    test_daemon_install_shell_propagates_start_failure,
    test_daemon_remove_shell_stops_on_stop_failure,
    test_python_install_propagates_daemon_script_failure,
    test_python_remove_does_not_restore_competitors_before_success,
]


def main() -> int:
    failures = []
    for test in TESTS:
        try:
            test()
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    print(f"\n{len(TESTS) - len(failures)}/{len(TESTS)} focused checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
