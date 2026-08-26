#!/usr/bin/env python3
"""Temporary focused regression harness for the bounded PR #965 scope.

Work-branch only; removed before the public PR is rebuilt.
"""

from __future__ import annotations

import ast
import importlib.util
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
    if not path.exists():
        raise AssertionError(f"missing {relative_path}")
    tree = ast.parse(path.read_text(), filename=str(path))
    node = next(
        (item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name),
        None,
    )
    if node is None:
        raise AssertionError(f"missing function {name} in {relative_path}")
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


def load_update_module():
    path = ROOT / "auto_cpufreq/update.py"
    if not path.exists():
        raise AssertionError("missing auto_cpufreq/update.py")
    spec = importlib.util.spec_from_file_location("pr965_update_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def extract_bash_function(relative_path: str, name: str) -> str:
    text = (ROOT / relative_path).read_text()
    marker = f"function {name} {{"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"missing bash function {name}")
    pos, depth, seen_open = start, 0, False
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


def test_update_status_preserves_exact_release_tag():
    update = load_update_module()

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"tag_name": "v9.9.9"}

    update.get_literal_version = lambda: "1.2.3"
    update.requests = SimpleNamespace(
        get=lambda *_args, **_kwargs: Response(),
        RequestException=Exception,
    )
    status = update.get_update_status()
    assert status.latest_version == "v9.9.9", status
    assert status.update_available is True, status


def test_update_preparation_clones_exact_tag_before_installing():
    update = load_update_module()
    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        destination = Path(cmd[-1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
        return SimpleNamespace(returncode=0, stderr="")

    update.run = fake_run
    with tempfile.TemporaryDirectory() as tmp:
        source = update.prepare_release_source(tmp, "v9.9.9")
        assert (source / "auto-cpufreq-installer").is_file()
    clone = calls[0]
    assert clone[:2] == ["git", "clone"], clone
    assert clone[clone.index("--branch") + 1] == "v9.9.9", clone
    assert clone[clone.index("--depth") + 1] == "1", clone


def test_release_installer_failure_is_propagated():
    update = load_update_module()
    update.run = lambda *_args, **_kwargs: SimpleNamespace(returncode=7, stderr="failed")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp)
        (source / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
        try:
            update.install_release_source(source)
        except update.UpdateError:
            return
    raise AssertionError("release installer failure was not propagated")


def test_update_flow_prepares_first_preserves_daemon_state_and_verifies_version():
    source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
    start = source.find("def _apply_release_update")
    if start < 0:
        raise AssertionError("missing _apply_release_update")
    end = source.find("\n\nclass ", start)
    if end < 0:
        end = source.find("\n\n@click.command", start)
    block = source[start:end]
    assert "daemon_was_installed" in block
    assert "prepare_release_source(" in block
    assert block.index("prepare_release_source(") < block.index("daemon_remove_script") + block[block.index("daemon_remove_script"):].find("run([daemon_remove_script])")
    assert "if daemon_was_installed" in block
    assert "release_version_matches(" in block


def test_native_ppd_disable_uses_service_state_not_snap_guessing():
    calls, disabled, output = [], [], []

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
    disable = load_function("auto_cpufreq/power_helper.py", "gnome_power_svc_disable", namespace)
    disable()
    rendered = "\n".join(output).lower()
    assert disabled, "active PPD was not disabled"
    assert not any(isinstance(cmd, list) and cmd and cmd[0] == "snap" for cmd in calls)
    assert "snap package" not in rendered


def test_daemon_install_shell_propagates_start_failure():
    function = extract_bash_function("scripts/auto-cpufreq-install.sh", "auto_cpufreq_install")
    script = f"""
{function}
start_cmd() {{ return 7; }}
enable_cmd() {{ return 0; }}
auto_cpufreq_install systemd start_cmd enable_cmd
"""
    result = subprocess.run(["bash", "-c", script], capture_output=True)
    assert result.returncode != 0, "failed daemon start was masked"


def test_daemon_remove_shell_stops_before_destructive_steps_on_failure():
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
        result = subprocess.run(["bash", "-c", script], capture_output=True)
        assert result.returncode != 0, "failed daemon stop was masked"
        assert unit.exists(), "unit removed even though stop failed"


def test_python_lifecycle_helper_propagates_failure():
    namespace = {
        "run": lambda *_args, **_kwargs: SimpleNamespace(returncode=7),
        "click": FAKE_CLICK,
    }
    helper = load_function("auto_cpufreq/core.py", "_run_daemon_lifecycle_script", namespace)
    try:
        helper("/usr/local/bin/auto-cpufreq-install", "install")
    except HarnessError:
        return
    raise AssertionError("Python lifecycle helper accepted non-zero status")


def test_python_remove_restores_competitors_only_after_successful_remove():
    source = (ROOT / "auto_cpufreq/core.py").read_text()
    start = source.index("def remove_daemon():")
    end = source.index("\ndef gov_check", start)
    block = source[start:end]
    lifecycle = block.find('_run_daemon_lifecycle_script("/usr/local/bin/auto-cpufreq-remove"')
    if lifecycle < 0:
        raise AssertionError("remove_daemon does not use checked lifecycle execution")
    for call in ("bluetooth_enable()", "gnome_power_svc_enable()", "tuned_svc_enable()"):
        assert lifecycle < block.index(call), f"{call} occurs before daemon removal succeeds"


TESTS = [
    test_update_status_preserves_exact_release_tag,
    test_update_preparation_clones_exact_tag_before_installing,
    test_release_installer_failure_is_propagated,
    test_update_flow_prepares_first_preserves_daemon_state_and_verifies_version,
    test_native_ppd_disable_uses_service_state_not_snap_guessing,
    test_daemon_install_shell_propagates_start_failure,
    test_daemon_remove_shell_stops_before_destructive_steps_on_failure,
    test_python_lifecycle_helper_propagates_failure,
    test_python_remove_restores_competitors_only_after_successful_remove,
]


def main():
    failed = []
    for test in TESTS:
        try:
            test()
        except Exception as exc:
            failed.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} focused checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
