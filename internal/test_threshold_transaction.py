"""Start/end transactional safety tests: all writes remain in TemporaryDirectory.

The emulated kernel optionally rejects invalid intermediate pairs, rounds
individual thresholds, reports successful-but-no-op writes, or fails during
the update/rollback. No real sysfs, EC or D-Bus write is performed.
"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from auto_cpufreq.battery_scripts.shared import BatteryDevice
from test_generic_kernel_threshold import GenericBatteryFixture


class LegacyThresholdTransactionTests(GenericBatteryFixture):
    def setUp(self):
        super().setUp()
        self.start_file = self.bat / "charge_control_start_threshold"
        self.stop_file = self.bat / "charge_control_end_threshold"
        self.set_pair(80, 90)
        self.dev = BatteryDevice()
        self.history = []
        self.fail_on = set()
        self.no_op_on = set()
        self.round_to = {}
        self.perturb = {}
        self.before_second = None

    def set_pair(self, start, stop):
        self.start_file.write_text(str(start))
        self.stop_file.write_text(str(stop))

    def pair(self):
        return (int(self.start_file.read_text()), int(self.stop_file.read_text()))

    def kernel_write(self, path, value):
        key = "start" if path == str(self.start_file) else "stop"
        self.history.append((key, value))
        if self.before_second and len(self.history) == 2:
            self.before_second()
        if len(self.history) in self.fail_on:
            return False
        if len(self.history) in self.no_op_on:
            return True
        actual = self.round_to.get((key, int(value)), int(value))
        start, stop = self.pair()
        if key == "start":
            start = actual
        else:
            stop = actual
        if not 0 <= start < stop <= 100:
            return False
        if len(self.history) in self.perturb:
            start, stop = self.perturb[len(self.history)]
        self.set_pair(start, stop)
        return True

    def run_update(self, start, stop):
        with patch.object(self.dev, "_write_value_to_file",
                          side_effect=self.kernel_write):
            return self.dev.set_battery_thresholds("BAT1", start, stop)

    def test_decrease_start_then_stop_without_100(self):
        self.assertTrue(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50), ("stop", 70)])
        self.assertEqual(self.pair(), (50, 70))

    def test_increase_stop_then_start_without_100(self):
        self.set_pair(60, 80)
        self.assertTrue(self.run_update(90, 95))
        self.assertEqual(self.history, [("stop", 95), ("start", 90)])
        self.assertEqual(self.pair(), (90, 95))

    def test_change_stop_only_has_single_write(self):
        self.assertTrue(self.run_update(80, 95))
        self.assertEqual(self.history, [("stop", 95)])

    def test_change_start_only_has_single_write(self):
        self.assertTrue(self.run_update(60, 90))
        self.assertEqual(self.history, [("start", 60)])

    def test_same_pair_no_writes(self):
        self.assertTrue(self.run_update(80, 90))
        self.assertEqual(self.history, [])

    def test_invalid_targets_do_not_write(self):
        for pair in ((90, 90), (100, 101), (-1, 80), (50, 101),
                     ("50", 80), (None, 80)):
            with self.subTest(pair=pair):
                self.assertFalse(self.run_update(*pair))
                self.assertEqual(self.history, [])

    def test_unknown_current_pair_blocks_all_writes(self):
        self.start_file.write_text("not-an-integer")
        with patch.object(self.dev, "_write_value_to_file",
                          side_effect=AssertionError("unexpected write")):
            self.assertFalse(self.dev.set_battery_thresholds("BAT1", 50, 70))

    def test_invalid_existing_pair_blocks_all_writes(self):
        self.set_pair(90, 80)
        with patch.object(self.dev, "_write_value_to_file",
                          side_effect=AssertionError("unexpected write")):
            self.assertFalse(self.dev.set_battery_thresholds("BAT1", 50, 70))

    def test_initial_write_failure_no_change_no_restore(self):
        self.fail_on.add(1)
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])
        self.assertEqual(self.pair(), (80, 90))

    def test_second_write_failure_rolls_back_only_first(self):
        self.fail_on.add(2)
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [
            ("start", 50), ("stop", 70), ("start", 80)])
        self.assertEqual(self.pair(), (80, 90))

    def test_second_write_failure_stop_first_restores_stop(self):
        self.set_pair(60, 80)
        self.fail_on.add(2)
        self.assertFalse(self.run_update(90, 95))
        self.assertEqual(self.history, [
            ("stop", 95), ("start", 90), ("stop", 80)])
        self.assertEqual(self.pair(), (60, 80))

    def test_write_reports_success_but_first_attribute_doesnt_move(self):
        self.no_op_on.add(1)
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])
        self.assertEqual(self.pair(), (80, 90))

    def test_write_reports_success_but_second_attribute_doesnt_move(self):
        self.no_op_on.add(2)
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [
            ("start", 50), ("stop", 70), ("start", 80)])
        self.assertEqual(self.pair(), (80, 90))

    def test_failed_rollback_reports_partial(self):
        self.fail_on.update((2, 3))
        output = StringIO()
        with redirect_stdout(output):
            self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [
            ("start", 50), ("stop", 70), ("start", 80)])
        self.assertEqual(self.pair(), (50, 90))
        self.assertIn("PARTIAL", output.getvalue())

    def test_round_start_with_valid_remaining_stop(self):
        self.round_to[("start", 53)] = 55
        output = StringIO()
        with redirect_stdout(output):
            self.assertTrue(self.run_update(53, 70))
        self.assertEqual(self.pair(), (55, 70))
        self.assertIn("adjusted by the kernel", output.getvalue())

    def test_round_start_that_conflicts_with_target_stop_rolls_back(self):
        self.round_to[("start", 50)] = 75
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [
            ("start", 50), ("start", 80)])
        self.assertEqual(self.pair(), (80, 90))

    def test_round_stop_with_valid_final_pair(self):
        self.round_to[("stop", 70)] = 72
        self.assertTrue(self.run_update(50, 70))
        self.assertEqual(self.pair(), (50, 72))

    def test_round_start_no_progress_fails(self):
        self.round_to[("start", 50)] = 80
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])

    def test_unsafe_corrupted_readback_reports_partial_unknown(self):
        self.perturb[1] = (95, 90)
        output = StringIO()
        with redirect_stdout(output):
            self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])
        self.assertIn("unknown", output.getvalue())

    def test_other_field_changed_unexpectedly_aborts_without_rollback(self):
        self.perturb[1] = (50, 95)
        self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])
        self.assertEqual(self.pair(), (50, 95))

    def test_race_before_second_step_aborts_without_rollback(self):
        def external_change():
            self.stop_file.write_text("95")
        # Called by a mock write; to emulate concurrency before the next
        # write, patch the readback method to mutate right before step 2.
        self.dev.get_current_threshold_orig = self.dev.get_current_threshold
        count = [0]
        original = self.dev.get_current_threshold
        def read(bat):
            count[0] += 1
            if count[0] == 4:
                external_change()
            return original(bat)
        with patch.object(self.dev, "get_current_threshold", side_effect=read):
            self.assertFalse(self.run_update(50, 70))
        self.assertEqual(self.history, [("start", 50)])
        self.assertEqual(self.pair(), (50, 95))

    def test_no_100_intermediate_when_target_100_is_legitimate(self):
        self.set_pair(50, 70)
        self.assertTrue(self.run_update(50, 100))
        self.assertEqual(self.history, [("stop", 100)])
        self.assertEqual(self.pair(), (50, 100))


if __name__ == "__main__":
    import unittest
    unittest.main()
