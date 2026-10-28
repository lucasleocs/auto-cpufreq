"""Isolated tests of the real CLI update branch, with host operations stubbed.

Use the AST of the actual command branch, not a copied reimplementation of
the updater. No external command or system installation path is modified.
"""
import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def update_branch_function():
    source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
    tree = ast.parse(source)
    branch = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "update"
        and node.body
        and isinstance(node.body[0], ast.Expr)
    )
    fn = ast.FunctionDef(
        name="run_real_update_branch",
        args=ast.arguments(
            posonlyargs=[], args=[], vararg=None, kwonlyargs=[],
            kw_defaults=[], kwarg=None, defaults=[],
        ),
        body=branch.body, decorator_list=[], returns=None, type_comment=None,
    )
    return compile(
        ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
        str(ROOT / "auto_cpufreq/bin/auto_cpufreq.py"),
        "exec",
    )


class UpdateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.branch_code = update_branch_function()

    def exercise(self, *, installed=True, remove_status=0,
                 update_success=True, install_status=0):
        events = []
        output = io.StringIO()

        def exit_with(code):
            raise SystemExit(code)

        def fake_run(args, **kwargs):
            events.append(("run", tuple(args)))
            if args[-1] == "--version":
                return types.SimpleNamespace(
                    returncode=0, stdout="auto-cpufreq 3.2.0",
                )
            return types.SimpleNamespace(returncode=install_status)

        def fake_remove():
            events.append(("remove",))
            return remove_status

        def fake_update(path, version):
            events.append(("update", version))
            return update_success

        def fake_exists(path):
            self.assertEqual(path, "/usr/local/bin/auto-cpufreq-remove")
            return installed

        context = {
            "root_check": lambda: events.append(("root_check",)),
            "sys": types.SimpleNamespace(
                argv=["auto-cpufreq", "--update"], exit=exit_with,
            ),
            "os": types.SimpleNamespace(
                path=types.SimpleNamespace(exists=fake_exists),
                makedirs=lambda path, exist_ok: None,
            ),
            "IS_INSTALLED_WITH_SNAP": False,
            "IS_INSTALLED_WITH_AUR": False,
            "check_for_update": lambda: "v3.2.0",
            "input": lambda _prompt: "y",
            "remove_daemon": fake_remove,
            "remove_complete_msg": lambda: events.append(("removed",)),
            "new_update": fake_update,
            "run": fake_run,
            "parse_version_output": lambda value: "3.2.0",
        }
        exec(self.branch_code, context)
        with redirect_stdout(output):
            try:
                context["run_real_update_branch"]()
            except SystemExit as exc:
                return events, exc.code, output.getvalue()
        return events, 0, output.getvalue()

    def test_update_failure_restores_daemon_if_installed(self):
        events, code, _ = self.exercise(update_success=False)
        self.assertEqual(code, 1)
        self.assertEqual(
            [x[0] for x in events],
            ["root_check", "remove", "removed", "update", "run"],
        )
        self.assertEqual(events[-1][1], (
            "/usr/local/bin/auto-cpufreq", "--install",
        ))

    def test_remove_failure_aborts_before_updating(self):
        events, code, _ = self.exercise(remove_status=1)
        self.assertEqual(code, 1)
        self.assertEqual([x[0] for x in events], ["root_check", "remove"])

    def test_successful_update_reinstalls_previous_daemon(self):
        events, code, _ = self.exercise()
        self.assertEqual(code, 0)
        self.assertEqual(
            [x[0] for x in events],
            ["root_check", "remove", "removed", "update", "run", "run"],
        )
        self.assertEqual(events[-2][1][-1], "--install")
        self.assertEqual(events[-1][1][-1], "--version")

    def test_failed_update_without_daemon_does_not_create_one(self):
        events, code, _ = self.exercise(installed=False, update_success=False)
        self.assertEqual(code, 1)
        self.assertEqual(
            [x[0] for x in events], ["root_check", "update"],
        )

    def test_successful_update_with_daemon_reinstall_failure_is_reported(self):
        events, code, text = self.exercise(install_status=1)
        self.assertEqual(code, 1)
        self.assertIn("could not be installed", text)
        self.assertEqual(
            [x[0] for x in events],
            ["root_check", "remove", "removed", "update", "run"],
        )


if __name__ == "__main__":
    unittest.main()
