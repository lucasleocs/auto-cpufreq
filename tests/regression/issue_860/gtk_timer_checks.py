"""GLib periodic scheduling contracts without privileged policy operations."""
import unittest
from unittest.mock import Mock

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib

from auto_cpufreq.gui.refresh_timer import GtkRefreshTimer


class FakeCoordinator:
    def __init__(self):
        self.in_progress = False
        self.age = None

    def request_age_seconds(self):
        return self.age


class GtkTimerTests(unittest.TestCase):
    def setUp(self):
        self.active = True
        self.coordinator = FakeCoordinator()
        self.diagnostics = Mock()
        self.requests = 0
        self.clock_now = (10.0, 10.0)
        self.scheduler = GtkRefreshTimer(
            coordinator=self.coordinator,
            request=self.request,
            active=lambda: self.active,
            diagnostics=self.diagnostics,
            component="GTK test",
            interval=1,
            clocks=lambda: self.clock_now,
        )
        self.addCleanup(self.scheduler.stop)

    def request(self):
        self.requests += 1

    def test_start_and_stop_are_idempotent_and_one_source(self):
        self.scheduler.start()
        source = self.scheduler.source_id
        self.assertIsNotNone(source)
        self.scheduler.start()
        self.assertEqual(self.scheduler.source_id, source)
        source_object = GLib.MainContext.default().find_source_by_id(source)
        self.assertIsNotNone(source_object)
        self.scheduler.stop()
        self.scheduler.stop()
        self.assertIsNone(self.scheduler.source_id)
        self.assertTrue(source_object.is_destroyed())

    def test_interval_switch_replaces_source_without_leaking(self):
        self.scheduler.start()
        old = self.scheduler.source_id
        old_source = GLib.MainContext.default().find_source_by_id(old)
        for value in (2, 5, 1, 5, 2):
            self.scheduler.set_interval(value)
            self.assertTrue(old_source.is_destroyed())
            new = self.scheduler.source_id
            self.assertIsNotNone(new)
            old_source = GLib.MainContext.default().find_source_by_id(new)
            self.assertIsNotNone(old_source)
            self.assertFalse(old_source.is_destroyed())
            old = new
        self.scheduler.set_interval(2)
        self.assertEqual(self.scheduler.source_id, old)

    def test_busy_ticks_do_not_queue_successor(self):
        self.coordinator.in_progress = True
        self.coordinator.age = 4
        for _ in range(40):
            self.assertTrue(self.scheduler._tick())
        self.assertEqual(self.requests, 0)
        self.coordinator.in_progress = False
        self.scheduler._tick()
        self.assertEqual(self.requests, 1)

    def test_stall_warns_once_per_episode_with_no_extra_collection(self):
        self.coordinator.in_progress = True
        self.coordinator.age = 25
        self.scheduler._tick()
        self.scheduler._tick()
        self.diagnostics.warning.assert_called_once()
        self.assertEqual(self.requests, 0)
        self.coordinator.in_progress = False
        self.scheduler._tick()
        self.coordinator.in_progress = True
        self.scheduler._tick()
        self.assertEqual(self.diagnostics.warning.call_count, 2)

    def test_boottime_detects_resume_without_catchup_burst(self):
        self.scheduler.start()
        self.clock_now = (11.0, 11.0)
        self.scheduler._tick()
        self.assertEqual(self.requests, 1)
        # One hour of suspend: boottime advances, monotonic barely does.
        self.clock_now = (12.0, 3612.0)
        self.scheduler._tick()
        self.assertEqual(self.requests, 2)
        self.assertEqual(self.diagnostics.info.call_count, 1)
        self.assertIn("system resume detected",
                      self.diagnostics.info.call_args.args[0])
        self.clock_now = (13.0, 3613.0)
        self.scheduler._tick()
        self.assertEqual(self.requests, 3)
        self.assertEqual(self.diagnostics.info.call_count, 1)

    def test_missing_boottime_is_supported(self):
        self.clock_now = (10.0, None)
        self.scheduler.start()
        self.clock_now = (20.0, None)
        self.scheduler._tick()
        self.assertEqual(self.requests, 1)
        self.diagnostics.info.assert_not_called()

    def test_failed_request_retains_timer_and_retries_next_tick(self):
        calls = []

        def temporarily_broken():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("collector unavailable")

        self.scheduler._request = temporarily_broken
        self.scheduler.start()
        self.assertTrue(self.scheduler._tick())
        self.assertIsNotNone(self.scheduler.source_id)
        self.assertTrue(self.scheduler._tick())
        self.assertEqual(len(calls), 2)
        self.diagnostics.warning.assert_called_once()

    def test_inactive_tick_removes_source_and_new_start_is_ignored(self):
        self.scheduler.start()
        source = self.scheduler.source_id
        self.active = False
        # Invoked directly rather than by GLib: emulate GLib's automatic
        # source removal after the callback returns False.
        self.assertFalse(self.scheduler._tick())
        self.assertIsNone(self.scheduler.source_id)
        GLib.source_remove(source)
        self.scheduler.start()
        self.assertIsNone(self.scheduler.source_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
