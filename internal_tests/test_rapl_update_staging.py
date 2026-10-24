"""Actual source updater staging with local Git release tags.

Exercise exact _install_update_from_staging/new_update function bodies from
production core.py and the actual installer tool_install function with an
explicitly synthetic package payload. No GitHub network, apt, services, or
physical Powercap access. This is not a full end-to-end CLI service upgrade.
"""
import ast
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory, mkdtemp
import unittest

from test_rapl_installer_transactions import SCRIPT as INSTALLER_FIXTURE, ROOT


def run(*args, **kwargs):
    result = subprocess.run(*args, **kwargs)
    return result


def build_updater(remote):
    source = (ROOT / "auto_cpufreq/core.py").read_text()
    parsed = ast.parse(source)
    subset = [
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_install_update_from_staging", "new_update"
        }
    ]
    assert len(subset) == 2, "Production updater functions were renamed"
    fragment = ast.fix_missing_locations(
        ast.Module(body=subset, type_ignores=[])
    )
    globals_for_module = {
        "GITHUB": "file://" + str(remote).removesuffix(".git"),
        "mkdtemp": mkdtemp,
        "rmtree": shutil.rmtree,
        "run": run,
        "print": lambda *args, **kwargs: None,
    }
    exec(compile(fragment, "core.py:extracted-updater", "exec"),
         globals_for_module)
    return globals_for_module["new_update"], globals_for_module


def git(*args, **kwargs):
    p = subprocess.run(("git", *map(str, args)), capture_output=True,
                       text=True, **kwargs)
    if p.returncode:
        raise AssertionError(
            f"Git failed: {args!r}\n{p.stdout}\n{p.stderr}"
        )
    return p


class GitReleaseUpdateStaging(unittest.TestCase):
    def test_real_git_tag_checkout_installer_and_staging_cleanup(self):
        if os.geteuid() != 0:
            self.skipTest("root fixture runs only in the dedicated isolated CI job")
        with TemporaryDirectory(prefix="auto-cpufreq-updater-lab-") as temp:
            root = Path(temp)
            seed = root / "seed"
            remote = root / "fixture.git"
            staged = root / "staging"
            lab_root = root / "isolated-host"
            staged.mkdir()
            seed.mkdir()
            git("init", "-q", seed)
            git("-C", seed, "config", "user.email", "lab@example.invalid")
            git("-C", seed, "config", "user.name", "CI lab")
            (seed / "scripts").mkdir()
            shutil.copyfile(ROOT / "auto-cpufreq-installer",
                            seed / "auto-cpufreq-installer")
            shutil.copyfile(ROOT / "scripts/auto-cpufreq-venv-wrapper",
                            seed / "scripts/auto-cpufreq-venv-wrapper")
            (seed / "tag.txt").write_text("v3.2.1\n")
            git("-C", seed, "add", ".")
            git("-C", seed, "commit", "-q", "-m", "Synthetic test release")
            git("-C", seed, "tag", "v3.2.1")
            git("clone", "-q", "--bare", seed, remote)

            installation_prefix = INSTALLER_FIXTURE.split(
                'tool_install > "$LAB_ROOT/first.log"', 1
            )[0]
            self.assertIn("build_release()", installation_prefix)
            self.assertIn("source <(sed", installation_prefix)

            def checked_run(command, **kwargs):
                if command[:2] == ["bash", "./auto-cpufreq-installer"]:
                    checkout = Path(kwargs["cwd"])
                    self.assertEqual(
                        (checkout / "tag.txt").read_text(), "v3.2.1\n"
                    )
                    shell = installation_prefix.replace(
                        'export MOCK_VERSION="3.2.0"',
                        'export MOCK_VERSION="3.2.1"',
                    )
                    shell += (
                        '\ntool_install > "$LAB_ROOT/updater.log" 2>&1'
                        ' || { cat "$LAB_ROOT/updater.log"; exit 91; }\n'
                        'assert_active "3.2.1" || exit 92\n'
                    )
                    env = {
                        **os.environ,
                        "INSTALLER": str(checkout / "auto-cpufreq-installer"),
                        "REPO_ROOT": str(checkout),
                        "LAB_ROOT": str(lab_root),
                        "TERM": "xterm",
                    }
                    return subprocess.run(
                        ["bash", "-c", shell], cwd=checkout, env=env,
                        capture_output=True, text=True,
                        timeout=50,
                    )
                return subprocess.run(
                    command, capture_output=True, text=True, **kwargs
                )

            updater, scope = build_updater(remote)
            scope["run"] = checked_run
            self.assertTrue(updater(str(staged), "v3.2.1"))
            self.assertEqual(list(staged.iterdir()), [],
                             "Updater must delete its temporary checkout")
            current = lab_root / "opt/auto-cpufreq/current"
            self.assertTrue(current.is_symlink())
            self.assertEqual((lab_root / "usr/local/bin/auto-cpufreq").exists(),
                             True)
            target_before = current.resolve()
            self.assertTrue((target_before / ".complete").exists())

            # A tag that does not exist must fail safely without switching
            # the previously active source release or leaving staging dirt.
            self.assertFalse(updater(str(staged), "v3.2.2"))
            self.assertEqual(list(staged.iterdir()), [])
            self.assertEqual(current.resolve(), target_before)

            # Failure after successful checkout but before installation
            # must also preserve the current release and clean staging.
            def failing_install(command, **kwargs):
                if command[:2] == ["bash", "./auto-cpufreq-installer"]:
                    return subprocess.CompletedProcess(command, 17)
                return subprocess.run(command, capture_output=True,
                                      text=True, **kwargs)
            scope["run"] = failing_install
            self.assertFalse(updater(str(staged), "v3.2.1"))
            self.assertEqual(list(staged.iterdir()), [])
            self.assertEqual(current.resolve(), target_before)


if __name__ == "__main__":
    unittest.main()
