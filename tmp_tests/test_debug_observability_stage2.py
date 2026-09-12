from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    AmdPstateInfo,
    CpuFreqPolicyInfo,
    read_amd_pstate_info,
    read_cpufreq_policy_info,
    read_power_services_info,
)


class CpuFreqPolicyCollectorTests(unittest.TestCase):
    @staticmethod
    def _write(root: Path, policy: str, **files: str) -> None:
        path = root / policy
        path.mkdir()
        for name, value in files.items():
            (path / name).write_text(f"{value}\n")

    def test_policies_sort_numerically_and_preserve_per_policy_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "policy10",
                related_cpus="10-11",
                scaling_driver="amd-pstate-epp",
                scaling_governor="powersave",
                scaling_available_governors="performance powersave",
                energy_performance_preference="power",
                energy_performance_available_preferences="performance balance_performance power",
                scaling_min_freq="400000",
                scaling_max_freq="3000000",
                cpuinfo_min_freq="400000",
                cpuinfo_max_freq="4200000",
            )
            self._write(
                root,
                "policy2",
                related_cpus="0-1,4 6",
                scaling_driver="intel_pstate",
                scaling_governor="performance",
                scaling_available_governors="performance powersave",
                energy_performance_preference="performance",
                energy_performance_available_preferences="performance balance_performance balance_power power",
                scaling_min_freq="800000",
                scaling_max_freq="4600000",
                cpuinfo_min_freq="400000",
                cpuinfo_max_freq="4700000",
            )

            policies = read_cpufreq_policy_info(root)

            self.assertEqual([item.name for item in policies], ["policy2", "policy10"])
            self.assertEqual(policies[0].related_cpus, (0, 1, 4, 6))
            self.assertEqual(policies[0].scaling_driver, "intel_pstate")
            self.assertEqual(policies[0].scaling_governor, "performance")
            self.assertEqual(
                policies[0].available_governors,
                ("performance", "powersave"),
            )
            self.assertEqual(policies[0].energy_performance_preference, "performance")
            self.assertEqual(
                policies[0].available_epp_preferences,
                ("performance", "balance_performance", "balance_power", "power"),
            )
            self.assertEqual(policies[0].scaling_min_freq_khz, 800000)
            self.assertEqual(policies[0].scaling_max_freq_khz, 4600000)
            self.assertEqual(policies[0].cpuinfo_min_freq_khz, 400000)
            self.assertEqual(policies[0].cpuinfo_max_freq_khz, 4700000)

    def test_missing_and_malformed_attributes_are_fail_soft(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root,
                "policy0",
                related_cpus="bad-range",
                scaling_governor="powersave",
                scaling_min_freq="not-an-int",
            )

            policies = read_cpufreq_policy_info(root)

            self.assertEqual(
                policies,
                (
                    CpuFreqPolicyInfo(
                        name="policy0",
                        related_cpus=None,
                        scaling_governor="powersave",
                    ),
                ),
            )

    def test_absent_cpufreq_root_returns_empty_tuple(self):
        self.assertEqual(read_cpufreq_policy_info(Path("/does/not/exist")), ())


class AmdPstateCollectorTests(unittest.TestCase):
    def test_reads_documented_global_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").write_text("guided\n")
            (root / "prefcore").write_text("enabled\n")

            self.assertEqual(
                read_amd_pstate_info(root),
                AmdPstateInfo(mode="guided", preferred_core="enabled"),
            )

    def test_unknown_future_state_is_preserved_for_debugging(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").write_text("future-mode\n")

            self.assertEqual(
                read_amd_pstate_info(root),
                AmdPstateInfo(mode="future-mode", preferred_core=None),
            )

    def test_missing_root_is_fail_soft(self):
        self.assertEqual(
            read_amd_pstate_info(Path("/does/not/exist")),
            AmdPstateInfo(),
        )


class PowerServiceCollectorTests(unittest.TestCase):
    def test_systemd_host_includes_tuned_ppd(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")
            calls = []

            def capture_state(unit):
                calls.append(unit)
                return {
                    "load_state": "loaded",
                    "active_state": "inactive",
                    "unit_file_state": "disabled",
                }

            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=capture_state,
            )

            self.assertEqual(
                calls,
                [
                    "auto-cpufreq.service",
                    "power-profiles-daemon.service",
                    "tuned.service",
                    "tuned-ppd.service",
                    "tlp.service",
                ],
            )
            self.assertEqual(
                [service.name for service in result.services],
                ["auto-cpufreq", "power-profiles-daemon", "tuned", "tuned-ppd", "TLP"],
            )

    def test_snap_queries_only_its_own_daemon(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")
            snap_calls = []
            host_calls = []

            def snap_runner(args, **kwargs):
                snap_calls.append((args, kwargs))
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "Service               Startup  Current  Notes\n"
                        "auto-cpufreq.service  enabled  active   -\n"
                    ),
                )

            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: host_calls.append(unit),
                is_snap=True,
                snap_runner=snap_runner,
            )

            self.assertEqual(host_calls, [])
            self.assertEqual(
                snap_calls[0][0],
                ["snapctl", "services", "auto-cpufreq.service"],
            )
            self.assertTrue(snap_calls[0][1]["capture_output"])
            self.assertTrue(snap_calls[0][1]["text"])
            self.assertFalse(snap_calls[0][1]["check"])

            by_name = {service.name: service for service in result.services}
            daemon = by_name["auto-cpufreq"]
            self.assertIs(daemon.installed, True)
            self.assertEqual(daemon.active_state, "active")
            self.assertEqual(daemon.unit_file_state, "enabled")

            for name in ("power-profiles-daemon", "tuned", "tuned-ppd", "TLP"):
                self.assertIsNone(by_name[name].installed)
                self.assertEqual(
                    by_name[name].detail,
                    "Unavailable (Snap confinement)",
                )

    def test_snap_query_failure_is_fail_soft(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")

            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: self.fail("host systemctl must not be queried"),
                is_snap=True,
                snap_runner=lambda *args, **kwargs: SimpleNamespace(
                    returncode=1,
                    stdout="",
                ),
            )

            by_name = {service.name: service for service in result.services}
            self.assertIsNone(by_name["auto-cpufreq"].installed)
            self.assertEqual(by_name["auto-cpufreq"].detail, "Unavailable")
            self.assertEqual(
                by_name["power-profiles-daemon"].detail,
                "Unavailable (Snap confinement)",
            )

    def test_non_systemd_non_snap_host_does_not_query_systemctl(self):
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


if __name__ == "__main__":
    unittest.main()
