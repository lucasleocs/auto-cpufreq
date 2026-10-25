"""Isolated OpenRC and dinit service ownership regressions.

This harness extracts the real shell functions and init case from the installer
scripts, redirects only /etc service paths to a temporary tree, and stubs service
manager commands. No host services, privilege or /etc files are touched.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

INSTALL = Path("scripts/auto-cpufreq-install.sh")
REMOVE = Path("scripts/auto-cpufreq-remove.sh")


def extract_function(source, name):
    start = source.index(f"function {name} {{")
    end = source.index("\n}", start) + 2
    return source[start:end]


class ServiceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.share = self.root / "share"
        self.templates = self.share / "scripts"
        self.templates.mkdir(parents=True)
        (self.templates / "auto-cpufreq-openrc").write_text(
            "#!/sbin/openrc-run\ncommand=/usr/local/bin/auto-cpufreq\n"
        )
        (self.templates / "auto-cpufreq-dinit").write_text(
            "type = scripted\ncommand = /usr/local/bin/auto-cpufreq --daemon\n"
        )
        self.events = self.root / "commands.log"
        self.openrc = self.root / "etc" / "init.d" / "auto-cpufreq"
        self.dinit = self.root / "etc" / "dinit.d" / "auto-cpufreq"
        for path in (self.openrc, self.dinit):
            path.parent.mkdir(parents=True)

    def managed_file(self, init_name):
        return self.templates / f"auto-cpufreq-{init_name}"

    def target(self, init_name):
        return self.openrc if init_name == "openrc" else self.dinit

    def run_script(self, mode, init_name):
        source = INSTALL if mode == "install" else REMOVE
        code = source.read_text()
        included = (
            ["auto_cpufreq_install", "publish_service_file"]
            if mode == "install" else
            ["auto_cpufreq_remove", "openrc_disable"]
        )
        names = included + ["service_file_is_managed"] if "function service_file_is_managed {" in code else included
        functions = "\n\n".join(extract_function(code, name) for name in names)
        case = code[code.index('case "$(ps h -o comm 1)" in'):]
        case = case.replace(
            "/etc/init.d/auto-cpufreq", str(self.openrc)
        ).replace(
            "/etc/dinit.d/auto-cpufreq", str(self.dinit)
        )
        # The case contains all manager branches but only the requested branch
        # executes. Other init systems are left unchanged.
        prelude = r'''
ps() { printf '%s\n' "$FAKE_INIT"; }
dinitctl() { printf 'dinitctl:%s\n' "$*" >> "$COMMAND_LOG"; return 0; }
rc-service() { printf 'rc-service:%s\n' "$*" >> "$COMMAND_LOG"; return 0; }
rc-update() {
  printf 'rc-update:%s\n' "$*" >> "$COMMAND_LOG"
  [ "$1" = show ] && printf 'auto-cpufreq | default\n'
  return 0
}
'''
        for template in ("auto-cpufreq-openrc", "auto-cpufreq-dinit"):
            pass
        case = case.replace(
            '    echo -e "\\n* Deploying auto-cpufreq openrc unit file"',
            '    echo -e "\\n* Deploying auto-cpufreq openrc unit file"',
        )
        shell = prelude + "\n" + functions + "\n" + case
        return subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "FAKE_INIT": "init" if init_name == "openrc" else "dinit",
                 "COMMAND_LOG": str(self.events), "SHARE_DIR": str(self.share)},
            capture_output=True, text=True,
        )

    def assert_no_commands(self):
        self.assertFalse(self.events.exists() and self.events.read_text().strip())

    def test_new_install_preserves_install_support(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.unlink(missing_ok=True)
                result = self.run_script("install", init_name)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(target.read_bytes(), self.managed_file(init_name).read_bytes())
                self.assertTrue(self.events.exists())

    def test_reinstall_existing_owned_service(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.write_bytes(self.managed_file(init_name).read_bytes())
                result = self.run_script("install", init_name)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(target.read_bytes(), self.managed_file(init_name).read_bytes())

    def test_install_does_not_overwrite_foreign_definition(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.write_text("custom owner service\n")
                result = self.run_script("install", init_name)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_text(), "custom owner service\n")
                self.assert_no_commands()

    def test_install_rejects_symlinks_including_broken(self):
        for init_name in ("openrc", "dinit"):
            for broken in (True, False):
                with self.subTest(init_name=init_name, broken=broken):
                    self.events.unlink(missing_ok=True)
                    target = self.target(init_name)
                    target.unlink(missing_ok=True)
                    outside = self.root / "outside"
                    if not broken:
                        outside.write_text("foreign\n")
                    else:
                        outside.unlink(missing_ok=True)
                    target.symlink_to(outside)
                    result = self.run_script("install", init_name)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(target.is_symlink())
                    if not broken:
                        self.assertEqual(outside.read_text(), "foreign\n")
                    self.assert_no_commands()
                    target.unlink()

    def test_remove_refuses_foreign_definition_before_stopping_service(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.write_text("foreign\n")
                result = self.run_script("remove", init_name)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_text(), "foreign\n")
                self.assert_no_commands()

    def test_remove_owned_definition_succeeds(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.write_bytes(self.managed_file(init_name).read_bytes())
                result = self.run_script("remove", init_name)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertFalse(target.exists())
                self.assertTrue(self.events.exists())

    def test_remove_missing_definition_is_retryable(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.unlink(missing_ok=True)
                result = self.run_script("remove", init_name)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assert_no_commands()

    def test_remove_rejects_owned_filename_symlink(self):
        for init_name in ("openrc", "dinit"):
            with self.subTest(init_name=init_name):
                self.events.unlink(missing_ok=True)
                target = self.target(init_name)
                target.unlink(missing_ok=True)
                outside = self.root / f"foreign-{init_name}"
                outside.write_bytes(self.managed_file(init_name).read_bytes())
                target.symlink_to(outside)
                result = self.run_script("remove", init_name)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(target.is_symlink())
                self.assertEqual(outside.read_bytes(), self.managed_file(init_name).read_bytes())
                self.assert_no_commands()
                target.unlink()


if __name__ == "__main__":
    unittest.main()
