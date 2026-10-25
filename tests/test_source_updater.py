"""Exercise the real source updater's Git-tag staging without remote network.

Runs real git fetch/checkout against an isolated local bare repository, while
replacing ONLY the privileged installer invocation with a known return code.
All cloned source trees are temporary; no system service is altered.
"""
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "auto_cpufreq/core.py").read_text())
SYMBOLS = {"_install_update_from_staging", "new_update"}
FUNCTIONS = ast.Module(
    body=[node for node in TREE.body if isinstance(node, ast.FunctionDef)
          and node.name in SYMBOLS],
    type_ignores=[],
)
assert len(FUNCTIONS.body) == 2
compile(FUNCTIONS, str(ROOT / "auto_cpufreq/core.py"), "exec")


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        timeout=15,
    ).stdout.strip()


class SourceUpdaterTagTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.origin = self.base / "origin.git"
        self.source = self.base / "author"
        git("init", "--bare", str(self.origin))
        git("init", str(self.source))
        git("config", "user.name", "E2E Fixture", cwd=self.source)
        git("config", "user.email", "fixture@example.invalid", cwd=self.source)
        (self.source / "test.txt").write_text("tagged release\n")
        git("add", "test.txt", cwd=self.source)
        git("commit", "-m", "Release commit", cwd=self.source)
        self.release_sha = git("rev-parse", "HEAD", cwd=self.source)
        git("tag", "v3.2.1", cwd=self.source)
        # Deliberately create a branch with EXACTLY the same name, at a
        # different commit; the updater must select the TAG.
        (self.source / "test.txt").write_text("wrong branch\n")
        git("commit", "-am", "Branch must not be selected", cwd=self.source)
        self.branch_sha = git("rev-parse", "HEAD", cwd=self.source)
        git("branch", "v3.2.1", cwd=self.source)
        git("remote", "add", "origin", str(self.origin), cwd=self.source)
        git("push", "origin", "refs/tags/v3.2.1", "refs/heads/v3.2.1", cwd=self.source)
        self.assertNotEqual(self.release_sha, self.branch_sha)
        self.installer_calls = []
        self.injected_failure = False

        def run(command, cwd=None):
            if command == ["bash", "./auto-cpufreq-installer", "--install"]:
                self.installer_calls.append({
                    "cwd": cwd, "commit": git("rev-parse", "HEAD", cwd=cwd),
                    "content": (Path(cwd) / "test.txt").read_text(),
                })
                return SimpleNamespace(returncode=17 if self.injected_failure else 0)
            return subprocess.run(command, cwd=cwd, check=False, timeout=20)

        import shutil
        import tempfile as tm
        self.scope = {
            "run": run,
            "GITHUB": str(self.origin)[:-4],
            "mkdtemp": tm.mkdtemp,
            "rmtree": shutil.rmtree,
            "print": lambda *args, **kwargs: None,
            "OSError": OSError,
        }
        exec(compile(FUNCTIONS, str(ROOT / "auto_cpufreq/core.py"), "exec"), self.scope)

    def test_selects_tag_not_identically_named_branch_and_cleans_staging(self):
        self.assertTrue(self.scope["new_update"](str(self.base), "v3.2.1"))
        self.assertEqual(len(self.installer_calls), 1)
        self.assertEqual(self.installer_calls[0]["commit"], self.release_sha)
        self.assertEqual(self.installer_calls[0]["content"], "tagged release\n")
        self.assertEqual(list(self.base.glob("auto-cpufreq-*")), [])

    def test_install_failure_propagates_without_leaking_staging(self):
        self.injected_failure = True
        self.assertFalse(self.scope["new_update"](str(self.base), "v3.2.1"))
        self.assertEqual(len(self.installer_calls), 1)
        self.assertEqual(self.installer_calls[0]["commit"], self.release_sha)
        self.assertEqual(list(self.base.glob("auto-cpufreq-*")), [])

    def test_missing_tag_fails_without_invoking_installer(self):
        self.assertFalse(self.scope["new_update"](str(self.base), "v9.9.9"))
        self.assertEqual(self.installer_calls, [])
        self.assertEqual(list(self.base.glob("auto-cpufreq-*")), [])

    def test_invalid_staging_parent_is_rejected(self):
        self.assertFalse(self.scope["new_update"](str(self.base / "missing"), "v3.2.1"))
        self.assertEqual(self.installer_calls, [])

    def test_tag_fetch_precedes_installer_in_actual_source(self):
        source = (ROOT / "auto_cpufreq/core.py").read_text()
        self.assertIn("f\"refs/tags/{target_tag}:refs/tags/{target_tag}\"", source)
        self.assertIn("f\"refs/tags/{target_tag}^{{commit}}\"", source)
        self.assertIn("finally:", source)


if __name__ == "__main__":
    unittest.main()
