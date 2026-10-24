"""GTK window/Monitor shutdown contracts without opening a display.

These tests exercise the real lifecycle methods with controlled collaborators.
They do not create windows, install services or interact with real hardware.
"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from auto_cpufreq.gui import app, monitor_view


class FrontendLifecycleTests(unittest.TestCase):
    def dashboard(self):
        return SimpleNamespace(
            destroyed=False,
            _refresh_coordinator=Mock(),
            _refresh_timer=Mock(),
            power_apply_sources={"governor": 17, "turbo": 23},
            pending_power_updates={"governor", "turbo"},
            pending_power_release={"governor"},
            refresh_pending=True,
        )

    def test_dashboard_cleanup_is_idempotent_and_removes_timers(self):
        for _ in range(20):
            window = self.dashboard()
            with patch.object(app.GLib, "source_remove") as remove:
                with patch.object(app, "diagnostics") as log:
                    app.ToolWindow._cleanup_refresh(window)
                    app.ToolWindow._cleanup_refresh(window)

            self.assertTrue(window.destroyed)
            window._refresh_coordinator.close.assert_called_once_with()
            window._refresh_timer.stop.assert_called_once_with()
            self.assertEqual(
                sorted(call.args[0] for call in remove.call_args_list),
                [17, 23],
            )
            self.assertFalse(window.refresh_pending)
            self.assertEqual(window.power_apply_sources, {})
            self.assertEqual(window.pending_power_updates, set())
            self.assertEqual(window.pending_power_release, set())
            log.set_error_callback.assert_called_once_with(None)

    def test_monitor_cleanup_is_idempotent_and_disables_refresh(self):
        for _ in range(20):
            monitor = SimpleNamespace(
                running=True,
                _refresh_coordinator=Mock(),
                _refresh_timer=Mock(),
            )
            with patch.object(monitor_view, "diagnostics") as log:
                monitor_view.MonitorModeView.cleanup(monitor)
                monitor_view.MonitorModeView.cleanup(monitor)
            self.assertFalse(monitor.running)
            monitor._refresh_coordinator.close.assert_called_once_with()
            monitor._refresh_timer.stop.assert_called_once_with()
            log.info.assert_called_once()
            self.assertFalse(
                monitor_view.MonitorModeView.refresh_in_thread(monitor)
            )
            monitor._refresh_coordinator.request.assert_not_called()
            self.assertFalse(
                monitor_view.MonitorModeView._render_display(
                    monitor, None, None
                )
            )

    def test_back_navigation_cleans_before_rebuilding_dashboard(self):
        calls = Mock()
        parent = SimpleNamespace(
            remove=calls.remove,
            daemon_not_running=calls.daemon_not_running,
            show_all=calls.show_all,
        )
        monitor = SimpleNamespace(
            parent=parent, cleanup=calls.cleanup
        )
        monitor_view.MonitorModeView.on_back_clicked(monitor, None)
        self.assertEqual(
            [call[0] for call in calls.mock_calls],
            ["cleanup", "remove", "daemon_not_running", "show_all"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
