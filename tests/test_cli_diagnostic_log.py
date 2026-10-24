"""CLI opt-in logging contracts; no daemon operations or privilege changes."""
import unittest
from unittest.mock import Mock, patch

from click.testing import CliRunner

from auto_cpufreq.bin import auto_cpufreq as cli


class CliDiagnosticLogTests(unittest.TestCase):
    def test_help_exposes_opt_in_switch_separately_from_debug(self):
        result = CliRunner().invoke(cli.main, ["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--diagnostic-log", result.output)
        self.assertIn("--debug", result.output)

    def test_switch_does_not_apply_to_other_modes(self):
        for args in (
            ["--diagnostic-log"],
            ["--diagnostic-log", "--debug"],
            ["--diagnostic-log", "--version"],
            ["--diagnostic-log", "--install"],
        ):
            with self.subTest(args=args):
                result = CliRunner().invoke(cli.main, args)
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("requires --stats, --monitor or --live", result.output)

    def test_debug_is_separate_from_interactive_modes(self):
        for mode in ("--stats", "--monitor", "--live"):
            with self.subTest(mode=mode):
                for extra in ([], ["--diagnostic-log"]):
                    result = CliRunner().invoke(
                        cli.main, [mode, "--debug"] + extra
                    )
                    self.assertNotEqual(result.exit_code, 0)
                    self.assertIn(
                        "--debug is a separate one-shot command",
                        result.output,
                    )

    def test_diagnostic_log_rejects_multiple_modes_and_policy_flags(self):
        for args in (
            ["--stats", "--monitor"],
            ["--stats", "--live"],
            ["--stats", "--force", "performance"],
            ["--stats", "--turbo", "always"],
            ["--stats", "--remove"],
            ["--stats", "--update"],
            ["--stats", "--install"],
        ):
            with self.subTest(args=args):
                result = CliRunner().invoke(
                    cli.main, ["--diagnostic-log"] + args
                )
                self.assertNotEqual(result.exit_code, 0)
                self.assertIn("--diagnostic-log", result.output)

    def test_stats_passes_opt_in_flag_without_starting_daemon(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                instance = Mock()
                with patch.object(cli, "SystemMonitor", return_value=instance) as factory:
                    with patch.object(cli, "not_running_daemon_check"):
                        with patch.object(cli, "gnome_power_detect"):
                            with patch.object(cli, "tlp_service_detect"):
                                with patch.object(cli, "IS_INSTALLED_WITH_SNAP", False):
                                    with patch.object(cli, "tlp_stat_exists", False):
                                        with patch.object(cli, "systemctl_exists", False):
                                            with patch.object(cli.conf, "set_path"):
                                                with patch.object(cli.conf, "has_config", return_value=False):
                                                    args = ["--stats"]
                                                    if enabled:
                                                        args.append("--diagnostic-log")
                                                    result = CliRunner().invoke(cli.main, args)
                self.assertEqual(result.exit_code, 0, result.output)
                factory.assert_called_once_with(
                    type=cli.ViewType.STATS, diagnostic_log=enabled
                )
                instance.run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
