"""Regression checks for unused pre-unification core reporting paths.

Production imports must remain possible via the active CLI and report pipeline.
The tests are kept on the lab branch, not in the PR candidate.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
DELETED = {
    "mon_powersave", "mon_performance", "mon_autofreq", "sysinfo", "read_stats",
}

class DeadLegacyCoreTests(unittest.TestCase):
    def test_no_references_to_retired_symbols_in_product_python(self):
        for path in sorted((ROOT / "auto_cpufreq").rglob("*.py")):
            with self.subTest(path=str(path.relative_to(ROOT))):
                tree = ast.parse(path.read_text(), filename=str(path))
                definitions = {
                    node.name for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                self.assertFalse(definitions & DELETED)
                names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
                attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
                self.assertFalse((names | attributes) & DELETED)

    def test_current_governor_policy_remains_callable_by_cli(self):
        core = ast.parse((ROOT / "auto_cpufreq/core.py").read_text())
        functions = {
            node.name: node for node in core.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in ("set_autofreq", "set_performance", "set_powersave",
                     "get_current_gov", "python_info", "device_info", "distro_info"):
            self.assertIn(name, functions)
        calls = {
            node.func.id for node in ast.walk(functions["set_autofreq"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertGreaterEqual(calls & {"set_performance", "set_powersave"},
                                {"set_performance", "set_powersave"})

    def test_current_frontends_use_structured_report_pipeline(self):
        cli = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
        status = (ROOT / "auto_cpufreq/modules/system_info.py").read_text()
        monitor = (ROOT / "auto_cpufreq/modules/system_monitor.py").read_text()
        self.assertIn("SystemMonitor(", cli)
        self.assertIn("print_system_report(", cli)
        self.assertIn("def print_system_report(", status)
        self.assertIn("class SystemMonitor:", monitor)


if __name__ == "__main__":
    unittest.main()
