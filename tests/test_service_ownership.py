"""Focused regressions for daemon helper provenance and Runit ownership.

No host services or system installation paths are modified.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from auto_cpufreq.modules import daemon_helper_publish as publishing


ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "scripts/auto-cpufreq-install.sh").read_text()
REMOVE = (ROOT / "scripts/auto-cpufreq-remove.sh").read_text()


def extract(source, start, end):
    return source[source.index(start):source.index(end, source.index(start))]


HELPER = extract(
    INSTALL, "function daemon_helper_is_managed {",
    "function daemon_install_preflight {",
)
INSTALL_VALIDATORS = extract(
    INSTALL, "function directory_contains_only {",
    "function daemon_helper_is_managed {",
)
REMOVE_VALIDATORS = extract(
    REMOVE, "function directory_contains_only {",
    "function openrc_disable {",
)
REMOVE_RUNIT = extract(
    REMOVE, "    rm_sv() {",
    "    if [ -f /etc/os-release ]; then",
)


def bash(script, *args):
    return subprocess.run(
        ["bash", "-c", script, "--", *(str(a) for a in args)],
        capture_output=True, text=True, timeout=10,
    )


LEGACY_SYSTEMD_31 = """[Unit]
Description=auto-cpufreq - Automatic CPU speed & power optimizer for Linux

[Service]
Type=simple
User=root
WorkingDirectory=/opt/auto-cpufreq/venv
Environment=PYTHONPATH=/opt/auto-cpufreq
ExecStart=/opt/auto-cpufreq/venv/bin/python /opt/auto-cpufreq/venv/bin/auto-cpufreq --daemon
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""


class LegacySystemdUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.share = self.root / "share" / "scripts"
        self.share.mkdir(parents=True)
        self.template = self.share / "auto-cpufreq.service"
        shutil.copyfile(ROOT / "scripts/auto-cpufreq.service", self.template)
        self.target = self.root / "auto-cpufreq.service"
        self.target.write_text(LEGACY_SYSTEMD_31)

    def shell(self, code):
        # The fixtures are unprivileged, while real system units belong to root.
        uid_stub = """stat() {
            if [ "$1" = "-c" ] && [ "$2" = "%u" ]; then
              echo 0
              return 0
            fi
            command stat "$@"
        }
        """
        start = extract(
            INSTALL, "function replace_legacy_systemd_unit {",
            "# Dinit reports",
        )
        predicate = extract(
            INSTALL, "function service_file_is_managed {",
            "function directory_contains_only {",
        )
        return bash(
            'SHARE_DIR="$1"\n' + uid_stub + start + predicate
            + '\n' + code + '\n',
            self.share.parent, self.target, self.template,
        )

    def test_v31_unit_has_verified_official_identity(self):
        import hashlib
        data = LEGACY_SYSTEMD_31.encode()
        digest = hashlib.sha1(
            ("blob %d\0" % len(data)).encode() + data
        ).hexdigest()
        self.assertEqual(digest, "196bae318ebf79cf9264ecd4fae1bcca912ab8ae")
        self.assertEqual(
            self.shell('service_file_is_managed "$2" "$3"').returncode, 0,
        )

    def test_legacy_unit_is_migrated_to_current_paths(self):
        result = self.shell('replace_legacy_systemd_unit "$3" "$2"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target.read_bytes(), self.template.read_bytes())
        self.assertEqual(list(self.root.glob(".auto-cpufreq-previous.*")), [])

    def test_custom_unit_is_preserved(self):
        self.target.write_text(LEGACY_SYSTEMD_31 + "# administrator edit\n")
        result = self.shell('replace_legacy_systemd_unit "$3" "$2"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("# administrator edit", self.target.read_text())


class DinitEnabledDependencyTest(unittest.TestCase):
    def test_documented_system_service_directories_are_considered(self):
        for path in ("/etc/dinit.d/boot", "/run/dinit.d/boot",
                     "/usr/local/lib/dinit.d/boot", "/lib/dinit.d/boot",
                     "/usr/lib/dinit.d/boot"):
            with self.subTest(path=path):
                self.assertIn(path, INSTALL)

    def test_existing_waits_for_link_must_match_managed_service(self):
        original = extract(
            INSTALL, "function dinit_enable_link_is_managed {",
            "function dinit_enable {",
        )
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            service = base / "auto-cpufreq"
            service.write_text("type = process\ncommand = /bin/true\n")
            boot = base / "boot"
            boot.write_text("type = internal\nwaits-for.d: boot.d\n")
            links = base / "boot.d"
            links.mkdir()
            link = links / "auto-cpufreq"
            link.symlink_to(service)
            source = original.replace(
                "service_file=/etc/dinit.d/auto-cpufreq",
                'service_file="' + str(service) + '"',
            )
            begin = source.index("for candidate in ")
            end = source.index("; do", begin)
            source = source[:begin] + (
                'for candidate in "' + str(boot) + '"'
            ) + source[end:]
            command = source + "\ndinit_enable_link_is_managed\n"
            self.assertEqual(bash(command).returncode, 0)
            link.unlink()
            foreign = base / "other-service"
            foreign.write_text("type = internal\n")
            link.symlink_to(foreign)
            self.assertNotEqual(bash(command).returncode, 0)


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.templates = self.root / "share" / "scripts"
        self.templates.mkdir(parents=True)
        for name in ("install", "remove"):
            shutil.copyfile(
                ROOT / ("scripts/auto-cpufreq-" + name + ".sh"),
                self.templates / ("auto-cpufreq-" + name + ".sh"),
            )

    def validate(self, target, kind):
        # Fake only the UID check, because CI builds are unprivileged.
        stub = """stat() {
            if [ "$1" = "-c" ] && [ "$2" = "%u" ]; then
              echo 0
              return 0
            fi
            command stat "$@"
        }
        """
        return bash(
            'SHARE_DIR="$1"\n' + stub + HELPER +
            '\ndaemon_helper_is_managed "$2" "$3"\n',
            self.templates.parent, target, kind,
        )

    def test_exact_installed_templates_are_recognized(self):
        for kind in ("install", "remove"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.validate(
                        self.templates / ("auto-cpufreq-" + kind + ".sh"),
                        kind,
                    ).returncode, 0,
                )

    def test_modified_helper_with_original_header_is_rejected(self):
        for kind in ("install", "remove"):
            with self.subTest(kind=kind):
                target = self.root / (kind + "-helper")
                target.write_text(
                    (self.templates / ("auto-cpufreq-" + kind + ".sh")).read_text()
                    + "\necho 'malicious addition'\n"
                )
                self.assertNotEqual(self.validate(target, kind).returncode, 0)

    def test_official_release_blobs_are_allowlisted(self):
        # Release identities verified against official upstream tags. The
        # regression suite must not rely on tags in the contributor's fork.
        history = (
            ("install", "ed088b544b5d4e36a4ae45105e9f16d32dd12ac0"),
            ("install", "1e962aadb949cda33f0f1a26f3af036821150b65"),
            ("install", "a19232084061bf01d04a560a739b401b612bd33d"),
            ("install", "119f51541c582fe9b9834de3d72b62c14ee209e4"),
            ("install", "b85811b6e97d3257bfa56e558a0df40bbcbc3a08"),
            ("install", "1bae02ede93501b283061fc977ae40886134dfb4"),
            ("remove", "3004e13b49c0dc71f5c91a253280a0467134a2c0"),
            ("remove", "66636a1ee289d24c51fb2b0298791e3e37862286"),
            ("remove", "659e7ab2d3a4526a51b6d11af1d951644c62c3a0"),
            ("remove", "293fa48be0fd6b52e82f6ff528bdaff1c5cb986e"),
            ("remove", "73dfc1cf8d32ea170f2712fbf23fbc9a652b3812"),
        )
        core = (ROOT / "auto_cpufreq/core.py").read_text()
        for kind, digest in history:
            with self.subTest(kind=kind, digest=digest):
                self.assertIn(digest, core)
                self.assertIn(digest, INSTALL)

    def test_git_blob_identity_matches_python_and_shell(self):
        import hashlib
        data = b"#!/bin/sh\necho official legacy script\n"
        digest = hashlib.sha1(
            ("blob %d\0" % len(data)).encode("ascii") + data
        ).hexdigest()
        target = self.root / "synthetic-legacy-helper"
        target.write_bytes(data)
        template = self.templates / "auto-cpufreq-install.sh"
        with mock.patch.object(publishing, "_root_owned_regular", return_value=True):
            self.assertTrue(publishing._owned_helper(target, template, (digest,)))
            self.assertFalse(publishing._owned_helper(
                target, template, ("0000000000000000000000000000000000000000",)
            ))
        # Inject just the synthetic digest into the allowlist to exercise
        # the exact shell validation algorithm without a Git checkout.
        managed = HELPER.replace('known_blobs="', 'known_blobs="' + digest + ' ')
        stub = """stat() {
            if [ "$1" = "-c" ] && [ "$2" = "%u" ]; then
              echo 0
              return 0
            fi
            command stat "$@"
        }
        """
        def run_check():
            return bash(
                'SHARE_DIR="$1"\n' + stub + managed
                + '\ndaemon_helper_is_managed "$2" install\n',
                self.templates.parent, target,
            )
        self.assertEqual(run_check().returncode, 0)
        target.write_bytes(data + b"\necho extra\n")
        self.assertNotEqual(run_check().returncode, 0)



class AtomicHelperPublishTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.template = self.root / "install-script"
        self.target = self.root / "auto-cpufreq-install"
        self.template.write_bytes(b"new trusted helper")

    def publish(self):
        with mock.patch.object(publishing, "_root_owned_regular", return_value=True):
            publishing.publish_daemon_helper(
                self.template, self.target, "deadbeef",
            )

    def test_identical_helper_does_not_require_rename_exchange(self):
        self.target.write_bytes(b"new trusted helper")
        self.target.chmod(0o755)
        with mock.patch.object(
            publishing, "_exchange",
            side_effect=OSError("RENAME_EXCHANGE unavailable"),
        ):
            self.publish()
        self.assertEqual(self.target.read_bytes(), b"new trusted helper")

    def test_unsupported_exchange_preserves_existing_helper(self):
        import errno
        self.target.write_bytes(b"old legitimate helper")
        self.target.chmod(0o755)
        old_blob = publishing.hashlib.sha1(
            b"blob 21\0old legitimate helper"
        ).hexdigest()
        with mock.patch.object(
            publishing, "_root_owned_regular", return_value=True
        ), mock.patch.object(
            publishing, "_exchange",
            side_effect=OSError(errno.EOPNOTSUPP, "Not supported"),
        ):
            with self.assertRaises(OSError):
                publishing.publish_daemon_helper(
                    self.template, self.target, (old_blob,),
                )
        self.assertEqual(self.target.read_bytes(), b"old legitimate helper")
        self.assertEqual(
            list(self.root.glob(".auto-cpufreq-install.*")), [],
        )

    def test_first_install_refuses_to_clobber_racing_file(self):
        original = publishing.os.link

        def race(stage, path):
            Path(path).write_bytes(b"foreign helper")
            return original(stage, path)

        with mock.patch.object(publishing.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                self.publish()
        self.assertEqual(self.target.read_bytes(), b"foreign helper")

    def test_install_into_empty_path(self):
        self.publish()
        self.assertEqual(self.target.read_bytes(), b"new trusted helper")
        self.assertTrue(self.target.stat().st_mode & 0o111)

    def test_replaces_identical_known_helper(self):
        self.target.write_bytes(b"new trusted helper")
        self.publish()
        self.assertEqual(self.target.read_bytes(), b"new trusted helper")

    def test_refuses_foreign_existing_helper(self):
        self.target.write_bytes(b"foreign helper")
        with self.assertRaises(PermissionError):
            self.publish()
        self.assertEqual(self.target.read_bytes(), b"foreign helper")

    def test_rolls_back_foreign_file_swapped_after_preflight(self):
        self.target.write_bytes(b"new trusted helper")
        exchange = publishing._exchange
        swapped = []

        def race(stage, path):
            if not swapped:
                swapped.append(True)
                Path(path).write_bytes(b"foreign concurrent edit")
            return exchange(stage, path)

        with mock.patch.object(publishing, "_exchange", side_effect=race):
            with self.assertRaises(PermissionError):
                self.publish()
        self.assertEqual(self.target.read_bytes(), b"foreign concurrent edit")
        self.assertEqual(
            list(self.root.glob(".auto-cpufreq-install.*")), [],
        )


class RunitDestinationTest(unittest.TestCase):
    def test_only_service_specific_run_paths_are_accepted(self):
        allowed = (
            "/run/auto-cpufreq",
            "/run/runit/supervise/auto-cpufreq",
            "/run/sv.auto-cpufreq",
            "/run/auto-cpufreq-supervise",
            "/run/team/auto-cpufreq/supervise",
        )
        refused = (
            "/tmp/auto-cpufreq",
            "/etc/auto-cpufreq",
            "/run/sv.other-auto-cpufreq",
            "/run/auto-cpufreq-other/supervise",
            "/run/custom-runit-supervisor",
            "/run/other-service",
        )
        for source in (INSTALL, REMOVE):
            predicate = extract(
                source,
                "function runit_supervise_target_is_managed {",
                "function runit_supervise_is_managed {",
            )
            for path in allowed:
                with self.subTest(path=path, expected=True):
                    result = bash(
                        predicate + '\nrunit_supervise_target_is_managed "$1"\n',
                        path,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
            for path in refused:
                with self.subTest(path=path, expected=False):
                    result = bash(
                        predicate + '\nrunit_supervise_target_is_managed "$1"\n',
                        path,
                    )
                    self.assertNotEqual(result.returncode, 0)



class RunitSuperviseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.share = self.root / "share" / "scripts"
        self.share.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "scripts/auto-cpufreq-runit",
            self.share / "auto-cpufreq-runit",
        )
        self.etc = self.root / "etc"
        self.var = self.root / "var"
        self.service = self.etc / "sv" / "auto-cpufreq"
        self.link = self.var / "service" / "auto-cpufreq"
        self.service.mkdir(parents=True)
        self.link.parent.mkdir(parents=True)
        shutil.copyfile(self.share / "auto-cpufreq-runit",
                        self.service / "run")
        self.link.symlink_to(self.service)

    def remove(self):
        script = (
            'SHARE_DIR="$1"\n'
            + REMOVE_VALIDATORS + "\n" + REMOVE_RUNIT
            + '\nsv() { return 0; }\nrm_sv "$2" "$3"\n'
        )
        return bash(script, self.share.parent, self.etc, self.var)

    def test_known_runit_files_can_be_removed(self):
        supervise = self.service / "supervise"
        supervise.mkdir()
        (supervise / "status").write_bytes(b"status")
        (supervise / "lock").write_bytes(b"")
        os.mkfifo(supervise / "ok")
        os.mkfifo(supervise / "control")
        result = self.remove()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.service.exists())
        self.assertFalse(self.link.is_symlink())

    def test_unexpected_supervise_entries_are_preserved(self):
        supervise = self.service / "supervise"
        supervise.mkdir()
        (supervise / "administrator-notes").write_text("preserve")
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((supervise / "administrator-notes").exists())
        self.assertTrue((self.service / "run").exists())
        self.assertTrue(self.link.is_symlink())

    def test_untrusted_supervise_symlink_is_not_followed(self):
        other = self.root / "other-service"
        other.mkdir()
        (other / "status").write_text("preserve")
        (self.service / "supervise").symlink_to(other)
        result = self.remove()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((other / "status").exists())
        self.assertTrue((self.service / "supervise").is_symlink())


if __name__ == "__main__":
    unittest.main()
