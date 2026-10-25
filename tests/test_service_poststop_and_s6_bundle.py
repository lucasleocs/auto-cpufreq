"""Regressions for s6 bundle ownership and runit post-stop replacement.

The tests execute extracted production shell code in a temporary directory.
They never invoke actual host init managers or modify /etc.
"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

INSTALL = Path("scripts/auto-cpufreq-install.sh")
REMOVE = Path("scripts/auto-cpufreq-remove.sh")


def shell_function(source, name):
    start = source.index(f"function {name} {{")
    return source[start:source.index("\n}", start) + 2]


class S6BundleOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.share = self.root / "share"
        self.template = self.share / "scripts" / "auto-cpufreq-s6"
        self.template.mkdir(parents=True)
        (self.template / "run").write_text("#!/bin/sh\nexec auto-cpufreq --daemon\n")
        (self.template / "type").write_text("longrun\n")
        self.definition = self.root / "etc/s6/sv/auto-cpufreq"
        self.definition.mkdir(parents=True)
        for name in ("run", "type"):
            (self.definition / name).write_bytes((self.template / name).read_bytes())
        self.bundle = self.root / "etc/s6/adminsv/default/contents.d/auto-cpufreq"
        self.bundle.parent.mkdir(parents=True)
        self.commands = self.root / "commands.log"

    def invoke(self, action):
        source = (INSTALL if action in ("preflight", "install") else REMOVE).read_text()
        source = source.replace("/etc/s6/", str(self.root / "etc/s6") + "/")
        functions = "\n\n".join(shell_function(source, name) for name in (
            "directory_contains_only", "s6_service_is_managed",
            "s6_bundle_entry_is_managed"))
        stubs = """
ps() { echo s6-svscan; }
command() { [ "$1" = -v ] && return 0; builtin command "$@"; }
flock() { return 0; }
s6-service() { echo "s6-service:$*" >> "$COMMANDS"; }
s6-db-reload() { echo "s6-db-reload:$*" >> "$COMMANDS"; }
s6-rc() { echo "s6-rc:$*" >> "$COMMANDS"; }
"""
        if action == "preflight":
            shell = stubs + functions + "\n" + shell_function(source, "daemon_install_preflight") + "\ndaemon_install_preflight"
        elif action == "install":
            shell = stubs + functions + "\n" + shell_function(source, "auto_cpufreq_install") + "\n" + source[source.index('case "$(ps h -o comm 1)" in'):]
        else:
            shell = stubs + functions + "\n" + source[source.index('case "$(ps h -o comm 1)" in'):]
        return subprocess.run(
            ["bash", "-c", shell],
            env={**os.environ, "SHARE_DIR": str(self.share), "COMMANDS": str(self.commands)},
            text=True, capture_output=True)

    def assert_no_commands(self):
        self.assertFalse(self.commands.exists() and self.commands.read_text().strip())

    def test_missing_and_empty_membership_are_accepted(self):
        self.assertEqual(self.invoke("preflight").returncode, 0)
        self.bundle.touch()
        self.assertEqual(self.invoke("preflight").returncode, 0)

    def test_symlink_membership_is_rejected_before_install(self):
        external = self.root / "external"
        external.write_text("admin service\n")
        self.bundle.symlink_to(external)
        result = self.invoke("preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.bundle.is_symlink())
        self.assertEqual(external.read_text(), "admin service\n")
        self.assert_no_commands()

    def test_nonempty_membership_is_rejected_before_install(self):
        self.bundle.write_text("admin supplied\n")
        result = self.invoke("preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.bundle.read_text(), "admin supplied\n")
        self.assert_no_commands()

    def test_directory_membership_is_rejected_before_install(self):
        self.bundle.mkdir()
        result = self.invoke("preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.bundle.is_dir())
        self.assert_no_commands()

    def test_install_rechecks_bundle_after_preflight(self):
        self.assertEqual(self.invoke("preflight").returncode, 0)
        external = self.root / "external"
        external.write_text("foreign")
        self.bundle.symlink_to(external)
        result = self.invoke("install")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.bundle.is_symlink())
        self.assertEqual(external.read_text(), "foreign")
        self.assert_no_commands()

    def test_removal_refuses_foreign_symlink_before_service_commands(self):
        external = self.root / "external"
        external.write_text("unchanged")
        self.bundle.symlink_to(external)
        result = self.invoke("remove")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(external.read_text(), "unchanged")
        self.assertTrue(self.definition.is_dir())
        self.assert_no_commands()

    def test_removal_refuses_foreign_nonempty_file(self):
        self.bundle.write_text("host-owned")
        result = self.invoke("remove")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.bundle.read_text(), "host-owned")
        self.assertTrue(self.definition.is_dir())
        self.assert_no_commands()


class RunitPostStopOwnership(unittest.TestCase):
    def test_replace_during_stop_preserves_link_and_foreign_script(self):
        source = REMOVE.read_text()
        start = source.index("    rm_sv() {")
        end = source.index("\n    }\n\n    if [ -f /etc/os-release ]", start) + 6
        rm_sv = source[start:end]
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            service = root / "sv" / "auto-cpufreq"
            service.mkdir(parents=True)
            managed_run = "#!/bin/sh\nexec auto-cpufreq --daemon\n"
            (service / "run").write_text(managed_run)
            templates = root / "share/scripts"
            templates.mkdir(parents=True)
            (templates / "auto-cpufreq-runit").write_text(managed_run)
            active = root / "active/service"
            active.mkdir(parents=True)
            (active / "auto-cpufreq").symlink_to(service)
            shell = "\n".join((
                shell_function(source, "directory_contains_only"),
                shell_function(source, "runit_service_is_managed"),
                """sv() {
    printf '#!/bin/sh\\nexec admin-owned-daemon\\n' > "$ROOT/sv/auto-cpufreq/run"
    return 0
}""",
                rm_sv,
                'rm_sv "$ROOT" "$ROOT/active"',
            ))
            result = subprocess.run(
                ["bash", "-c", shell], capture_output=True, text=True,
                env={**os.environ, "ROOT": str(root), "SHARE_DIR": str(root / "share")})
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((active / "auto-cpufreq").is_symlink())
            self.assertIn("admin-owned-daemon", (service / "run").read_text())


if __name__ == "__main__":
    unittest.main()
