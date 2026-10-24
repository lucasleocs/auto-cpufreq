"""Static CLI wiring regression without running privileged auto-cpufreq modes.

This verifies the Click entrypoint's real source AST and exact keyword wiring;
PTY behavior of SystemMonitor is covered in test_stats_lifecycle.py.
"""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / "auto_cpufreq/bin/auto_cpufreq.py"


class CliDiagnosticWiringTests(unittest.TestCase):
    def test_flag_is_registered_guarded_and_forwarded_to_every_ui_mode(self):
        source = ENTRY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        main = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        self.assertIn("diagnostic_log", [arg.arg for arg in main.args.args])
        flag_decorators = [
            ast.unparse(decorator)
            for decorator in main.decorator_list
            if isinstance(decorator, ast.Call)
        ]
        self.assertTrue(any("--diagnostic-log" in item
                            for item in flag_decorators))
        constructors = [
            call for call in ast.walk(main)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "SystemMonitor"
        ]
        self.assertEqual(len(constructors), 3)
        passed_modes = set()
        for call in constructors:
            keywords = {kw.arg: kw.value for kw in call.keywords}
            self.assertIn("diagnostic_log", keywords)
            self.assertIsInstance(keywords["diagnostic_log"], ast.Name)
            self.assertEqual(keywords["diagnostic_log"].id, "diagnostic_log")
            self.assertIn("type", keywords)
            passed_modes.add(ast.unparse(keywords["type"]))
        self.assertEqual(
            passed_modes,
            {"ViewType.STATS", "ViewType.MONITOR", "ViewType.LIVE"},
        )
        self.assertIn(
            "--diagnostic-log requires --stats, --monitor or --live", source,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
