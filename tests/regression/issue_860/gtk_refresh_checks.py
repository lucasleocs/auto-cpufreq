import importlib.util
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib


class Label:
    def __init__(self):
        self.visible = False
        self.message = ""

    def set_text(self, message):
        self.message = message

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True


def load_app():
    config = types.ModuleType("auto_cpufreq.config.config")
    config.config = types.SimpleNamespace(
        has_config=lambda: False, path="/tmp/issue-860.conf"
    )

    core = types.ModuleType("auto_cpufreq.core")
    core.check_for_update = lambda: False
    core.daemon_is_running = lambda: False

    globals_module = types.ModuleType("auto_cpufreq.globals")
    globals_module.IS_INSTALLED_WITH_SNAP = False
    globals_module.GITHUB = "https://github.com/AdnanHodzic/auto-cpufreq"

    objects = types.ModuleType("auto_cpufreq.gui.objects")
    for name in (
        "BluetoothBootControl", "DaemonNotRunningView", "DropDownMenu",
        "MonitorModeView", "RadioButtonView", "CPUTurboOverride",
        "UpdateDialog",
    ):
        setattr(objects, name, object)

    info = types.ModuleType("auto_cpufreq.modules.system_info")
    info.battery_power_label = lambda battery: "Battery power"
    info.format_platform_profile_choices = lambda value: ""
    info.system_info = types.SimpleNamespace(
        generate_system_report=lambda: object()
    )

    power = types.ModuleType("auto_cpufreq.power_helper")
    power.bluetoothctl_exists = lambda: False

    replacements = {
        config.__name__: config,
        core.__name__: core,
        globals_module.__name__: globals_module,
        objects.__name__: objects,
        info.__name__: info,
        power.__name__: power,
    }
    spec = importlib.util.spec_from_file_location(
        "_issue860_gtk_app", ROOT / "auto_cpufreq/gui/app.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(module)
    return module


APP = load_app()


class WindowState:
    refresh_in_thread = APP.ToolWindow.refresh_in_thread
    _collect_report = APP.ToolWindow._collect_report
    refresh_in_progress = APP.ToolWindow.refresh_in_progress
    _apply_refresh = APP.ToolWindow._apply_refresh
    _finish_refresh_error = APP.ToolWindow._finish_refresh_error
    request_refresh = APP.ToolWindow.request_refresh
    _run_pending_refresh = APP.ToolWindow._run_pending_refresh
    _schedule_refresh = APP.ToolWindow._schedule_refresh
    _cleanup_refresh = APP.ToolWindow._cleanup_refresh
    refresh_id = APP.ToolWindow.refresh_id
    set_refresh_interval = APP.ToolWindow.set_refresh_interval
    begin_power_state_apply = APP.ToolWindow.begin_power_state_apply
    finish_power_state_apply = APP.ToolWindow.finish_power_state_apply
    _release_power_state_apply = APP.ToolWindow._release_power_state_apply

    def __init__(self):
        self.destroyed = False
        self._refresh_bridge = APP.GtkRefreshBridge(
            self._collect_report,
            component="GTK dashboard",
            diagnostics=APP.diagnostics,
            active=lambda: not self.destroyed,
            interval=lambda: self.refresh_interval,
            on_report=self._apply_refresh,
            on_error=self._finish_refresh_error,
            before_ack=self._run_pending_refresh,
            thread_name="gtk-test-refresh",
        )
        self._refresh_coordinator = self._refresh_bridge.coordinator
        self._refresh_timer = APP.GtkRefreshTimer(
            coordinator=self._refresh_coordinator,
            request=self._refresh_coordinator.request_if_idle,
            active=lambda: not self.destroyed,
            diagnostics=APP.diagnostics,
            component="GTK dashboard",
            interval=1,
        )
        self.refresh_pending = False
        self.refresh_interval = 1
        self.pending_power_updates = set()
        self.pending_power_release = set()
        self.power_apply_sources = {}
        self.power_values = {"governor": Label(), "turbo": Label()}
        self.applied = []
        self.report_view = types.SimpleNamespace(
            apply_report=lambda report, pending: self.applied.append(report)
        )
        self.config_label = Label()
        self.refresh_error_label = Label()


def pump_until(predicate, timeout=4):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for _ in range(20):
            if not context.pending():
                break
            context.iteration(False)
        if predicate():
            return True
        time.sleep(0.01)
    return False


class GtkRefreshTests(unittest.TestCase):
    def setUp(self):
        self.window = WindowState()
        self.generator = APP.system_info.generate_system_report

    def tearDown(self):
        self.window._cleanup_refresh()
        APP.system_info.generate_system_report = self.generator
        pump_until(lambda: True, timeout=0.1)

    def test_periodic_tick_skips_busy_collection_without_pending_work(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def collect():
            calls.append(1)
            started.set()
            release.wait(timeout=3)
            return object()

        APP.system_info.generate_system_report = collect
        try:
            self.window.refresh_in_thread()
            self.assertTrue(started.wait(timeout=2))
            self.window._schedule_refresh()
            for _ in range(30):
                self.assertTrue(self.window._refresh_timer._tick())
            self.assertFalse(self.window.refresh_pending)
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(pump_until(
                lambda: len(self.window.applied) == 1
                and not self.window._refresh_coordinator.in_progress,
                timeout=3,
            ))
            self.assertEqual(len(calls), 1)
        finally:
            release.set()

    def test_repeated_interval_changes_do_not_duplicate_timers(self):
        self.window._schedule_refresh()
        source = self.window.refresh_id
        for interval in (2, 5, 1, 5, 2):
            self.window.set_refresh_interval(interval)
            self.assertIsNone(
                GLib.MainContext.default().find_source_by_id(source)
            )
            source = self.window.refresh_id
            self.assertIsNotNone(source)
            self.assertIsNotNone(
                GLib.MainContext.default().find_source_by_id(source)
            )

    def test_dashboard_busy_state_is_owned_by_coordinator(self):
        started, release = threading.Event(), threading.Event()

        def collect():
            started.set()
            release.wait(timeout=3)
            return object()

        APP.system_info.generate_system_report = collect
        try:
            self.assertFalse(self.window.refresh_in_progress)
            self.window.refresh_in_thread()
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(self.window.refresh_in_progress)
            self.assertTrue(self.window._refresh_coordinator.in_progress)
            release.set()
            self.assertTrue(pump_until(
                lambda: not self.window.refresh_in_progress
                and len(self.window.applied) == 1
            ))
            self.assertFalse(self.window._refresh_coordinator.in_progress)
        finally:
            release.set()

    def test_pending_requests_are_coalesced(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        lock = threading.Lock()

        def collect():
            with lock:
                calls.append(time.monotonic())
            started.set()
            release.wait(timeout=3)
            return object()

        APP.system_info.generate_system_report = collect
        try:
            self.window.refresh_in_thread()
            self.assertTrue(started.wait(timeout=2))
            for _ in range(30):
                self.window.request_refresh()
            self.assertEqual(len(calls), 1)
            release.set()
            self.assertTrue(
                pump_until(
                    lambda: len(self.window.applied) == 2
                    and not self.window.refresh_in_progress
                )
            )
            self.assertEqual(len(calls), 2)
            self.assertFalse(self.window.refresh_pending)
        finally:
            release.set()

    def test_pending_scheduler_failure_still_acknowledges_report(self):
        self.window._refresh_bridge._before_ack = lambda: (
            _ for _ in ()
        ).throw(RuntimeError("synthetic scheduling error"))
        self.window.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(self.window.applied) == 1))
        self.assertTrue(pump_until(
            lambda: not self.window._refresh_coordinator.in_progress
        ))
        self.assertFalse(self.window.refresh_in_progress)
        # The coordinator remains usable after the faulty callback.
        self.window._refresh_bridge._before_ack = lambda: None
        self.window.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(self.window.applied) == 2))

    def test_diagnostic_failure_must_not_strand_dashboard_refresh(self):
        # A diagnostics backend can fail after a worker has completed.
        # GUI logging must not hold the delivery slot permanently.
        with patch.object(APP.diagnostics, "record_duration",
                          side_effect=OSError("simulated logging failure")):
            self.window.refresh_in_thread()
            self.assertTrue(pump_until(
                lambda: not self.window._refresh_coordinator.in_progress,
                timeout=2,
            ), "Diagnostics error stranded a completed GTK refresh")
        self.window.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(self.window.applied) >= 1))

    def test_destroy_during_collection(self):
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def collect():
            started.set()
            release.wait(timeout=3)
            completed.set()
            return object()

        APP.system_info.generate_system_report = collect
        try:
            self.window.refresh_in_thread()
            self.assertTrue(started.wait(timeout=2))
            self.window._schedule_refresh()
            source = self.window.refresh_id
            self.window._cleanup_refresh()
            release.set()
            self.assertTrue(completed.wait(timeout=2))
            self.assertTrue(self.window.destroyed)
            self.assertIsNone(self.window.refresh_id)
            self.assertIsNone(GLib.MainContext.default().find_source_by_id(source))
            self.assertEqual(self.window.applied, [])
        finally:
            release.set()

    def test_dashboard_shutdown_survives_broken_diagnostics(self):
        self.window._schedule_refresh()
        with patch.object(APP.diagnostics, "set_error_callback",
                          side_effect=OSError("diagnostic callback broken")), \
             patch.object(APP.diagnostics, "info",
                          side_effect=OSError("diagnostic logger broken")):
            self.window._cleanup_refresh()
        self.assertTrue(self.window.destroyed)
        self.assertTrue(self.window._refresh_coordinator.closed)
        self.assertIsNone(self.window.refresh_id)
        # Closing again is intentionally harmless.
        self.window._cleanup_refresh()

    def test_power_apply_holds_old_report_until_new_snapshot(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def collect():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                started.set()
                release.wait(timeout=3)
            return "snapshot-%d" % len(calls)

        def show_report(report, pending):
            self.window.applied.append(report)
            if "governor" not in pending:
                self.window.power_values["governor"].set_text(report)

        APP.system_info.generate_system_report = collect
        self.window.report_view.apply_report = show_report
        try:
            self.window.refresh_in_thread()
            self.assertTrue(started.wait(timeout=2))
            self.assertTrue(self.window.begin_power_state_apply("governor"))
            self.assertEqual(
                self.window.power_values["governor"].message, "Applying…"
            )
            self.assertTrue(self.window.finish_power_state_apply(
                "governor", success=True
            ))
            source_id = self.window.power_apply_sources["governor"]
            # Trigger the delayed completion deterministically.
            GLib.source_remove(source_id)
            self.window._release_power_state_apply("governor")
            self.assertTrue(self.window.refresh_pending)

            release.set()
            self.assertTrue(pump_until(
                lambda: len(self.window.applied) == 2
                and not self.window._refresh_coordinator.in_progress,
                timeout=3,
            ))
            self.assertEqual(self.window.applied, [
                "snapshot-1", "snapshot-2"
            ])
            self.assertEqual(
                self.window.power_values["governor"].message, "snapshot-2"
            )
            self.assertFalse(self.window.pending_power_updates)
            self.assertFalse(self.window.pending_power_release)
        finally:
            release.set()

    def test_refresh_recovers_after_exception(self):
        attempts = []

        def collect():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("simulated telemetry failure")
            return object()

        APP.system_info.generate_system_report = collect
        self.window.refresh_in_thread()
        self.assertTrue(
            pump_until(lambda: self.window.refresh_error_label.visible)
        )
        self.assertIn(
            "simulated telemetry failure",
            self.window.refresh_error_label.message,
        )
        self.window.refresh_in_thread()
        self.assertTrue(pump_until(lambda: len(self.window.applied) == 1))
        self.assertFalse(self.window.refresh_error_label.visible)
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
