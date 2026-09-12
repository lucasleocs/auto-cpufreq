from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    BatteryThresholdDiagnostics,
    BatteryThresholdInfo,
    DiagnosticsReport,
    IntelPstateInfo,
    PowerServicesInfo,
    ServiceStatus,
    format_diagnostics_report,
)


def system_report(*, ac=False, charging=False, turbo=(True, False)):
    return SimpleNamespace(
        battery_info=SimpleNamespace(
            is_charging=charging,
            is_ac_plugged=ac,
            battery_level=73,
            power_consumption=7.5,
        ),
        current_gov="powersave",
        current_epp="balance_power",
        current_epb="balance_power",
        current_hwp_dynamic_boost=False,
        is_turbo_on=turbo,
        cpu_usage=12.5,
        load=0.42,
        cpu_avg_temp=51.25,
    )


def diagnostics_report(**overrides):
    values = {
        "config_path": None,
        "governor_override": "default",
        "turbo_override": "auto",
        "intel_pstate": IntelPstateInfo("active", 20, 100),
        "battery_thresholds": BatteryThresholdDiagnostics(),
        "power_services": PowerServicesInfo("systemd", ()),
    }
    values.update(overrides)
    return DiagnosticsReport(**values)


class DiagnosticsFormatterTests(unittest.TestCase):
    def test_formatter_uses_existing_reports_for_fast_power_and_load_state(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics_report(),
        )

        self.assertIn("Configuration: defaults (no config file)", output)
        self.assertIn("AC power: Disconnected", output)
        self.assertIn("Battery status: Discharging", output)
        self.assertIn("Auto-cpufreq profile selection: battery", output)
        self.assertIn("Governor: powersave", output)
        self.assertIn("Governor override: none (profile-controlled)", output)
        self.assertIn("EPP: balance_power", output)
        self.assertIn("EPB: balance_power", output)
        self.assertIn("HWP Dynamic Boost: Disabled", output)
        self.assertIn("Turbo Boost: Enabled", output)
        self.assertIn("Turbo override: auto", output)
        self.assertIn("Intel P-State mode: active", output)
        self.assertIn("Intel P-State min performance: 20%", output)
        self.assertIn("Intel P-State max performance: 100%", output)
        self.assertIn("Total CPU usage: 12.5%", output)
        self.assertIn("Total system load: 0.42", output)
        self.assertIn("Average temp. of all cores: 51.25 °C", output)

    def test_unknown_ac_keeps_existing_charger_profile_semantics(self):
        output = format_diagnostics_report(
            system_report(ac=None, turbo=(None, True)),
            diagnostics_report(),
        )

        self.assertIn("AC power: Unknown", output)
        self.assertIn("Battery status: Unknown", output)
        self.assertIn(
            "Auto-cpufreq profile selection: charger (AC state unknown)",
            output,
        )
        self.assertIn("Turbo Boost: Driver managed", output)

    def test_formatter_reports_multiple_batteries_and_service_states(self):
        output = format_diagnostics_report(
            system_report(ac=True, charging=True),
            diagnostics_report(
                config_path="/etc/auto-cpufreq.conf",
                battery_thresholds=BatteryThresholdDiagnostics(
                    batteries=(
                        BatteryThresholdInfo("BAT0", 40, 80),
                        BatteryThresholdInfo("BAT1", None, 90),
                    ),
                    conservation_mode=True,
                ),
                power_services=PowerServicesInfo(
                    "systemd",
                    (
                        ServiceStatus("auto-cpufreq", False),
                        ServiceStatus("power-profiles-daemon", None),
                        ServiceStatus("tuned", True, "active", "enabled"),
                    ),
                ),
            ),
        )

        self.assertIn("Configuration: /etc/auto-cpufreq.conf", output)
        self.assertIn("AC power: Connected", output)
        self.assertIn("Battery status: Charging", output)
        self.assertIn("Auto-cpufreq profile selection: charger", output)
        self.assertIn("BAT0: start 40%, stop 80%", output)
        self.assertIn("BAT1: start Unavailable, stop 90%", output)
        self.assertIn("Ideapad conservation mode: Enabled", output)
        self.assertIn("auto-cpufreq: Not installed", output)
        self.assertIn("power-profiles-daemon: Unavailable", output)
        self.assertIn("tuned: active, enabled", output)

    def test_non_systemd_service_state_is_explicitly_unavailable(self):
        output = format_diagnostics_report(
            system_report(),
            diagnostics_report(
                power_services=PowerServicesInfo("openrc-init", ()),
            ),
        )

        self.assertIn(
            "Service status: Unavailable (PID 1: openrc-init)",
            output,
        )


if __name__ == "__main__":
    unittest.main()
