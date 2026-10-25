"""Static and behavior regression for narrowly removed non-consumed helpers.

This test intentionally stays on the lab branch; the production tree keeps
only the small code deletion. No GTK or host hardware is required.
"""
import ast
from dataclasses import fields
from pathlib import Path
import unittest

from auto_cpufreq.modules.report_presentation import Availability, DisplayValue
from auto_cpufreq.modules.report_recommendations import Recommendation, RecommendationStatus
from auto_cpufreq.modules.report_refresh import RefreshResult

ROOT = Path(__file__).resolve().parents[1]
REMOVED = {
    "auto_cpufreq/modules/report_presentation.py": "is_available",
    "auto_cpufreq/modules/report_recommendations.py": "needs_change",
    "auto_cpufreq/modules/report_refresh.py": "succeeded",
    "auto_cpufreq/power_helper.py": "valid_options",
}


class RemovedHelpersTests(unittest.TestCase):
    def test_removed_names_have_no_python_references(self):
        for path in sorted((ROOT / "auto_cpufreq").rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for name in REMOVED.values():
                with self.subTest(path=path.relative_to(ROOT), name=name):
                    definitions = {
                        n.name for n in ast.walk(tree)
                        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    }
                    references = {
                        n.id for n in ast.walk(tree)
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    }
                    attributes = {
                        n.attr for n in ast.walk(tree)
                        if isinstance(n, ast.Attribute)
                    }
                    self.assertNotIn(name, definitions | references | attributes)

    def test_data_class_fields_remain_unchanged(self):
        self.assertEqual(
            [f.name for f in fields(DisplayValue)],
            ["value", "availability", "text"],
        )
        self.assertEqual(
            [f.name for f in fields(Recommendation)],
            ["status", "current", "target"],
        )
        self.assertEqual(
            [f.name for f in fields(RefreshResult)],
            ["generation", "report", "error", "duration",
             "queue_seconds", "requested_at", "completed_at"],
        )
        self.assertEqual(
            DisplayValue(5, Availability.AVAILABLE, "5").text, "5",
        )
        self.assertEqual(
            Recommendation(RecommendationStatus.CHANGE, target="powersave").suggested,
            "powersave",
        )
        self.assertIsNone(
            Recommendation(RecommendationStatus.UNAVAILABLE).suggested,
        )
        self.assertIsNone(
            RefreshResult(1, None, None, 0.2).error,
        )

    def test_retained_active_functions_are_present(self):
        checks = {
            "auto_cpufreq/modules/report_presentation.py": ("present_report",),
            "auto_cpufreq/modules/report_recommendations.py": ("evaluate_recommendations",),
            "auto_cpufreq/modules/report_refresh.py": ("request_age_seconds",),
            "auto_cpufreq/power_helper.py": ("gnome_power_rm_reminder",),
        }
        for file, names in checks.items():
            src = (ROOT / file).read_text()
            for name in names:
                self.assertIn("def " + name + "(", src)


if __name__ == "__main__":
    unittest.main()
