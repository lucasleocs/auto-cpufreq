import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    BatteryThresholdDiagnostics,
    DiagnosticsReport,
    IntelPstateInfo,
    PowerServicesInfo,
    collect_diagnostics,
    format_diagnostics_report,
    read_battery_threshold_diagnostics,
    read_debug_override,
    read_intel_pstate_info,
    read_power_services_info,
)


class DiagnosticsCollectorTests(unittest.TestCase):
    def test_override_reader_rejects_corrupt_or_non_string_values(self):
        self.assertEqual(
            read_debug_override(lambda: "performance", {"default", "performance"}),
            "performance",
        )
        self.assertIsNone(read_debug_override(lambda: 1, {"default", "performance"}))

        def broken():
            raise ValueError("corrupt pickle")

        self.assertIsNone(read_debug_override(broken, {"default", "performance"}))

    def test_intel_pstate_reader_is_fail_soft(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").write_text("active\n")
            (root / "min_perf_pct").write_text("not-an-int\n")
            (root / "max_perf_pct").write_text("100\n")

            self.assertEqual(
                read_intel_pstate_info(root),
                IntelPstateInfo(mode="active", min_perf_pct=None, max_perf_pct=100),
            )

    def test_battery_diagnostics_prefer_kernel_abi_and_keep_legacy_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "power_supply"
            root.mkdir()

            bat0 = root / "BAT0"
            bat0.mkdir()
            (bat0 / "type").write_text("Battery\n")
            (bat0 / "charge_control_start_threshold").write_text("40\n")
            (bat0 / "charge_control_end_threshold").write_text("80\n")
            (bat0 / "charge_start_threshold").write_text("1\n")
            (bat0 / "charge_stop_threshold").write_text("2\n")

            bat1 = root / "CMB1"
            bat1.mkdir()
            (bat1 / "type").write_text("Battery\n")
            (bat1 / "charge_start_threshold").write_text("55\n")
            (bat1 / "charge_stop_threshold").write_text("90\n")

            ac = root / "AC"
            ac.mkdir()
            (ac / "type").write_text("Mains\n")

            result = read_battery_threshold_diagnostics(
                power_supply_root=root,
                ideapad_roots=(),
            )

            self.assertEqual([item.name for item in result.batteries], ["BAT0", "CMB1"])
            self.assertEqual((result.batteries[0].start_threshold, result.batteries[0].stop_threshold), (40, 80))
            self.assertEqual((result.batteries[1].start_threshold, result.batteries[1].stop_threshold), (55, 90))

    def test_battery_diagnostics_read_conservation_mode_without_writes(self):
        with TemporaryDirectory() as tmp:
            power = Path(tmp) / "power_supply"
            power.mkdir()
            bat = power / "BAT0"
            bat.mkdir()
            (bat / "type").write_text("Battery\n")

            ideapad = Path(tmp) / "ideapad_acpi"
            ideapad.mkdir()
            (ideapad / "conservation_mode").write_text("1\n")

            result = read_battery_threshold_diagnostics(
                power_supply_root=power,
                ideapad_roots=(ideapad,),
            )
            self.assertIs(result.conservation_mode, True)

    def test_non_systemd_hosts_do_not_query_systemctl(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("openrc-init\n")

            calls = []
            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: calls.append(unit),
            )

            self.assertEqual(calls, [])
            self.assertEqual(result.init_system, "openrc-init")
            self.assertEqual(result.services, ())

    def test_service_state_distinguishes_missing_from_query_failure(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")

            states = {
                "auto-cpufreq.service": {
                    "load_state": "not-found",
                    "active_state": "inactive",
                    "unit_file_state": "",
                },
                "power-profiles-daemon.service": None,
                "tuned.service": {
                    "load_state": "loaded",
                    "active_state": "active",
                    "unit_file_state": "enabled",
                },
                "tlp.service": {
                    "load_state": "loaded",
                    "active_state": "inactive",
                    "unit_file_state": "disabled",
                },
            }

            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: states[unit],
            )
            by_name = {item.name: item for item in result.services}

            self.assertIs(by_name["auto-cpufreq"].installed, False)
            self.assertIsNone(by_name["power-profiles-daemon"].installed)
            self.assertIs(by_name["tuned"].installed, True)
            self.assertEqual(by_name["tuned"].active_state, "active")

    def test_collect_diagnostics_returns_one_structured_report(self):
        fake_system_report = SimpleNamespace()
        result = collect_diagnostics(
            fake_system_report,
            config_path="/tmp/auto-cpufreq.conf",
            governor_override_getter=lambda: "default",
            turbo_override_getter=lambda: "auto",
            intel_pstate_root=Path("/nonexistent"),
            power_supply_root=Path("/nonexistent"),
            ideapad_roots=(),
            init_comm=Path("/nonexistent"),
            capture_state=lambda unit: None,
        )

        self.assertIsInstance(result, DiagnosticsReport)
        self.assertEqual(result.config_path, "/tmp/auto-cpufreq.conf")
        self.assertEqual(result.governor_override, "default")
        self.assertEqual(result.turbo_override, "auto")
        self.assertIsInstance(result.intel_pstate, IntelPstateInfo)
        self.assertIsInstance(result.power_services, PowerServicesInfo)
        self.assertIsInstance(result.battery_thresholds, BatteryThresholdDiagnostics)


class DebugCliInvariantTests(unittest.TestCase):
    def test_debug_branch_does_not_call_legacy_mutating_or_duplicate_collectors(self):
        source = Path("auto_cpufreq/bin/auto_cpufreq.py").read_text()
        tree = ast.parse(source)

        debug_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "debug":
                debug_body = node.body
                break

        self.assertIsNotNone(debug_body)
        calls = {
            node.func.id
            for statement in debug_body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("collect_diagnostics", calls)
        self.assertNotIn("cpufreqctl", calls)
        self.assertNotIn("battery_get_thresholds", calls)
        self.assertNotIn("charging", calls)
        self.assertNotIn("get_current_gov", calls)
        self.assertNotIn("get_turbo", calls)
        self.assertNotIn("get_load", calls)


if __name__ == "__main__":
    unittest.main()
