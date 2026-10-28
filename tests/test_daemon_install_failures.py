"""Characterize daemon-install failure ordering in the upstream core.

This executes the actual deploy_daemon() function body with simulated side
effects, without accessing system power settings or /usr/local/bin.
"""
import ast
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def deploy_function():
    tree = ast.parse((ROOT / "auto_cpufreq/core.py").read_text())
    original = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "deploy_daemon")
    return compile(
        ast.fix_missing_locations(
            ast.Module(body=[original], type_ignores=[])
        ), str(ROOT / "auto_cpufreq/core.py"), "exec"
    )


class DaemonInstallFailureOrderingTests(unittest.TestCase):
    def test_failed_service_setup_leaves_helper_marker_and_side_effects(self):
        # Existing behavior, not a claimed solution: the helper marker is
        # published and competing services disabled before the shell installer
        # has confirmed it can deploy the service.
        events = []
        markers = set()

        def recorded(name):
            return lambda: events.append(name)

        def copy(source, target):
            events.append("copy:" + Path(target).name)
            markers.add(target)

        def call(args, **kwargs):
            if isinstance(args, list) and args[0] == "chmod":
                return 0
            events.append("service_install_failed")
            return 23

        context = {
            "SCRIPTS_DIR": Path("/synthetic/source/scripts"),
            "cpufreqctl": recorded("cpufreqctl"),
            "bluetooth_disable": recorded("bluetooth_disabled"),
            "gnome_power_detect_install": recorded("gnome_detected"),
            "gnome_power_svc_disable": recorded("gnome_disabled"),
            "tuned_svc_disable": recorded("tuned_disabled"),
            "tlp_service_detect": recorded("tlp_detected"),
            "auto_cpufreq_stats_path": types.SimpleNamespace(
                touch=lambda **kwargs: events.append("stats_touched")
            ),
            "copy": copy,
            "call": call,
        }
        exec(deploy_function(), context)
        result = context["deploy_daemon"]()
        self.assertEqual(result, 23)
        self.assertIn("/usr/local/bin/auto-cpufreq-remove", markers)
        self.assertIn("bluetooth_disabled", events)
        self.assertIn("gnome_disabled", events)
        self.assertIn("tuned_disabled", events)
        self.assertGreater(
            events.index("service_install_failed"),
            events.index("tuned_disabled"),
        )


if __name__ == "__main__":
    unittest.main()
