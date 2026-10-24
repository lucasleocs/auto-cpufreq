"""Original --update CLI branch control flow with mocked OS effects.

Extracts the exact 'elif update:' body from production source as AST. It is
intentionally not a whole-process executable test: no daemon, package manager,
real system paths or network are used. Complements the real local-tag staging
and source-installer transaction tests.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parent.parent


class MockSystemExit(SystemExit):
    pass


def update_branch():
    tree = ast.parse((ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text())
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name == "main"]
    assert len(funcs) == 1, "CLI main signature changed"
    matches = []
    for node in ast.walk(funcs[0]):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name):
            if node.test.id == "update" and any(
                isinstance(expr, ast.Expr)
                and isinstance(expr.value, ast.Call)
                and isinstance(expr.value.func, ast.Name)
                and expr.value.func.id == "root_check"
                for expr in node.body[:2]
            ):
                matches.append(node)
    assert len(matches) == 1, "The original --update control-flow branch changed"
    func = ast.FunctionDef(
        name="run_original_update", args=ast.arguments(
            posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[],
            defaults=[],
        ),
        body=matches[0].body,
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[]))
    return compile(module, "production-cli-update-branch", "exec")


CODE = update_branch()


def scenario(*, stop=0, new=True, install=0, restore=0, version="3.2.1",
             installed=True):
    events = []
    argv = ["auto-cpufreq", "--update"]
    def end(code):
        raise MockSystemExit(code)
    sys = SimpleNamespace(argv=argv, exit=end)
    os = SimpleNamespace(
        path=SimpleNamespace(exists=lambda path: installed),
        makedirs=lambda *a, **k: events.append("mkdir"),
    )
    def remove_daemon():
        events.append("remove")
        return stop
    def new_update(*args):
        events.append("new_update")
        return new
    def run(command, **kwargs):
        action = command[-1]
        events.append(action)
        if action == "--version":
            return SimpleNamespace(returncode=0,
                                   stdout="auto-cpufreq version: " + version)
        return SimpleNamespace(returncode=restore if "restore" in events else install)
    def launch_install(command, **kwargs):
        action = command[-1]
        events.append(action)
        if action == "--version":
            return SimpleNamespace(returncode=0,
                                   stdout="auto-cpufreq version: " + version)
        if not new and action == "--install":
            return SimpleNamespace(returncode=restore)
        return SimpleNamespace(returncode=install)

    scope = {
        "sys": sys, "os": os, "root_check": lambda: events.append("root"),
        "IS_INSTALLED_WITH_SNAP": False,
        "IS_INSTALLED_WITH_AUR": False,
        "check_for_update": lambda: "v3.2.1",
        "input": lambda *a: "y",
        "remove_daemon": remove_daemon,
        "remove_complete_msg": lambda: events.append("removed-message"),
        "new_update": new_update,
        "run": launch_install,
        "parse_version_output": lambda output: (
            output.removeprefix("auto-cpufreq version: ")
        ),
        "print": lambda *a, **kw: None,
        "update": True,
    }
    exec(CODE, scope)
    try:
        scope["run_original_update"]()
        status = 0
    except MockSystemExit as exc:
        status = exc.code
    return status, events


class CliUpdaterFlowTests(unittest.TestCase):
    def test_stop_failure_prevents_version_switch_and_install(self):
        status, events = scenario(stop=3)
        self.assertEqual(status, 1)
        self.assertIn("remove", events)
        self.assertNotIn("new_update", events)
        self.assertNotIn("--install", events)

    def test_source_update_failure_reinstalls_original_daemon(self):
        status, events = scenario(new=False)
        self.assertEqual(status, 1)
        self.assertLess(events.index("remove"), events.index("new_update"))
        self.assertLess(events.index("new_update"), events.index("--install"))
        self.assertEqual(events.count("--install"), 1)

    def test_success_updates_then_installs_and_checks_version(self):
        status, events = scenario()
        self.assertEqual(status, 0)
        self.assertLess(events.index("remove"), events.index("new_update"))
        self.assertLess(events.index("new_update"), events.index("--install"))
        self.assertLess(events.index("--install"), events.index("--version"))

    def test_post_update_daemon_install_failure_is_reported(self):
        status, events = scenario(install=2)
        self.assertEqual(status, 1)
        self.assertEqual(events.count("--install"), 1)
        self.assertNotIn("--version", events)

    def test_no_installed_daemon_never_calls_service_removal(self):
        status, events = scenario(installed=False)
        self.assertEqual(status, 0)
        self.assertNotIn("remove", events)
        self.assertNotIn("--install", events)
        self.assertIn("new_update", events)


if __name__ == "__main__":
    unittest.main()
