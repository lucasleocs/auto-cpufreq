from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    AmdPstateInfo,
    BatteryThresholdDiagnostics,
    CpuFreqPolicyInfo,
    DiagnosticsReport,
    IntelPstateInfo,
    PowerServicesInfo,
    format_diagnostics_report,
)


def _system_report():
    return SimpleNamespace(
        battery_info=SimpleNamespace(
            is_ac_plugged=True,
            is_charging=False,
            battery_level=80,
            power_consumption=None,
        ),
        current_gov="powersave",
        current_epp="balance_performance",
        current_epb="balance_performance",
        current_hwp_dynamic_boost=True,
        is_turbo_on=(True, False),
        cpu_avg_temp=42.0,
        cpu_usage=12.5,
        load=0.75,
    )


def _diagnostics(*, policies=(), amd=AmdPstateInfo()):
    return DiagnosticsReport(
        config_path=None,
        governor_override="default",
        turbo_override="auto",
        intel_pstate=IntelPstateInfo(),
        amd_pstate=amd,
        cpufreq_policies=policies,
        battery_thresholds=BatteryThresholdDiagnostics(),
        power_services=PowerServicesInfo(init_system="systemd", services=()),
    )


class Stage2FormatterTests(unittest.TestCase):
    def test_matching_policies_are_compact(self):
        policies = (
            CpuFreqPolicyInfo(
                name="policy0",
                related_cpus=(0, 1),
                scaling_driver="intel_pstate",
                scaling_governor="powersave",
                available_governors=("performance", "powersave"),
                energy_performance_preference="balance_performance",
                available_epp_preferences=(
                    "performance",
                    "balance_performance",
                    "balance_power",
                    "power",
                ),
                scaling_min_freq_khz=400000,
                scaling_max_freq_khz=4600000,
                cpuinfo_min_freq_khz=400000,
                cpuinfo_max_freq_khz=4700000,
            ),
            CpuFreqPolicyInfo(
                name="policy2",
                related_cpus=(2, 3),
                scaling_driver="intel_pstate",
                scaling_governor="powersave",
                available_governors=("performance", "powersave"),
                energy_performance_preference="balance_performance",
                available_epp_preferences=(
                    "performance",
                    "balance_performance",
                    "balance_power",
                    "power",
                ),
                scaling_min_freq_khz=400000,
                scaling_max_freq_khz=4600000,
                cpuinfo_min_freq_khz=400000,
                cpuinfo_max_freq_khz=4700000,
            ),
        )

        output = format_diagnostics_report(
            _system_report(),
            _diagnostics(policies=policies),
        )

        self.assertIn("CPUFreq driver: intel_pstate", output)
        self.assertIn("Governor: powersave", output)
        self.assertIn("EPP: balance_performance", output)
        self.assertIn("Available governors: performance powersave", output)
        self.assertIn(
            "Available EPP preferences: performance balance_performance balance_power power",
            output,
        )
        self.assertIn("CPUFreq scaling limits: 400–4600 MHz", output)
        self.assertIn("CPUFreq hardware limits: 400–4700 MHz", output)
        self.assertNotIn("mixed across policies", output)

    def test_mixed_policies_expose_policy_and_related_cpu_groups(self):
        policies = (
            CpuFreqPolicyInfo(
                name="policy0",
                related_cpus=(0, 1),
                scaling_driver="intel_pstate",
                scaling_governor="performance",
                energy_performance_preference="performance",
                scaling_min_freq_khz=800000,
                scaling_max_freq_khz=4600000,
            ),
            CpuFreqPolicyInfo(
                name="policy2",
                related_cpus=(2, 3),
                scaling_driver="intel_pstate",
                scaling_governor="powersave",
                energy_performance_preference="power",
                scaling_min_freq_khz=400000,
                scaling_max_freq_khz=3000000,
            ),
        )

        output = format_diagnostics_report(
            _system_report(),
            _diagnostics(policies=policies),
        )

        self.assertIn("Governor: mixed across policies", output)
        self.assertIn("performance [policy0; CPUs 0-1]", output)
        self.assertIn("powersave [policy2; CPUs 2-3]", output)
        self.assertIn("EPP: mixed across policies", output)
        self.assertIn("CPUFreq scaling limits: mixed across policies", output)
        self.assertIn("800–4600 MHz [policy0; CPUs 0-1]", output)
        self.assertIn("400–3000 MHz [policy2; CPUs 2-3]", output)

    def test_missing_policy_fields_remain_available_as_diagnostics(self):
        policies = (
            CpuFreqPolicyInfo(
                name="policy0",
                related_cpus=(0, 1),
                scaling_driver="intel_pstate",
            ),
        )

        output = format_diagnostics_report(
            _system_report(),
            _diagnostics(policies=policies),
        )

        self.assertIn("CPUFreq driver: intel_pstate", output)
        self.assertIn("Available governors: Unavailable", output)
        self.assertIn("CPUFreq scaling limits: Unavailable", output)
        self.assertIn("CPUFreq hardware limits: Unavailable", output)

    def test_amd_pstate_is_shown_only_when_observed(self):
        without_amd = format_diagnostics_report(
            _system_report(),
            _diagnostics(),
        )
        with_amd = format_diagnostics_report(
            _system_report(),
            _diagnostics(amd=AmdPstateInfo(mode="guided", preferred_core="enabled")),
        )

        self.assertNotIn("AMD P-State mode:", without_amd)
        self.assertIn("AMD P-State mode: guided", with_amd)
        self.assertIn("AMD P-State preferred core: enabled", with_amd)


if __name__ == "__main__":
    unittest.main()
