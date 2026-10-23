"""Static cross-file contract checks for the *real* upstream updater layout.

These do not replace running the full updater in an isolated VM. They
prevent source installer changes from silently invalidating RAPL cleanup.
No sudo, no installation, no systemctl calls and no real Powercap writes.
"""
from pathlib import Path
import re
import os
import subprocess
import unittest
from tempfile import TemporaryDirectory

from auto_cpufreq.modules.intel_rapl import DEFAULT_RAPL_STATE_PATH


ROOT = Path(__file__).resolve().parent.parent


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def unit_directive(name):
    matches = re.findall(
        r"^" + re.escape(name) + r"=(.+)$",
        read("scripts/auto-cpufreq.service"),
        re.MULTILINE,
    )
    if len(matches) != 1:
        raise AssertionError(f"Expected exactly one {name} directive")
    return matches[0]


class SourceUpdaterRaplContractTests(unittest.TestCase):
    def test_recovery_state_is_private_and_does_not_share_installer_lock(self):
        installer = read("auto-cpufreq-installer")
        match = re.search(r'^OPERATION_LOCK_DIR="([^"]+)"$', installer, re.M)
        self.assertIsNotNone(match)
        installer_lock = Path(match.group(1))
        self.assertEqual(installer_lock, Path("/run/auto-cpufreq"))
        self.assertIn('chmod 755 "$OPERATION_LOCK_DIR"', installer)

        runtime = unit_directive("RuntimeDirectory")
        self.assertEqual(runtime, "auto-cpufreq-rapl")
        self.assertEqual(DEFAULT_RAPL_STATE_PATH.parent, Path("/run") / runtime)
        self.assertNotEqual(installer_lock, DEFAULT_RAPL_STATE_PATH.parent)
        self.assertEqual(unit_directive("RuntimeDirectoryMode"), "0700")
        self.assertEqual(unit_directive("RuntimeDirectoryPreserve"), "yes")

    def test_existing_updater_stops_daemon_before_switching_release(self):
        updater = read("auto_cpufreq/bin/auto_cpufreq.py")
        start = updater.index("        elif update:")
        end = updater.index("        elif remove:", start)
        update = updater[start:end]
        self.assertLess(update.index("remove_daemon()"),
                        update.index("new_update(custom_dir, target_tag)"))
        self.assertLess(update.index("new_update(custom_dir, target_tag)"),
                        update.index('"--install"'))
        self.assertIn("if not new_update(custom_dir, target_tag):", update)
        self.assertIn("daemon_was_installed", update)

    def test_managed_unit_will_match_its_release_on_removal(self):
        remover = read("scripts/auto-cpufreq-remove.sh")
        self.assertIn('cmp -s -- "$managed_systemd_unit" "$systemd_unit"',
                      remover)
        self.assertLess(
            remover.index("systemctl stop auto-cpufreq"),
            remover.index("systemctl disable auto-cpufreq"),
        )
        self.assertIn("ExecStopPost=-/opt/auto-cpufreq/current/venv/bin/"
                      "auto-cpufreq-restore-rapl",
                      read("scripts/auto-cpufreq.service"))

    def test_removal_requires_successful_rerun_of_rapl_restore(self):
        source = read("scripts/auto-cpufreq-remove.sh")
        start = source.index("  systemd)")
        end = source.index("  s6-svscan)", start)
        systemd = source[start:end]
        stop = systemd.index("systemctl stop auto-cpufreq")
        check = systemd.index('|| ! "$rapl_restore_helper"')
        disable = systemd.index("systemctl disable auto-cpufreq")
        remove_unit = systemd.index('rm -f -- "$systemd_unit"')
        self.assertLess(stop, check)
        self.assertLess(check, disable)
        self.assertLess(disable, remove_unit)
        self.assertIn(
            "refusing to remove the recovery helper", systemd
        )
        self.assertIn("exit 1", systemd[check:disable])

    def test_real_remove_systemd_branch_blocks_unresolved_journal(self):
        """Execute the verbatim systemd removal branch with fake paths only.

        This does not execute a source installer or touch system unit files:
        the extracted shell case uses an isolated temporary managed unit,
        fake systemctl and restore helper.
        """
        source = read("scripts/auto-cpufreq-remove.sh")
        start = source.index("  systemd)")
        end = source.index("  s6-svscan)", start)
        branch = source[start:end].replace(
            "systemd_unit=/etc/systemd/system/auto-cpufreq.service",
            'systemd_unit="$MOCK_UNIT"',
        ).replace(
            "rapl_restore_helper=/opt/auto-cpufreq/current/venv/bin/"
            "auto-cpufreq-restore-rapl",
            'rapl_restore_helper="$MOCK_HELPER"',
        )
        self.assertIn('systemd_unit="$MOCK_UNIT"', branch)
        self.assertIn('rapl_restore_helper="$MOCK_HELPER"', branch)

        bash = (
            '#!/bin/bash\n'
            'SHARE_DIR="$MOCK_SHARE"\n'
            'systemctl() { printf "%s\\n" "$*" >> "$MOCK_EVENTS"; }\n'
            'case "systemd" in\n' + branch + 'esac\n'
        )

        with TemporaryDirectory() as td:
            root = Path(td)
            share = root / "share"
            (share / "scripts").mkdir(parents=True)
            packaged = share / "scripts" / "auto-cpufreq.service"
            installed = root / "installed-auto-cpufreq.service"
            helper = root / "fake-restore"
            events = root / "systemctl.log"
            packaged.write_text(read("scripts/auto-cpufreq.service"))
            installed.write_text(packaged.read_text())
            helper.write_text("#!/bin/sh\nexit 1\n")
            helper.chmod(0o700)

            env = {
                **os.environ, "MOCK_SHARE": str(share),
                "MOCK_UNIT": str(installed), "MOCK_HELPER": str(helper),
                "MOCK_EVENTS": str(events),
            }
            failed = subprocess.run(
                ["bash", "-c", bash], text=True, capture_output=True, env=env
            )
            self.assertNotEqual(failed.returncode, 0, failed.stdout)
            self.assertTrue(installed.exists(), "Unit must survive failed restore")
            self.assertEqual(events.read_text().splitlines(),
                             ["stop auto-cpufreq"])

            helper.write_text("#!/bin/sh\nexit 0\n")
            succeeded = subprocess.run(
                ["bash", "-c", bash], text=True, capture_output=True, env=env
            )
            self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
            self.assertFalse(installed.exists())
            self.assertEqual(events.read_text().splitlines(), [
                "stop auto-cpufreq",
                "stop auto-cpufreq",
                "disable auto-cpufreq",
                "daemon-reload",
            ])

    def test_helper_is_packaged_in_source_venv_and_release_is_atomic(self):
        scripts = read("pyproject.toml")
        self.assertIn('auto-cpufreq-restore-rapl = '
                      '"auto_cpufreq.bin.auto_cpufreq_restore_rapl:main"',
                      scripts)
        installer = read("auto-cpufreq-installer")
        self.assertIn("function release_is_complete", installer)
        self.assertIn('mv -Tf "$temporary_current" "$CURRENT_PATH"', installer)
        self.assertIn('\"$release_dir/venv/bin/auto-cpufreq\"', installer)
        self.assertEqual(
            unit_directive("ExecStopPost"),
            "-/opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl",
        )


if __name__ == "__main__":
    unittest.main()
