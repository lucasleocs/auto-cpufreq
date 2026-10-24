"""Compatibility tests for the single source of monitor suggestion rules.

No privileged commands, hardware sampling, daemon operations or GTK required.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_cpufreq.modules import system_info as system_module
from auto_cpufreq.modules.report_recommendations import (
    governor_candidate, turbo_candidate,
)


class LegacySuggestionCompatibilityTests(unittest.TestCase):
    def test_governor_legacy_wrapper_uses_shared_rule(self):
        governors = system_module.AVAILABLE_GOVERNORS_SORTED
        self.assertTrue(governors)
        for ac in (True, False, None):
            with self.subTest(ac=ac):
                snapshot = SimpleNamespace(
                    battery_info=SimpleNamespace(is_ac_plugged=ac)
                )
                expected = governor_candidate(ac, governors)
                self.assertEqual(
                    system_module.SystemInfo.governor_suggestion(snapshot),
                    expected,
                )
                with patch.object(
                    system_module.SystemInfo, "battery_info",
                    return_value=snapshot.battery_info,
                ):
                    self.assertEqual(
                        system_module.SystemInfo.governor_suggestion(),
                        expected,
                    )

    def test_turbo_legacy_wrapper_preserves_thresholds(self):
        for usage in (0.0, 19.9, 20.0, 24.0, 99.0):
            for temp in (20.0, 70.0, 90.0):
                with self.subTest(usage=usage, temp=temp):
                    snapshot = SimpleNamespace(
                        cpu_usage=usage, cpu_avg_temp=temp, cores_info=[]
                    )
                    expected = turbo_candidate(usage, temp)
                    self.assertEqual(
                        system_module.SystemInfo.turbo_on_suggestion(snapshot),
                        expected,
                    )
                    with patch.object(
                        system_module.SystemInfo, "cpu_usage",
                        return_value=usage,
                    ):
                        with patch.object(
                            system_module.SystemInfo, "avg_temp",
                            return_value=temp,
                        ):
                            self.assertEqual(
                                system_module.SystemInfo.turbo_on_suggestion(),
                                expected,
                            )

    def test_turbo_legacy_wrapper_uses_snapshot_temperature_fallback(self):
        snapshot = SimpleNamespace(
            cpu_usage=10.0,
            cpu_avg_temp=None,
            cores_info=[
                SimpleNamespace(temperature=60.0),
                SimpleNamespace(temperature=80.0),
                SimpleNamespace(temperature=0.0),
            ],
        )
        with patch.object(
            system_module.SystemInfo, "avg_temp",
            side_effect=AssertionError("Snapshot should avoid hardware reads"),
        ):
            self.assertEqual(
                system_module.SystemInfo.turbo_on_suggestion(snapshot),
                turbo_candidate(10.0, 70.0),
            )

    def test_current_monitor_evaluation_uses_shared_candidates(self):
        for ac in (True, False):
            report = SimpleNamespace(
                battery_info=SimpleNamespace(is_ac_plugged=ac),
                current_gov=system_module.AVAILABLE_GOVERNORS_SORTED[0],
                is_turbo_on=(False, False),
                cpu_usage=21.0,
                cpu_avg_temp=50.0,
                cores_info=[],
            )
            with self.subTest(ac=ac):
                values = system_module.SystemInfo.monitor_recommendations(report)
                self.assertEqual(
                    values.governor.suggested,
                    system_module.SystemInfo.governor_suggestion(report),
                )
                self.assertEqual(
                    values.turbo.suggested,
                    system_module.SystemInfo.turbo_on_suggestion(report),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
