from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    BatteryThresholdDiagnostics,
    DiagnosticsReport,
    IntelPstateInfo,
    AmdPstateInfo,
    PpdDiagnostics,
    PpdProfileHold,
    PowerServicesInfo,
    ServiceStatus,
    format_diagnostics_report,
)


def system_report():
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


def diagnostics(ppd, *, provider_active=True):
    provider = ServiceStatus(
        name="power-profiles-daemon",
        installed=True,
        active_state="active" if provider_active else "inactive",
        unit_file_state="enabled",
    )
    return DiagnosticsReport(
        config_path=None,
        governor_override="default",
        turbo_override="auto",
        intel_pstate=IntelPstateInfo(),
        amd_pstate=AmdPstateInfo(),
        cpufreq_policies=(),
        battery_thresholds=BatteryThresholdDiagnostics(),
        power_services=PowerServicesInfo(
            init_system="systemd",
            services=(provider,),
        ),
        ppd=ppd,
    )


class PpdFormatterTests(unittest.TestCase):
    def test_no_ppd_section_when_provider_was_not_active(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics(PpdDiagnostics(), provider_active=False),
        )

        self.assertNotIn("Power Profiles Daemon\n", output)
        self.assertNotIn("Active profile:", output)

    def test_empty_degraded_reason_and_empty_holds_are_explicit(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics(
                PpdDiagnostics(
                    active_profile="balanced",
                    performance_degraded="",
                    active_profile_holds=(),
                )
            ),
        )

        self.assertIn("Power Profiles Daemon", output)
        self.assertIn("Active profile: balanced", output)
        self.assertIn("Performance degraded: No", output)
        self.assertIn("Active profile holds: None", output)

    def test_nonempty_degraded_reason_is_preserved(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics(
                PpdDiagnostics(
                    active_profile="performance",
                    performance_degraded="high-operating-temperature",
                    active_profile_holds=(),
                )
            ),
        )

        self.assertIn(
            "Performance degraded: high-operating-temperature",
            output,
        )

    def test_unavailable_ppd_properties_are_distinct_from_empty_values(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics(PpdDiagnostics()),
        )

        self.assertIn("Active profile: Unavailable", output)
        self.assertIn("Performance degraded: Unavailable", output)
        self.assertIn("Active profile holds: Unavailable", output)

    def test_holds_show_profile_application_and_reason(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics(
                PpdDiagnostics(
                    active_profile="performance",
                    performance_degraded="",
                    active_profile_holds=(
                        PpdProfileHold(
                            application_id="org.example.Render",
                            profile="performance",
                            reason="Rendering video",
                        ),
                        PpdProfileHold(
                            application_id=None,
                            profile="power-saver",
                            reason=None,
                        ),
                    ),
                )
            ),
        )

        self.assertIn("Active profile holds:", output)
        self.assertIn(
            "- Profile: performance; Application: org.example.Render; Reason: Rendering video",
            output,
        )
        self.assertIn(
            "- Profile: power-saver; Application: Unavailable; Reason: Unavailable",
            output,
        )


if __name__ == "__main__":
    unittest.main()
