#!/usr/bin/env python3
import ast
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from auto_cpufreq.modules.diagnostics import (
    format_debug_diagnostics,
    read_cpufreq_policy_info,
)


def _report(*, turbo=(True, False)):
    battery = SimpleNamespace(
        is_charging=False,
        is_ac_plugged=True,
        battery_level=80,
        power_consumption=None,
    )
    return SimpleNamespace(
        battery_info=battery,
        cpu_driver="intel_pstate",
        current_gov="performance",
        current_epp="balance_performance",
        current_epb="balance_performance",
        current_hwp_dynamic_boost=False,
        is_turbo_on=turbo,
    )


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
        for name, governor in (
            ("policy0", "performance"),
            ("policy4", "powersave"),
        ):
            policy = root / name
            policy.mkdir()
            (policy / "scaling_governor").write_text(governor + "\n")

        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(
            _report(),
            cpufreq_policies=policies,
        )

    assert (
        "Governor: mixed across policies "
        "(performance [policy0]; powersave [policy4])"
    ) in output, output


def test_current_epp_is_grouped_per_cpufreq_policy():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, epp in (
            ("policy0", "balance_performance"),
            ("policy4", "power"),
        ):
            policy = root / name
            policy.mkdir()
            (policy / "energy_performance_preference").write_text(epp + "\n")

        policies = read_cpufreq_policy_info(root)
        output = format_debug_diagnostics(
            _report(),
            cpufreq_policies=policies,
        )

    assert (
        "EPP: mixed across policies "
        "(balance_performance [policy0]; power [policy4])"
    ) in output, output


def test_turbo_control_is_described_as_enabled_not_active():
    enabled = format_debug_diagnostics(_report(turbo=(True, False)))
    disabled = format_debug_diagnostics(_report(turbo=(False, False)))
    assert "Turbo Boost: Enabled" in enabled, enabled
    assert "Turbo Boost: Disabled" in disabled, disabled


def main():
    tests = [
        test_debug_path_does_not_deploy_cpufreqctl,
        test_current_governor_is_grouped_per_cpufreq_policy,
        test_current_epp_is_grouped_per_cpufreq_policy,
        test_turbo_control_is_described_as_enabled_not_active,
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
    print("all diagnostics validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
