"""Terminal telemetry survives failure of optional diagnostic startup."""
import unittest
from unittest.mock import Mock, patch

from auto_cpufreq.modules import system_monitor


class TerminalDiagnosticFailureTests(unittest.TestCase):
    def test_unwritable_diagnostic_destination_does_not_abort_stats(self):
        monitor = system_monitor.SystemMonitor(type=system_monitor.ViewType.STATS)
        diagnostic = Mock()
        diagnostic._activate.side_effect = OSError("log directory read-only")
        diagnostic.error_status.return_value = (
            1, "Unable to write diagnostic log: log directory read-only"
        )
        monitor._diagnostics = diagnostic
        monitor.loop.watch_pipe = Mock(return_value=34567)
        monitor.loop.set_alarm_in = Mock(return_value="alarm")
        monitor.loop.run = Mock()
        monitor.loop.remove_alarm = Mock()
        monitor.loop.remove_watch_pipe = Mock()

        with patch.object(system_monitor.os, "set_blocking") as set_blocking:
            with patch.object(system_monitor.os, "close") as close:
                monitor.run()

        set_blocking.assert_called_once_with(34567, False)
        monitor.loop.run.assert_called_once_with()
        monitor.loop.remove_alarm.assert_called_once_with("alarm")
        monitor.loop.remove_watch_pipe.assert_called_once_with(34567)
        close.assert_called_once_with(34567)
        diagnostic._report_error.assert_called_once()
        diagnostic.shutdown.assert_called_once_with()
        self.assertIn(
            "Diagnostic log write failure", monitor.footer_text.text
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
