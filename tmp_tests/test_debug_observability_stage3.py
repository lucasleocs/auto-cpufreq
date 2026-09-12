from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    PpdDiagnostics,
    PpdProfileHold,
    read_power_profiles_daemon_info,
)


CURRENT = (
    "org.freedesktop.UPower.PowerProfiles",
    "/org/freedesktop/UPower/PowerProfiles",
    "org.freedesktop.UPower.PowerProfiles",
)
LEGACY = (
    "net.hadess.PowerProfiles",
    "/net/hadess/PowerProfiles",
    "net.hadess.PowerProfiles",
)


def result(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


class PpdTransportTests(unittest.TestCase):
    def test_current_namespace_reads_strings_and_profile_holds(self):
        calls = []
        outputs = {
            "ActiveProfile": 's "balanced"\n',
            "PerformanceDegraded": 's ""\n',
            "ActiveProfileHolds": (
                'aa{sv} 2 '
                '3 "ApplicationId" s "org.example.Render" '
                '"Profile" s "performance" '
                '"Reason" s "Rendering video" '
                '3 "ApplicationId" s "org.example.Power" '
                '"Profile" s "power-saver" '
                '"Reason" s "Low battery"\n'
            ),
        }

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return result(outputs[args[-1]])

        info = read_power_profiles_daemon_info(runner=runner)

        self.assertEqual(
            info,
            PpdDiagnostics(
                active_profile="balanced",
                performance_degraded="",
                active_profile_holds=(
                    PpdProfileHold(
                        application_id="org.example.Render",
                        profile="performance",
                        reason="Rendering video",
                    ),
                    PpdProfileHold(
                        application_id="org.example.Power",
                        profile="power-saver",
                        reason="Low battery",
                    ),
                ),
            ),
        )
        self.assertEqual(len(calls), 3)
        for args, kwargs in calls:
            self.assertEqual(args[:3], ["busctl", "--system", "get-property"])
            self.assertEqual(tuple(args[3:6]), CURRENT)
            self.assertTrue(kwargs["capture_output"])
            self.assertTrue(kwargs["text"])
            self.assertFalse(kwargs["check"])

    def test_empty_holds_are_distinct_from_unavailable(self):
        outputs = {
            "ActiveProfile": 's "performance"\n',
            "PerformanceDegraded": 's "high-operating-temperature"\n',
            "ActiveProfileHolds": "aa{sv} 0\n",
        }

        info = read_power_profiles_daemon_info(
            runner=lambda args, **kwargs: result(outputs[args[-1]])
        )

        self.assertEqual(info.active_profile, "performance")
        self.assertEqual(info.performance_degraded, "high-operating-temperature")
        self.assertEqual(info.active_profile_holds, ())

    def test_legacy_namespace_is_used_only_after_current_active_profile_fails(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            service = args[3]
            prop = args[-1]
            if service == CURRENT[0]:
                self.assertEqual(prop, "ActiveProfile")
                return result(returncode=1)
            outputs = {
                "ActiveProfile": 's "power-saver"\n',
                "PerformanceDegraded": 's ""\n',
                "ActiveProfileHolds": "aa{sv} 0\n",
            }
            return result(outputs[prop])

        info = read_power_profiles_daemon_info(runner=runner)

        self.assertEqual(info.active_profile, "power-saver")
        self.assertEqual(tuple(calls[0][3:6]), CURRENT)
        self.assertEqual(tuple(calls[1][3:6]), LEGACY)
        self.assertTrue(all(tuple(call[3:6]) == LEGACY for call in calls[1:]))

    def test_later_property_failure_preserves_successful_properties(self):
        def runner(args, **kwargs):
            prop = args[-1]
            if prop == "ActiveProfile":
                return result('s "balanced"\n')
            if prop == "PerformanceDegraded":
                return result(returncode=1)
            return result("aa{sv} 0\n")

        info = read_power_profiles_daemon_info(runner=runner)

        self.assertEqual(info.active_profile, "balanced")
        self.assertIsNone(info.performance_degraded)
        self.assertEqual(info.active_profile_holds, ())

    def test_malformed_scalar_or_holds_are_fail_soft(self):
        scalar_calls = []

        def bad_scalar(args, **kwargs):
            scalar_calls.append(args)
            return result("u 7\n")

        self.assertEqual(
            read_power_profiles_daemon_info(runner=bad_scalar),
            PpdDiagnostics(),
        )
        self.assertEqual(len(scalar_calls), 2)

        def bad_holds(args, **kwargs):
            prop = args[-1]
            if prop == "ActiveProfile":
                return result('s "balanced"\n')
            if prop == "PerformanceDegraded":
                return result('s ""\n')
            return result('aa{sv} 1 3 "ApplicationId" s "app"\n')

        info = read_power_profiles_daemon_info(runner=bad_holds)
        self.assertEqual(info.active_profile, "balanced")
        self.assertEqual(info.performance_degraded, "")
        self.assertIsNone(info.active_profile_holds)

    def test_runner_exception_is_fail_soft(self):
        def runner(*args, **kwargs):
            raise FileNotFoundError("busctl")

        self.assertEqual(
            read_power_profiles_daemon_info(runner=runner),
            PpdDiagnostics(),
        )


if __name__ == "__main__":
    unittest.main()
