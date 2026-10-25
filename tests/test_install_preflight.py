"""Read-only shell preflight tests, with all system service paths sandboxed."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

INSTALL = Path("scripts/auto-cpufreq-install.sh")


def fun(source, name):
    start = source.index(f"function {name} {{")
    return source[start:source.index("\n}", start) + 2]


class PreflightServiceOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.share = self.root / "share" / "scripts"
        self.share.mkdir(parents=True)
        self.destinations = {
            "dinit": self.root / "etc/dinit.d/auto-cpufreq",
            "init": self.root / "etc/init.d/auto-cpufreq",
            "systemd": self.root / "etc/systemd/system/auto-cpufreq.service",
        }
        self.templates = {
            "dinit": self.share / "auto-cpufreq-dinit",
            "init": self.share / "auto-cpufreq-openrc",
            "systemd": self.share / "auto-cpufreq.service",
        }
        for target in self.destinations.values():
            target.parent.mkdir(parents=True, exist_ok=True)
        for template in self.templates.values():
            template.write_text("managed service contents\n")

    def preflight(self, manager):
        source = INSTALL.read_text()
        f = fun(source, "daemon_install_preflight")
        for actual, substitute in [
            ("/etc/dinit.d/auto-cpufreq", self.destinations["dinit"]),
            ("/etc/init.d/auto-cpufreq", self.destinations["init"]),
            ("/etc/systemd/system/auto-cpufreq.service", self.destinations["systemd"]),
        ]:
            f = f.replace(actual, str(substitute))
        shell = "\n".join([
            fun(source, "service_file_is_managed"),
            "ps() { printf '%s\\n' \"$FAKE_INIT\"; }",
            f,
            "daemon_install_preflight",
        ])
        return subprocess.run(["bash", "-c", shell], capture_output=True, text=True,
                              env={**os.environ, "FAKE_INIT": manager,
                                   "SHARE_DIR": str(self.share.parent)})

    def test_new_service_preflight_is_allowed(self):
        for manager in self.destinations:
            with self.subTest(manager=manager):
                self.assertEqual(self.preflight(manager).returncode, 0)

    def test_existing_owned_service_preflight_is_allowed(self):
        for manager, target in self.destinations.items():
            with self.subTest(manager=manager):
                target.write_bytes(self.templates[manager].read_bytes())
                self.assertEqual(self.preflight(manager).returncode, 0)

    def test_unowned_service_preflight_rejected_before_mutation(self):
        for manager, target in self.destinations.items():
            with self.subTest(manager=manager):
                target.write_text("foreign service\n")
                result = self.preflight(manager)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_text(), "foreign service\n")
                target.unlink()

    def test_service_symlink_preflight_rejected(self):
        for manager, target in self.destinations.items():
            with self.subTest(manager=manager):
                outside = self.root / ("foreign-" + manager)
                outside.write_bytes(self.templates[manager].read_bytes())
                target.symlink_to(outside)
                self.assertNotEqual(self.preflight(manager).returncode, 0)
                self.assertEqual(outside.read_bytes(), self.templates[manager].read_bytes())
                target.unlink()

    def test_missing_template_preflight_rejected(self):
        for manager in self.destinations:
            with self.subTest(manager=manager):
                self.templates[manager].unlink()
                try:
                    self.assertNotEqual(self.preflight(manager).returncode, 0)
                finally:
                    self.templates[manager].write_text("managed service contents\n")

    def test_unsupported_init_rejected(self):
        self.assertNotEqual(self.preflight("unknown-init").returncode, 0)


if __name__ == "__main__":
    unittest.main()
