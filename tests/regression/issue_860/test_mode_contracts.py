"""Structural guardrails for read-only frontend modes before adapter refactoring.

These checks never import privileged CLI modules or touch sysfs.
"""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


def module(relative_path):
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def method(tree, name):
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def cli_branches():
    main = method(module("auto_cpufreq/bin/auto_cpufreq.py"), "main")
    return {
        node.test.id: ast.unparse(node.body)
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id in {"stats", "monitor", "live", "daemon", "debug"}
    }


class ModeBoundaryTests(unittest.TestCase):
    def test_read_only_terminal_modes_never_apply_policy(self):
        branches = cli_branches()
        for name in ("stats", "monitor", "debug"):
            with self.subTest(mode=name):
                for policy in (
                    "set_autofreq(", "cpufreqctl(", "set_override(",
                    "set_turbo_override(", "start_battery_daemon(",
                    "LiveModeSession(", "session.prepare(",
                ):
                    self.assertNotIn(policy, branches[name])
                self.assertNotIn("conf.notifier.start()", branches[name]
                                     if name != "monitor" else "")
        self.assertIn("conf.notifier.start()", branches["monitor"])
        self.assertIn("conf.stop_watching()", branches["monitor"])
        self.assertIn("LiveModeSession(", branches["live"])
        self.assertIn("set_autofreq()", branches["daemon"])

    def test_gtk_entrypoint_does_not_start_config_observer(self):
        entry = ast.unparse(module("auto_cpufreq/bin/auto_cpufreq_gtk.py"))
        self.assertIn("config.set_path(", entry)
        self.assertNotIn("notifier.start(", entry)
        self.assertNotIn("set_autofreq(", entry)

    def test_both_monitors_use_common_recommendation_contract(self):
        for file, function in (
            ("auto_cpufreq/gui/objects.py", "_collect_report"),
            ("auto_cpufreq/modules/system_monitor.py", "_collect_report_data"),
        ):
            with self.subTest(file=file):
                tree = module(file)
                target = method(tree, function)
                text = ast.unparse(target)
                self.assertIn("monitor_recommendations(report)", text)
                self.assertNotIn("governor_suggestion(report)", text)
                self.assertNotIn("turbo_on_suggestion(report)", text)

    def test_both_gtk_views_delegate_delivery_to_single_bridge(self):
        for filename, classname in (
            ("auto_cpufreq/gui/app.py", "ToolWindow"),
            ("auto_cpufreq/gui/objects.py", "MonitorModeView"),
        ):
            with self.subTest(view=classname):
                tree = module(filename)
                klass = next(
                    node for node in tree.body
                    if isinstance(node, ast.ClassDef)
                    and node.name == classname
                )
                source = ast.unparse(klass)
                self.assertIn("GtkRefreshBridge(", source)
                self.assertIn("self._refresh_bridge.coordinator", source)
                self.assertNotIn("ReportRefreshCoordinator(", source)

    def test_report_recommendations_has_no_privileged_runtime_import(self):
        tree = module("auto_cpufreq/modules/report_recommendations.py")
        imports = [
            alias.name for node in tree.body
            if isinstance(node, ast.Import) for alias in node.names
        ] + [
            node.module for node in tree.body if isinstance(node, ast.ImportFrom)
        ]
        self.assertEqual(set(imports), {"dataclasses", "enum", "math"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
