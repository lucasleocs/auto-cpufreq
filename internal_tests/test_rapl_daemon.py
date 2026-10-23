"""Safety and policy tests for daemon-only optional RAPL writes."""
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import io
from contextlib import redirect_stderr
import unittest
from unittest.mock import patch

from auto_cpufreq.modules import rapl_daemon as daemon
from auto_cpufreq.modules import intel_rapl as rapl


def config(text=""):
    result = ConfigParser()
    result.read_string(text or "[charger]\n[battery]\n")
    return result


def selected(*, charger="12", battery="13", controller="intel-rapl",
             package="package-0"):
    return config(
        "[intel_power]\n"
        "enable_rapl_envelopes = true\n"
        f"rapl_control_type = {controller}\n"
        f"rapl_package = {package}\n"
        "[charger]\n"
        f"rapl_package_long_term_w = {charger}\n"
        "[battery]\n"
        f"rapl_package_long_term_w = {battery}\n"
    )


class Discovery:
    def __init__(self, root):
        self.powercap_root = root

    def powercap_zone_paths(self):
        for control in ("intel-rapl", "intel-rapl-mmio"):
            zone = self.powercap_root / control / (control + ":0")
            if zone.is_dir():
                yield zone, control


class RAPLDaemonTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.msr = self._make_zone("intel-rapl", 28_000_000)
        self.mmio = self._make_zone("intel-rapl-mmio", 15_000_000)
        store = rapl.RaplStateStore(self.root / "private" / "power-state.json")
        self.engine = rapl.IntelRaplController(
            discovery=Discovery(self.root), state_store=store
        )
        self.policy = daemon.RaplDaemonPolicy(
            controller=self.engine, ready=lambda: True,
        )

    def _make_zone(self, control, initial):
        p = self.root / control / (control + ":0")
        p.mkdir(parents=True, exist_ok=True)
        (p / "name").write_text("package-0\n")
        (p / "enabled").write_text("1\n")
        (p / "constraint_0_name").write_text("long_term\n")
        (p / "constraint_0_power_limit_uw").write_text(str(initial) + "\n")
        return p / "constraint_0_power_limit_uw"

    def tick(self, cfg, profile="charger"):
        with redirect_stderr(io.StringIO()):
            return self.policy.tick(cfg, profile)

    def test_default_is_noop_and_does_not_create_journal(self):
        self.assertEqual(self.tick(config()).state, "disabled")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(int(self.msr.read_text()), 28_000_000)
        self.assertFalse(self.engine.state_store.path.exists())

    def test_not_ready_fails_closed_without_writes(self):
        self.policy.ready = lambda: False
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "read-only")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertFalse(self.engine.state_store.path.exists())

    def test_direct_top_level_mmio_zone_uses_same_reader_and_writer_identity(self):
        # Some Powercap layouts expose package zones directly beneath the
        # class root rather than underneath a control-type directory.
        zone = self.mmio.parent
        direct = self.root / "intel-rapl-mmio:0"
        zone.rename(direct)
        (self.root / "intel-rapl-mmio").rmdir()
        direct_power = direct / "constraint_0_power_limit_uw"
        result = self.tick(selected(controller="intel-rapl-mmio"))
        self.assertEqual(result.state, "active")
        self.assertEqual(int(direct_power.read_text()), 12_000_000)
        self.assertEqual(int(self.msr.read_text()), 28_000_000)
        self.assertEqual(self.tick(config()).state, "restored")
        self.assertEqual(int(direct_power.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_duplicate_package_identity_cannot_write_either_zone(self):
        second = self.mmio.parent.parent / "intel-rapl-mmio:1"
        second.mkdir()
        (second / "name").write_text("package-0\n")
        (second / "constraint_0_name").write_text("long_term\n")
        other_limit = second / "constraint_0_power_limit_uw"
        other_limit.write_text("17000000\n")
        event = self.tick(selected(controller="intel-rapl-mmio"))
        self.assertEqual(event.state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(int(other_limit.read_text()), 17_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_duplicate_long_term_indices_cannot_write(self):
        zone = self.mmio.parent
        (zone / "constraint_7_name").write_text("long_term\n")
        another = zone / "constraint_7_power_limit_uw"
        another.write_text("15000000\n")
        event = self.tick(selected(controller="intel-rapl-mmio"))
        self.assertEqual(event.state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(int(another.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_symlinked_power_limit_attribute_must_not_be_written(self):
        external = self.root / "unrelated-control"
        external.write_text("15000000\n")
        self.mmio.unlink()
        self.mmio.symlink_to(external)
        event = self.tick(selected(controller="intel-rapl-mmio"))
        self.assertEqual(event.state, "blocked")
        self.assertEqual(int(external.read_text()), 15_000_000)
        self.assertFalse(self.engine.state_store.path.exists())

    def test_top_level_alias_and_nested_package_are_not_double_written(self):
        zone = self.mmio.parent
        (self.root / "intel-rapl-mmio:0").symlink_to(
            zone, target_is_directory=True,
        )
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state,
                         "active")
        self.assertEqual(len(self.engine.state_store.load()), 1)
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)
        self.assertEqual(self.tick(config()).state, "restored")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_msr_and_mmio_must_be_selected_explicitly(self):
        c = config(
            "[intel_power]\nenable_rapl_envelopes = true\n"
            "[charger]\nrapl_package_long_term_w = 12\n[battery]\n"
        )
        self.assertEqual(self.tick(c).state, "ambiguous_control")
        self.assertEqual(int(self.msr.read_text()), 28_000_000)
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_selected_mmio_only_changes_mmio_and_restores_on_opt_out(self):
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "active")
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)
        self.assertEqual(int(self.msr.read_text()), 28_000_000)
        self.assertEqual(self.tick(config()).state, "restored")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_switch_ac_battery_reuses_original_baseline(self):
        settings = selected(controller="intel-rapl-mmio", charger="12", battery="13")
        self.assertEqual(self.tick(settings, "charger").state, "active")
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)
        self.assertEqual(self.tick(settings, "battery").state, "active")
        self.assertEqual(int(self.mmio.read_text()), 13_000_000)
        record = self.engine.state_store.load()[0]
        self.assertEqual(record.original_power_limit_uw, 15_000_000)
        self.assertEqual(self.tick(config()).state, "restored")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_missing_battery_target_restores_charger_envelope(self):
        settings = selected(controller="intel-rapl-mmio")
        settings.remove_option("battery", "rapl_package_long_term_w")
        self.assertEqual(self.tick(settings, "charger").state, "active")
        self.assertEqual(self.tick(settings, "battery").state, "restored")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_remove_last_owned_pl1_when_only_unowned_pl2_remains(self):
        # PL2 happens to equal the external baseline and has never been
        # modified; withdrawing PL1 should retire the last owned entry.
        zone = self.mmio.parent
        (zone / "constraint_1_name").write_text("short_term\n")
        (zone / "constraint_1_power_limit_uw").write_text("41000000\n")
        settings = selected(controller="intel-rapl-mmio")
        settings.set("charger", "rapl_package_short_term_w", "41")
        self.assertEqual(self.tick(settings).state, "active")
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)
        settings.remove_option("charger", "rapl_package_long_term_w")
        self.assertEqual(self.tick(settings).state, "no-change")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())
        self.assertEqual(self.tick(config()).state, "disabled")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_config_hot_reload_target_change_does_not_reset_baseline(self):
        self.assertEqual(self.tick(selected(charger="14", controller="intel-rapl-mmio")).state, "active")
        self.assertEqual(self.tick(selected(charger="11", controller="intel-rapl-mmio")).state, "active")
        self.assertEqual(int(self.mmio.read_text()), 11_000_000)
        self.assertEqual(self.engine.state_store.load()[0].original_power_limit_uw,
                         15_000_000)

    def test_switch_scope_restores_original_before_second_acquisition(self):
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "active")
        self.assertEqual(self.tick(selected(controller="intel-rapl")).state, "active")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(int(self.msr.read_text()), 12_000_000)
        self.assertEqual(self.engine.state_store.load()[0].identity.control_type,
                         "intel-rapl")

    def test_invalid_new_configuration_restores_previous_envelope(self):
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "active")
        bad = selected(controller="intel-rapl-mmio")
        bad.set("charger", "rapl_package_long_term_w", "not-a-number")
        self.assertEqual(self.tick(bad).state, "restored")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_disappeared_owned_journal_disables_new_writes(self):
        settings = selected(controller="intel-rapl-mmio")
        self.assertEqual(self.tick(settings).state, "active")
        self.engine.state_store.path.unlink()
        self.assertEqual(self.tick(config()).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)
        self.assertEqual(self.tick(settings).state, "blocked")

    def test_removed_systemd_cleanup_hook_restores_owned_limit(self):
        settings = selected(controller="intel-rapl-mmio")
        self.assertEqual(self.tick(settings).state, "active")
        self.policy.ready = lambda: False
        self.assertEqual(self.tick(settings).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())
        self.policy.ready = lambda: True
        self.assertEqual(self.tick(settings).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_external_drift_latches_off_without_reacquisition(self):
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "active")
        self.mmio.write_text("14000000\n")
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "blocked")
        self.mmio.write_text("15000000\n")
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_preexisting_journal_blocks_new_writes(self):
        preflight = self.engine.apply(
            rapl.RaplEnvelopeTargets(long_term_uw=14_000_000),
            control_type="intel-rapl-mmio",
        )
        self.assertEqual(preflight[0].action, "acquired")
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 14_000_000)

    def test_missing_driver_then_appears_without_restart(self):
        self.mmio.parent.rename(self.root / "mmio-hidden")
        self.msr.parent.rename(self.root / "msr-hidden")
        settings = selected(controller="intel-rapl-mmio")
        self.assertEqual(self.tick(settings).state, "no_package_constraints")
        (self.root / "mmio-hidden").rename(self.mmio.parent)
        (self.root / "msr-hidden").rename(self.msr.parent)
        self.assertEqual(self.tick(settings).state, "active")
        self.assertEqual(int(self.mmio.read_text()), 12_000_000)

    def test_bios_ignores_power_limit_without_write_error_only_once(self):
        settings = selected(controller="intel-rapl-mmio")
        attempts = []

        def ignored(path, value):
            attempts.append((path, value))
            # Locked BIOS constraint: the kernel reports no write error
            # but readback is unchanged at the external 15 W baseline.

        with patch.object(rapl, "_write_int", side_effect=ignored):
            self.assertEqual(self.tick(settings).state, "blocked")
            self.assertEqual(self.tick(settings).state, "blocked")

        self.assertEqual(len(attempts), 1)
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_bios_rejects_write_error_only_once(self):
        settings = selected(controller="intel-rapl-mmio")
        attempts = []
        def rejected(path, value):
            attempts.append(value)
            raise OSError("RAPL constraint locked by firmware")

        with patch.object(rapl, "_write_int", side_effect=rejected):
            self.assertEqual(self.tick(settings).state, "blocked")
            self.assertEqual(self.tick(settings).state, "blocked")
        self.assertEqual(attempts, [12_000_000])
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_quantized_readback_does_not_loop_or_leave_modified_limit(self):
        settings = selected(controller="intel-rapl-mmio")
        actual_write = rapl._write_int
        writes = []

        def quantized(path, value):
            writes.append(value)
            if path == self.mmio and value == 12_000_000:
                self.mmio.write_text("12500000\n")
            else:
                actual_write(path, value)

        with patch.object(rapl, "_write_int", side_effect=quantized):
            self.assertEqual(self.tick(settings).state, "blocked")
            self.assertEqual(self.tick(settings).state, "blocked")

        self.assertEqual(writes, [12_000_000, 15_000_000])
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_partial_pl2_failure_restores_already_applied_pl1(self):
        zone = self.mmio.parent
        (zone / "constraint_1_name").write_text("short_term\n")
        second = zone / "constraint_1_power_limit_uw"
        second.write_text("41000000\n")
        settings = selected(controller="intel-rapl-mmio")
        settings.set("charger", "rapl_package_short_term_w", "35")
        real_write = rapl._write_int

        def fail_second(path, value):
            if path == second and value == 35_000_000:
                raise OSError("injected PL2 write failure")
            real_write(path, value)

        with patch.object(rapl, "_write_int", side_effect=fail_second):
            self.assertEqual(self.tick(settings).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(int(second.read_text()), 41_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_above_original_reload_restores_prior_owned_limit(self):
        settings = selected(controller="intel-rapl-mmio")
        self.assertEqual(self.tick(settings).state, "active")
        raised = selected(charger="18", controller="intel-rapl-mmio")
        self.assertEqual(self.tick(raised).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_unexpected_policy_exception_attempts_recovery(self):
        settings = selected(controller="intel-rapl-mmio")
        self.assertEqual(self.tick(settings).state, "active")
        with redirect_stderr(io.StringIO()):
            self.policy.handle_unexpected_failure(RuntimeError("injected"))
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())
        self.assertEqual(self.tick(settings).state, "blocked")

    def test_exception_during_initial_acquisition_recovers_pending_journal(self):
        settings = selected(controller="intel-rapl-mmio")
        original = self.engine._save_ownership
        calls = [0]

        def fail_after_first_hardware_write(state):
            calls[0] += 1
            if calls[0] == 2:
                raise rapl.RaplStateError("injected journal finalization failure")
            return original(state)

        with patch.object(self.engine, "_save_ownership",
                          side_effect=fail_after_first_hardware_write):
            self.assertEqual(self.tick(settings).state, "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_max_power_attribute_rejects_out_of_range_target(self):
        (self.mmio.parent / "constraint_0_max_power_uw").write_text("10000000\n")
        self.assertEqual(
            self.tick(selected(charger="12", controller="intel-rapl-mmio")).state,
            "blocked",
        )
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)
        self.assertEqual(self.engine.state_store.load(), ())

    def test_no_writes_to_explicitly_disabled_zone(self):
        (self.mmio.parent / "enabled").write_text("0\n")
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio")).state,
                         "targets_unavailable")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)

    def test_invalid_profile_does_not_fallback_to_charger(self):
        self.assertEqual(self.tick(selected(controller="intel-rapl-mmio"), "foo").state,
                         "blocked")
        self.assertEqual(int(self.mmio.read_text()), 15_000_000)


class LifecycleGuardTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.unit = root / "auto-cpufreq.service"
        self.packaged = root / "package-unit"
        self.helper = root / "auto-cpufreq-restore-rapl"
        self.helper.write_text("#!/bin/sh\nexit 0\n")
        self.helper.chmod(0o755)
        correct = "ExecStopPost=-" + str(self.helper)
        self.unit.write_text("[Service]\n" + correct + "\n")
        self.packaged.write_text(self.unit.read_text())
        self.paths = [
            patch.object(daemon, "INSTALLED_UNIT", self.unit),
            patch.object(daemon, "PACKAGE_UNIT", self.packaged),
            patch.object(daemon, "HELPER", self.helper),
            patch.object(daemon, "EXPECTED_STOP", correct),
        ]
        for obj in self.paths:
            obj.start()
            self.addCleanup(obj.stop)
        self.systemd_output = (
            "MainPID=1234\nActiveState=active\nDropInPaths=\n"
            "NeedDaemonReload=no\n"
            f"FragmentPath={self.unit}\n"
            "ExecStopPost={ path=" + str(self.helper)
            + " ; argv[]=" + str(self.helper)
            + " ; ignore_errors=yes ; start_time=[n/a] }\n"
        )

    def check_guard(self, output=None, *, uid=0):
        if output is None:
            output = self.systemd_output
        fake_os = SimpleNamespace(
            geteuid=lambda: uid, getpid=lambda: 1234,
            access=lambda path, mode: True, X_OK=1,
        )
        with patch.object(daemon, "os", fake_os), \
             patch.object(daemon.subprocess, "run",
                          return_value=SimpleNamespace(returncode=0,
                                                       stdout=output)):
            return daemon._managed_systemd_lifecycle()

    def test_valid_root_managed_unit(self):
        self.assertTrue(self.check_guard())

    def test_loaded_manager_pending_reload_cannot_write(self):
        self.assertFalse(self.check_guard(
            self.systemd_output.replace("NeedDaemonReload=no",
                                        "NeedDaemonReload=yes")
        ))

    def test_loaded_manager_without_cleanup_hook_cannot_write(self):
        self.assertFalse(self.check_guard(
            self.systemd_output.replace(
                "ExecStopPost={ path=", "ExecStopPost={ removed_path="
            )
        ))

    def test_loaded_manager_replaced_cleanup_cannot_write(self):
        self.assertFalse(self.check_guard(
            self.systemd_output.replace(
                f"argv[]={self.helper}", "argv[]=/usr/bin/true"
            )
        ))

    def test_loaded_manager_other_fragment_cannot_write(self):
        self.assertFalse(self.check_guard(
            self.systemd_output.replace(
                f"FragmentPath={self.unit}",
                "FragmentPath=/etc/systemd/system/not-our-unit.service"
            )
        ))

    def test_manual_nonroot_daemon_cannot_write(self):
        self.assertFalse(self.check_guard(uid=1000))

    def test_other_process_pid_does_not_borrow_service_authorization(self):
        self.assertFalse(self.check_guard("MainPID=777\nActiveState=active\nDropInPaths=\n"))

    def test_service_without_exact_cleanup_unit_fails_closed(self):
        self.unit.write_text("ExecStart=/usr/bin/false\n")
        self.assertFalse(self.check_guard())

    def test_dropin_configuration_fails_closed(self):
        self.assertFalse(self.check_guard(
            "MainPID=1234\nActiveState=active\n"
            "DropInPaths=/etc/systemd/system/auto-cpufreq.service.d/a.conf\n"
        ))

    def test_inactive_unit_fails_closed(self):
        self.assertFalse(self.check_guard(
            "MainPID=1234\nActiveState=inactive\nDropInPaths=\n"
        ))


if __name__ == "__main__":
    unittest.main()
