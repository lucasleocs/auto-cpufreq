"""Expected safety guarantees for the legacy Intel RAPL controller.

These tests intentionally identify unsafe legacy behavior before refactoring.
They are laboratory checks and are not part of the contribution branch.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auto_cpufreq.modules.intel_rapl as rapl


class FakeDiscovery:
    def __init__(self, root: Path, zone: Path):
        self.powercap_root = root
        self.zone = zone

    def powercap_zone_paths(self):
        return ((self.zone, "intel-rapl"),)


class RaplSafetyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zone = self.root / "intel-rapl" / "intel-rapl:0"
        self.zone.mkdir(parents=True)
        (self.zone / "name").write_text("package-0\n")
        (self.zone / "constraint_0_name").write_text("long_term\n")
        self.power_path = self.zone / "constraint_0_power_limit_uw"
        self.power_path.write_text("15000000\n")
        self.store = rapl.RaplStateStore(self.root / "state" / "power-state.json")
        self.controller = rapl.IntelRaplController(
            discovery=FakeDiscovery(self.root, self.zone),
            state_store=self.store,
        )
        self.target = rapl.RaplEnvelopeTargets(long_term_uw=14_500_000)

    def test_disabled_zone_must_not_be_modified(self):
        (self.zone / "enabled").write_text("0\n")
        self.controller.apply(self.target)
        self.assertEqual(
            self.power_path.read_text().strip(), "15000000",
            "An explicitly disabled Powercap zone must not be enabled or written",
        )

    def test_broken_enabled_symlink_fails_closed(self):
        (self.zone / "enabled").symlink_to(self.root / "missing-enabled")
        result = self.controller.apply(self.target)
        self.assertEqual(result[0].action, "skipped-zone-disabled")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_malformed_package_zone_is_not_writable(self):
        (self.zone / "name").write_text("package-not-an-index\n")
        results = self.controller.apply(self.target)
        self.assertEqual(results, ())
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_external_drift_suspends_reacquisition(self):
        acquired = self.controller.apply(self.target)
        self.assertEqual(acquired[0].action, "acquired")
        self.power_path.write_text("14250000\n")  # external firmware actor
        drift = self.controller.apply(self.target)
        self.assertEqual(drift[0].action, "drift")
        self.power_path.write_text("15000000\n")  # external actor restores cap
        self.controller.apply(self.target)
        self.assertEqual(
            self.power_path.read_text().strip(), "15000000",
            "After drift, do not fight the external controller on the next cycle",
        )

    def test_failed_readback_keeps_recovery_intent(self):
        actual_read = rapl._read_int
        reads = [0]

        def fail_after_write(path):
            if path == self.power_path:
                reads[0] += 1
                if reads[0] >= 2:
                    return None
            return actual_read(path)

        with patch.object(rapl, "_read_int", side_effect=fail_after_write):
            result = self.controller.apply(self.target)

        self.assertEqual(
            result[0].action, "readback-failed",
            "The scenario must enter post-write readback failure",
        )
        self.assertEqual(self.power_path.read_text().strip(), "14500000")
        self.assertTrue(
            self.store.load(),
            "An unresolved write must leave durable recovery metadata",
        )


    def _pending_record(self, last=15_000_000, target=14_500_000):
        ref = self.controller.discover_package_constraints()[0]
        return rapl.RaplOwnershipRecord(
            identity=self.controller._identity(ref),
            original_power_limit_uw=15_000_000,
            last_written_power_limit_uw=last,
            pending_target_uw=target,
        )

    def test_interrupted_before_write_can_retire_pending_intent(self):
        self.store.save((self._pending_record(),))
        result = self.controller.restore_owned()
        self.assertEqual(result[0].action, "already-restored")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(self.store.load(), ())

    def test_interrupted_after_write_restores_pending_intent(self):
        self.store.save((self._pending_record(),))
        self.power_path.write_text("14500000\n")
        result = self.controller.restore_owned()
        self.assertEqual(result[0].action, "restored")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(self.store.load(), ())

    def test_ambiguous_pending_readback_is_not_overwritten(self):
        self.store.save((self._pending_record(),))
        self.power_path.write_text("14250000\n")
        result = self.controller.restore_owned()
        self.assertEqual(result[0].action, "restore-failed")
        self.assertEqual(self.power_path.read_text().strip(), "14250000")
        self.assertEqual(len(self.store.load()), 1)

    def test_owned_update_readback_failure_preserves_original_baseline(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        actual_read = rapl._read_int
        reads = [0]

        def fail_post_write(path):
            if path == self.power_path:
                reads[0] += 1
                if reads[0] >= 2:
                    return None
            return actual_read(path)

        with patch.object(rapl, "_read_int", side_effect=fail_post_write):
            result = self.controller.apply(
                rapl.RaplEnvelopeTargets(long_term_uw=14_000_000)
            )
        self.assertEqual(result[0].action, "readback-failed")
        journal = self.store.load()[0]
        self.assertEqual(journal.original_power_limit_uw, 15_000_000)
        self.assertEqual(journal.last_written_power_limit_uw, 14_500_000)
        self.assertEqual(journal.pending_target_uw, 14_000_000)
        restored = self.controller.restore_owned()
        self.assertEqual(restored[0].action, "restored")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_prewrite_persistence_failure_prevents_hardware_write(self):
        with patch.object(
            self.store, "save", side_effect=rapl.RaplStateError("disk full")
        ):
            with self.assertRaises(rapl.RaplStateError):
                self.controller.apply(self.target)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_malformed_ownership_journal_stops_before_hardware_write(self):
        self.store.path.parent.mkdir(parents=True, mode=0o700)
        self.store.path.write_text("{bad json")
        self.store.path.chmod(0o600)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")


    def test_concurrent_apply_refuses_when_journal_locked(self):
        with self.store.locked():
            with self.assertRaises(rapl.RaplStateError):
                self.controller.apply(self.target)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(self.store.load(), ())

    def test_concurrent_restore_refuses_when_journal_locked(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        with self.store.locked():
            with self.assertRaises(rapl.RaplStateError):
                self.controller.restore_owned()
        self.assertEqual(self.power_path.read_text().strip(), "14500000")
        self.assertEqual(len(self.store.load()), 1)
        self.assertEqual(self.controller.restore_owned()[0].action, "restored")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_lock_file_is_private_and_symlink_safe(self):
        self.assertFalse((self.root / "state").exists())
        with self.store.locked():
            lock_file = Path(str(self.store.path) + ".lock")
            self.assertEqual(lock_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / "state").stat().st_mode & 0o777, 0o700)

        # Replacement by a symlink must not redirect a root-owned journal
        # operation to a user-chosen destination.
        lock_file.unlink()
        destination = self.root / "unrelated"
        destination.write_text("do-not-touch")
        lock_file.symlink_to(destination)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(destination.read_text(), "do-not-touch")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")


    def _add_package(self, control, zone_index, package, original_uw):
        zone = self.root / control / f"{control}:{zone_index}"
        zone.mkdir(parents=True, exist_ok=True)
        (zone / "name").write_text(package + "\n")
        (zone / "constraint_0_name").write_text("long_term\n")
        power = zone / "constraint_0_power_limit_uw"
        power.write_text(str(original_uw) + "\n")
        return zone, power

    def _set_discovery(self, zone_pairs):
        class MultiDiscovery:
            def __init__(self, root, pairs):
                self.powercap_root = root
                self.pairs = pairs

            def powercap_zone_paths(self):
                return self.pairs

        self.controller.discovery = MultiDiscovery(self.root, tuple(zone_pairs))

    def test_multiple_controllers_require_explicit_selection(self):
        mmio, mmio_power = self._add_package(
            "intel-rapl-mmio", 0, "package-0", 28_000_000
        )
        self._set_discovery((
            (self.zone, "intel-rapl"), (mmio, "intel-rapl-mmio")
        ))
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(mmio_power.read_text().strip(), "28000000")
        self.assertEqual(self.store.load(), ())

    def test_selected_mmio_does_not_modify_msr(self):
        mmio, mmio_power = self._add_package(
            "intel-rapl-mmio", 0, "package-0", 28_000_000
        )
        self._set_discovery((
            (self.zone, "intel-rapl"), (mmio, "intel-rapl-mmio")
        ))
        results = self.controller.apply(
            self.target, control_type="intel-rapl-mmio"
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action, "acquired")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(mmio_power.read_text().strip(), "14500000")
        self.assertEqual(self.store.load()[0].identity.control_type,
                         "intel-rapl-mmio")
        self.assertEqual(self.controller.restore_owned()[0].action, "restored")
        self.assertEqual(mmio_power.read_text().strip(), "28000000")

    def test_multiple_packages_require_explicit_selection(self):
        second, second_power = self._add_package(
            "intel-rapl", 1, "package-1", 30_000_000
        )
        self._set_discovery(((self.zone, "intel-rapl"), (second, "intel-rapl")))
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(second_power.read_text().strip(), "30000000")

        results = self.controller.apply(self.target, package="package-1")
        self.assertEqual(results[0].action, "acquired")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")
        self.assertEqual(second_power.read_text().strip(), "14500000")
        self.assertEqual(self.controller.restore_owned()[0].action, "restored")

    def test_switching_controller_with_owned_state_requires_restoration(self):
        mmio, mmio_power = self._add_package(
            "intel-rapl-mmio", 0, "package-0", 28_000_000
        )
        self._set_discovery((
            (self.zone, "intel-rapl"), (mmio, "intel-rapl-mmio")
        ))
        original = self.controller.apply(
            self.target, control_type="intel-rapl"
        )
        self.assertEqual(original[0].action, "acquired")
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target, control_type="intel-rapl-mmio")
        self.assertEqual(mmio_power.read_text().strip(), "28000000")
        self.assertEqual(self.controller.restore_owned()[0].action, "restored")
        second = self.controller.apply(
            self.target, control_type="intel-rapl-mmio"
        )
        self.assertEqual(second[0].action, "acquired")
        self.assertEqual(self.controller.restore_owned()[0].action, "restored")

    def test_stale_scope_selector_causes_no_write(self):
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target, control_type="intel-rapl-mmio")
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target, package="package-9")
        self.assertEqual(self.power_path.read_text().strip(), "15000000")


    def test_restore_failure_with_unexpected_external_value_keeps_pending(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        calls = []

        def fail_during_restore(path, value):
            calls.append(value)
            self.power_path.write_text("14250000\n")
            raise OSError("simulated partial/foreign write")

        with patch.object(rapl, "_write_int", side_effect=fail_during_restore):
            result = self.controller.restore_owned()

        self.assertEqual(result[0].action, "restore-failed")
        self.assertEqual(calls, [15_000_000])
        self.assertEqual(self.power_path.read_text().strip(), "14250000")
        ownership = self.store.load()
        self.assertEqual(len(ownership), 1, "Uncertain recovery must retain journal")
        self.assertEqual(ownership[0].original_power_limit_uw, 15_000_000)
        self.assertIsNotNone(ownership[0].pending_target_uw)
        retry = self.controller.restore_owned()
        self.assertEqual(retry[0].action, "restore-failed")
        self.assertEqual(self.power_path.read_text().strip(), "14250000")

    def test_unexpected_high_readback_does_not_claim_foreign_value(self):
        initial_write = rapl._write_int
        calls = []

        def synthetic_write(path, value):
            calls.append(value)
            if value == 14_500_000:
                # The sysfs write is followed by an unexpected higher
                # value that might come from firmware or another writer.
                self.power_path.write_text("16000000\n")
            else:
                raise AssertionError("No implicit second sysfs write is allowed")

        with patch.object(rapl, "_write_int", side_effect=synthetic_write):
            result = self.controller.apply(self.target)
        self.assertEqual(result[0].action, "unsafe-readback-unresolved")
        self.assertEqual(calls, [14_500_000])
        self.assertEqual(self.power_path.read_text().strip(), "16000000")
        record = self.store.load()[0]
        self.assertEqual(record.original_power_limit_uw, 15_000_000)
        self.assertEqual(record.last_written_power_limit_uw, 15_000_000)
        self.assertEqual(record.pending_target_uw, 14_500_000)
        retry = self.controller.restore_owned()
        self.assertEqual(retry[0].action, "restore-failed")
        self.assertEqual(self.power_path.read_text().strip(), "16000000")

    def test_firmware_already_restored_baseline_needs_no_write(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        self.power_path.write_text("15000000\n")
        with patch.object(rapl, "_write_int", side_effect=AssertionError(
            "Do not rewrite a baseline already restored by firmware"
        )):
            results = self.controller.restore_owned()
        self.assertEqual(results[0].action, "already-restored")
        self.assertEqual(self.store.load(), ())


    def test_symlink_journal_is_not_trusted_for_recovery(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        saved = self.root / "externally-managed-state.json"
        self.store.path.rename(saved)
        self.store.path.symlink_to(saved)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.restore_owned()
        self.assertEqual(self.power_path.read_text().strip(), "14500000")

    def test_world_readable_journal_is_rejected(self):
        self.assertEqual(self.controller.apply(self.target)[0].action, "acquired")
        self.store.path.chmod(0o644)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.restore_owned()
        self.assertEqual(self.power_path.read_text().strip(), "14500000")

    def test_untrusted_parent_directory_is_rejected_without_chmod(self):
        parent = self.store.path.parent
        parent.mkdir(mode=0o700)
        parent.chmod(0o777)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o777)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")

    def test_world_readable_lock_file_is_rejected_without_chmod(self):
        parent = self.store.path.parent
        parent.mkdir(mode=0o700)
        lockfile = Path(str(self.store.path) + ".lock")
        lockfile.write_text("external-owner\n")
        lockfile.chmod(0o644)
        with self.assertRaises(rapl.RaplStateError):
            self.controller.apply(self.target)
        self.assertEqual(lockfile.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.power_path.read_text().strip(), "15000000")


if __name__ == "__main__":
    unittest.main()
