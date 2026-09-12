from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    PpdDiagnostics,
    PpdProfileHold,
    collect_diagnostics,
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


def service_state(active_state="inactive"):
    return {
        "load_state": "loaded",
        "active_state": active_state,
        "unit_file_state": "enabled",
    }


def missing_service_state():
    return {
        "load_state": "not-found",
        "active_state": "inactive",
        "unit_file_state": "",
    }


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


class PpdIntegrationTests(unittest.TestCase):
    @staticmethod
    def _collect(
        init_comm,
        capture_state,
        ppd_runner,
        *,
        is_snap=False,
        snap_runner=None,
    ):
        kwargs = {}
        if snap_runner is not None:
            kwargs["snap_runner"] = snap_runner
        return collect_diagnostics(
            SimpleNamespace(),
            config_path=None,
            governor_override_getter=lambda: "default",
            turbo_override_getter=lambda: "auto",
            intel_pstate_root=Path("/does/not/exist"),
            amd_pstate_root=Path("/does/not/exist"),
            cpufreq_policy_root=Path("/does/not/exist"),
            power_supply_root=Path("/does/not/exist"),
            ideapad_roots=(),
            init_comm=init_comm,
            capture_state=capture_state,
            is_snap=is_snap,
            ppd_runner=ppd_runner,
            **kwargs,
        )

    def test_active_ppd_provider_enables_dbus_collection(self):
        for active_unit in (
            "power-profiles-daemon.service",
            "tuned-ppd.service",
        ):
            with self.subTest(active_unit=active_unit), TemporaryDirectory() as tmp:
                init_comm = Path(tmp) / "comm"
                init_comm.write_text("systemd\n")
                ppd_calls = []

                def capture_state(unit):
                    if unit == active_unit:
                        return service_state("active")
                    return missing_service_state()

                outputs = {
                    "ActiveProfile": 's "balanced"\n',
                    "PerformanceDegraded": 's ""\n',
                    "ActiveProfileHolds": "aa{sv} 0\n",
                }

                def ppd_runner(args, **kwargs):
                    ppd_calls.append(args)
                    return result(outputs[args[-1]])

                report = self._collect(
                    init_comm,
                    capture_state,
                    ppd_runner,
                )

                self.assertEqual(report.ppd.active_profile, "balanced")
                self.assertEqual(len(ppd_calls), 3)

    def test_inactive_provider_does_not_trigger_dbus(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")
            ppd_calls = []

            report = self._collect(
                init_comm,
                lambda unit: service_state("inactive"),
                lambda *args, **kwargs: ppd_calls.append(args),
            )

            self.assertEqual(ppd_calls, [])
            self.assertEqual(report.ppd, PpdDiagnostics())

    def test_non_systemd_host_does_not_trigger_dbus(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("openrc-init\n")
            ppd_calls = []

            report = self._collect(
                init_comm,
                lambda unit: self.fail("systemctl must not be queried"),
                lambda *args, **kwargs: ppd_calls.append(args),
            )

            self.assertEqual(ppd_calls, [])
            self.assertEqual(report.ppd, PpdDiagnostics())

    def test_snap_path_does_not_trigger_host_ppd_dbus(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")
            ppd_calls = []

            snap_output = (
                "Service               Startup  Current  Notes\n"
                "auto-cpufreq.service  enabled  active   -\n"
            )
            report = self._collect(
                init_comm,
                lambda unit: self.fail("host systemctl must not be queried"),
                lambda *args, **kwargs: ppd_calls.append(args),
                is_snap=True,
                snap_runner=lambda *args, **kwargs: result(snap_output),
            )

            self.assertEqual(ppd_calls, [])
            self.assertEqual(report.ppd, PpdDiagnostics())


if __name__ == "__main__":
    unittest.main()
