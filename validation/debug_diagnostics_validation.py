#!/usr/bin/env python3
import ast
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_cpufreq.modules.diagnostics import (
    AmdPstateInfo,
    IntelPstateInfo,
    PowerServicesInfo,
    ServiceStatus,
    format_debug_diagnostics,
    format_power_services_info,
    read_amd_pstate_info,
    read_cpufreq_policy_info,
    read_debug_override,
    read_intel_pstate_info,
    read_power_services_info,
)


def _report(
    *,
    turbo=(True, False),
    governor="performance",
    epp="balance_performance",
):
    battery = SimpleNamespace(
        is_charging=False,
        is_ac_plugged=True,
        battery_level=80,
        power_consumption=None,
    )
    return SimpleNamespace(
        battery_info=battery,
        cpu_driver="intel_pstate",
        current_gov=governor,
        current_epp=epp,
        current_epb="balance_performance",
        current_hwp_dynamic_boost=False,
        is_turbo_on=turbo,
    )


def _policy(root, name, **files):
    policy = root / name
    policy.mkdir()
    for filename, value in files.items():
        (policy / filename).write_text(str(value) + "\n")
    return policy


def test_debug_path_does_not_deploy_cpufreqctl():
    source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
    tree = ast.parse(source)
    debug_branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "debug"
    ]
    assert len(debug_branches) == 1, "could not isolate the --debug branch"

    calls = {
        node.func.id
        for node in ast.walk(debug_branches[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "cpufreqctl" not in calls, (
        "--debug still invokes cpufreqctl(), which can deploy a helper into "
        "/usr/local/bin"
    )


def test_current_governor_is_grouped_per_cpufreq_policy():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(root, "policy0", scaling_governor="performance")
        _policy(root, "policy4", scaling_governor="powersave")
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert (
        "Governor: mixed across policies "
        "(performance [policy0]; powersave [policy4])"
    ) in output, output


def test_governor_falls_back_when_policy_values_are_unavailable():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(root, "policy0")
        _policy(root, "policy4")
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert "Governor: performance" in output, output


def test_partial_governor_visibility_remains_explicit():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(root, "policy0", scaling_governor="performance")
        _policy(root, "policy4")
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert (
        "Governor: mixed across policies "
        "(performance [policy0]; Unavailable [policy4])"
    ) in output, output


def test_current_epp_is_grouped_per_cpufreq_policy():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(
            root,
            "policy0",
            scaling_governor="performance",
            energy_performance_preference="balance_performance",
        )
        _policy(
            root,
            "policy4",
            scaling_governor="performance",
            energy_performance_preference="power",
        )
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert (
        "EPP: mixed across policies "
        "(balance_performance [policy0]; power [policy4])"
    ) in output, output


def test_epp_falls_back_when_policy_values_are_unavailable():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(root, "policy0", scaling_governor="performance")
        _policy(root, "policy4", scaling_governor="performance")
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert "EPP: balance_performance" in output, output


def test_numeric_epp_value_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _policy(
            root,
            "policy0",
            scaling_governor="performance",
            energy_performance_preference="128",
        )
        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(_report(), cpufreq_policies=policies)

    assert "EPP: 128" in output, output


def test_turbo_control_states():
    cases = (
        ((True, False), "Enabled"),
        ((False, False), "Disabled"),
        ((None, True), "Driver managed"),
        ((None, None), "Unavailable"),
    )
    for turbo, expected in cases:
        output = format_debug_diagnostics(_report(turbo=turbo))
        assert f"Turbo Boost: {expected}" in output, output


def test_cpufreq_missing_root_and_malformed_values_are_safe():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "missing"
        assert read_cpufreq_policy_info(root) == ()

        root.mkdir()
        _policy(
            root,
            "policy10",
            scaling_governor="performance",
            scaling_min_freq="not-an-int",
            scaling_max_freq="4000000",
            scaling_available_governors="performance powersave performance",
        )
        _policy(root, "policy2", scaling_governor="powersave")
        policies = read_cpufreq_policy_info(root)

    assert [policy.name for policy in policies] == ["policy2", "policy10"]
    assert policies[1].scaling_min_freq_khz is None
    assert policies[1].scaling_max_freq_khz == 4000000
    assert policies[1].available_governors == ("performance", "powersave")


def test_pstate_missing_and_malformed_sysfs_is_safe():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        intel = read_intel_pstate_info(root / "intel")
        amd = read_amd_pstate_info(root / "amd")
        assert intel == IntelPstateInfo()
        assert amd == AmdPstateInfo()

        intel_root = root / "intel2"
        intel_root.mkdir()
        (intel_root / "status").write_text("active\n")
        (intel_root / "min_perf_pct").write_text("bad\n")
        (intel_root / "max_perf_pct").write_text("85\n")
        intel = read_intel_pstate_info(intel_root)
        assert intel.mode == "active"
        assert intel.min_perf_pct is None
        assert intel.max_perf_pct == 85

        amd_root = root / "amd2"
        amd_root.mkdir()
        (amd_root / "status").write_text("guided\n")
        (amd_root / "prefcore").write_text("1\n")
        amd = read_amd_pstate_info(amd_root)
        assert amd == AmdPstateInfo(mode="guided", preferred_core="1")


def test_debug_override_rejects_stale_or_corrupt_state():
    allowed = {"default", "powersave", "performance"}
    assert read_debug_override(lambda: "powersave", allowed) == "powersave"
    assert read_debug_override(lambda: "stale", allowed) == "Unavailable"
    assert read_debug_override(lambda: 1, allowed) == "Unavailable"

    def broken():
        raise ValueError("corrupt state")

    assert read_debug_override(broken, allowed) == "Unavailable"


def test_non_systemd_init_never_queries_systemctl_state():
    with tempfile.TemporaryDirectory() as directory:
        init_comm = Path(directory) / "comm"
        init_comm.write_text("openrc\n")
        calls = []

        def capture(unit):
            calls.append(unit)
            raise AssertionError("capture_state must not run without systemd PID 1")

        info = read_power_services_info(init_comm=init_comm, capture_state=capture)

    assert calls == []
    assert info.systemd_pid1 is False
    assert info.init_system == "openrc"
    assert "systemd is not PID 1" in format_power_services_info(info)


def test_systemd_service_states_are_reported_without_collapsing_unit_state():
    states = {
        "auto-cpufreq.service": {
            "load_state": "loaded",
            "active_state": "active",
            "unit_file_state": "enabled",
        },
        "power-profiles-daemon.service": {
            "load_state": "not-found",
            "active_state": "inactive",
            "unit_file_state": "",
        },
        "tuned.service": {
            "load_state": "loaded",
            "active_state": "inactive",
            "unit_file_state": "disabled",
        },
        "tuned-ppd.service": {
            "load_state": "loaded",
            "active_state": "inactive",
            "unit_file_state": "masked",
        },
        "tlp.service": {
            "load_state": "loaded",
            "active_state": "inactive",
            "unit_file_state": "static",
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        init_comm = Path(directory) / "comm"
        init_comm.write_text("systemd\n")
        info = read_power_services_info(
            init_comm=init_comm,
            capture_state=lambda unit: states[unit],
        )

    formatted = format_power_services_info(info)
    assert "auto-cpufreq: active / enabled" in formatted
    assert "power-profiles-daemon: Not installed" in formatted
    assert "tuned: inactive / disabled" in formatted
    assert "tuned-ppd: inactive / masked" in formatted
    assert "TLP: inactive / static" in formatted


def test_unreadable_systemd_service_state_is_unavailable():
    with tempfile.TemporaryDirectory() as directory:
        init_comm = Path(directory) / "comm"
        init_comm.write_text("systemd\n")
        info = read_power_services_info(
            init_comm=init_comm,
            capture_state=lambda _unit: None,
        )

    assert all(service.status == "Unavailable" for service in info.services)


def test_snap_service_state_and_confinement_reporting():
    stdout = (
        "Service Startup Current Notes\n"
        "auto-cpufreq.service enabled active -\n"
    )

    def runner(args, **kwargs):
        assert args == ["snapctl", "services", "auto-cpufreq.service"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    with tempfile.TemporaryDirectory() as directory:
        init_comm = Path(directory) / "comm"
        init_comm.write_text("systemd\n")
        info = read_power_services_info(
            init_comm=init_comm,
            is_snap=True,
            snap_runner=runner,
        )

    statuses = {service.name: service.status for service in info.services}
    assert statuses["auto-cpufreq"] == "active / enabled"
    assert statuses["power-profiles-daemon"] == "Unavailable (Snap confinement)"
    assert statuses["tuned"] == "Unavailable (Snap confinement)"
    assert statuses["tuned-ppd"] == "Unavailable (Snap confinement)"
    assert statuses["TLP"] == "Unavailable (Snap confinement)"


def test_snap_service_query_failure_is_safe():
    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="denied")

    with tempfile.TemporaryDirectory() as directory:
        init_comm = Path(directory) / "comm"
        init_comm.write_text("systemd\n")
        info = read_power_services_info(
            init_comm=init_comm,
            is_snap=True,
            snap_runner=runner,
        )

    assert info.services[0] == ServiceStatus("auto-cpufreq", "Unavailable")


def test_updater_still_uses_literal_version():
    source = (ROOT / "auto_cpufreq/core.py").read_text()
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    check_for_update = functions["check_for_update"]
    called = {
        node.func.id
        for node in ast.walk(check_for_update)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "get_literal_version" in called
    assert "get_formatted_version" not in called


def test_diagnostics_subprocess_is_query_only():
    source = (ROOT / "auto_cpufreq/modules/diagnostics.py").read_text()
    tree = ast.parse(source)
    command_literals = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        values = []
        for element in node.args[0].elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                values.append(element.value)
        if values:
            command_literals.append(tuple(values))

    assert command_literals == [("snapctl", "services", "auto-cpufreq.service")]


def main():
    tests = [
        test_debug_path_does_not_deploy_cpufreqctl,
        test_current_governor_is_grouped_per_cpufreq_policy,
        test_governor_falls_back_when_policy_values_are_unavailable,
        test_partial_governor_visibility_remains_explicit,
        test_current_epp_is_grouped_per_cpufreq_policy,
        test_epp_falls_back_when_policy_values_are_unavailable,
        test_numeric_epp_value_is_preserved,
        test_turbo_control_states,
        test_cpufreq_missing_root_and_malformed_values_are_safe,
        test_pstate_missing_and_malformed_sysfs_is_safe,
        test_debug_override_rejects_stale_or_corrupt_state,
        test_non_systemd_init_never_queries_systemctl_state,
        test_systemd_service_states_are_reported_without_collapsing_unit_state,
        test_unreadable_systemd_service_state_is_unavailable,
        test_snap_service_state_and_confinement_reporting,
        test_snap_service_query_failure_is_safe,
        test_updater_still_uses_literal_version,
        test_diagnostics_subprocess_is_query_only,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"PASS {test.__name__}")

    if failed:
        print(f"{failed} diagnostics validation check(s) failed")
        return 1
    print(f"all {len(tests)} diagnostics validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
