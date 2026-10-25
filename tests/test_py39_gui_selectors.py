"""Validate Python 3.9-compatible GUI selectors without importing GTK.

The real methods are extracted from the source AST and executed with
in-memory button doubles. No GUI, hardware, or privileged command runs.
"""
import ast
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "auto_cpufreq/gui/objects.py"
TREE = ast.parse(SOURCE.read_text())


class Button:
    def __init__(self):
        self.calls = []

    def set_active(self, value):
        self.calls.append(value)


class View:
    def __init__(self, names, set_by_app=True):
        for name in names:
            setattr(self, name, Button())
        self.set_by_app = set_by_app


def run_selected(class_name, getter_name, value, names, set_by_app=True):
    cls = next(n for n in TREE.body
               if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "set_selected")
    isolated = ast.Module(body=[method], type_ignores=[])
    namespace = {getter_name: lambda: value}
    exec(compile(isolated, filename=str(SOURCE), mode="exec"), namespace)
    view = View(names, set_by_app)
    namespace["set_selected"](view)
    activated = [name for name in names if getattr(view, name).calls == [True]]
    return activated, view.set_by_app


class GuiSelectionCompatibilityTests(unittest.TestCase):
    def test_governor_selection_and_unknown_state(self):
        for value, expected in [
            ("powersave", ["powersave"]),
            ("performance", ["performance"]),
            ("default", ["default"]),
            ("unrecognized", []),
        ]:
            with self.subTest(governor=value):
                names, flag = run_selected(
                    "RadioButtonView", "get_override", value,
                    ("powersave", "performance", "default"),
                )
                self.assertEqual(names, expected)
                self.assertEqual(flag, value != "default")

    def test_turbo_selection_and_unknown_state(self):
        for value, expected in [
            ("never", ["never"]),
            ("always", ["always"]),
            ("auto", ["auto"]),
            ("unrecognized", []),
        ]:
            with self.subTest(turbo=value):
                names, flag = run_selected(
                    "CPUTurboOverride", "get_turbo_override", value,
                    ("never", "always", "auto"),
                )
                self.assertEqual(names, expected)
                self.assertEqual(flag, value != "auto")

    def test_bluetooth_wildcard_fallback_includes_unknown(self):
        for value, expected in [
            ("off", ["off_btn"]),
            ("on", ["on_btn"]),
            ("unrecognized", ["on_btn"]),
            (None, ["on_btn"]),
        ]:
            with self.subTest(status=value):
                names, flag = run_selected(
                    "BluetoothBootControl", "get_bluetooth_boot_status", value,
                    ("off_btn", "on_btn"),
                )
                self.assertEqual(names, expected)
                self.assertEqual(flag, value == "off")

    def test_auto_choice_leaves_unset_sync_flag_alone(self):
        for cls, getter, value, attrs in [
            ("RadioButtonView", "get_override", "default",
             ("powersave", "performance", "default")),
            ("CPUTurboOverride", "get_turbo_override", "auto",
             ("never", "always", "auto")),
            ("BluetoothBootControl", "get_bluetooth_boot_status", "on",
             ("off_btn", "on_btn")),
        ]:
            with self.subTest(view=cls):
                _, flag = run_selected(cls, getter, value, attrs, set_by_app=False)
                self.assertFalse(flag)


if __name__ == "__main__":
    unittest.main()
