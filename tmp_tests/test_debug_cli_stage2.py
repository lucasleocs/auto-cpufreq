import ast
from pathlib import Path
import unittest


class DebugCliStage2Tests(unittest.TestCase):
    def test_debug_passes_snap_context_to_diagnostics(self):
        source = Path("auto_cpufreq/bin/auto_cpufreq.py").read_text()
        tree = ast.parse(source)

        debug_body = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "debug"
            ):
                debug_body = node.body
                break

        self.assertIsNotNone(debug_body)
        collect_call = None
        for statement in debug_body:
            for node in ast.walk(statement):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "collect_diagnostics"
                ):
                    collect_call = node
                    break
            if collect_call is not None:
                break

        self.assertIsNotNone(collect_call)
        keywords = {keyword.arg: keyword.value for keyword in collect_call.keywords}
        self.assertIn("is_snap", keywords)
        self.assertIsInstance(keywords["is_snap"], ast.Name)
        self.assertEqual(keywords["is_snap"].id, "IS_INSTALLED_WITH_SNAP")


if __name__ == "__main__":
    unittest.main()
