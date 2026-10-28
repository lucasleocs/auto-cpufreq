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

    def test_verified_release_history_is_recognized(self):
        # checkout@v4 with fetch-depth: 0 includes the official release tags.
        # Exercise both the shell preflight and the Python publication guard.
        history = (
            ("v3.1.0", "install", "1e962aadb949cda33f0f1a26f3af036821150b65"),
            ("v3.1.0", "remove", "66636a1ee289d24c51fb2b0298791e3e37862286"),
            ("v2.3.0", "install", "a19232084061bf01d04a560a739b401b612bd33d"),
            ("v2.3.0", "remove", "659e7ab2d3a4526a51b6d11af1d951644c62c3a0"),
            ("v1.9.9", "install", "119f51541c582fe9b9834de3d72b62c14ee209e4"),
            ("v1.9.9", "remove", "659e7ab2d3a4526a51b6d11af1d951644c62c3a0"),
            ("v1.8.2", "install", "1bae02ede93501b283061fc977ae40886134dfb4"),
            ("v1.8.2", "remove", "293fa48be0fd6b52e82f6ff528bdaff1c5cb986e"),
        )
        core = (ROOT / "auto_cpufreq/core.py").read_text()
        for tag, kind, digest in history:
            with self.subTest(tag=tag, kind=kind):
                data = subprocess.run(
                    ["git", "show", tag + ":scripts/auto-cpufreq-" + kind + ".sh"],
                    cwd=ROOT, capture_output=True, check=True,
                ).stdout
                target = self.root / (kind + "-" + tag)
                target.write_bytes(data)
                self.assertEqual(self.validate(target, kind).returncode, 0)
                self.assertIn(digest, core)
                with mock.patch.object(
                    publishing, "_root_owned_regular", return_value=True
                ):
                    self.assertTrue(publishing._owned_helper(
                        target,
                        self.templates / ("auto-cpufreq-" + kind + ".sh"),
                        (digest,),
                    ))
                target.write_bytes(data + b"\necho changed\n")
                with mock.patch.object(
                    publishing, "_root_owned_regular", return_value=True
                ):
                    self.assertFalse(publishing._owned_helper(
                        target,
                        self.templates / ("auto-cpufreq-" + kind + ".sh"),
                        (digest,),
                    ))

    def test_verified_previous_generation_is_recognized(self):
        # The Git blob identity is from upstream before the review branch.
        for kind in ("install", "remove"):
            with self.subTest(kind=kind):
                original = subprocess.run(
                    ["git", "show",
                     "fe7b13baeca002b368b1261bcc681ca9995055cf:"
                     "scripts/auto-cpufreq-" + kind + ".sh"],
                    cwd=ROOT, capture_output=True, check=True,
                ).stdout
                target = self.root / (kind + "-previous")
                target.write_bytes(original)
                self.assertEqual(self.validate(target, kind).returncode, 0)



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
