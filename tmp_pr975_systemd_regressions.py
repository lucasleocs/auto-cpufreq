import subprocess
import json
import ast
import importlib
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class SystemdQueryTests(unittest.TestCase):
    def test_disabled_is_a_valid_unit_file_state_despite_nonzero_status(self):
        from auto_cpufreq.systemd import query_unit_file_state

        result = subprocess.CompletedProcess([], 1, "disabled\n", "")
        with patch("auto_cpufreq.systemd.run", return_value=result):
            self.assertEqual(query_unit_file_state("example.service"), "disabled")

    def test_not_found_is_a_valid_absent_state(self):
        from auto_cpufreq.systemd import query_unit_file_state

        result = subprocess.CompletedProcess([], 4, "not-found\n", "")
        with patch("auto_cpufreq.systemd.run", return_value=result):
            self.assertEqual(query_unit_file_state("example.service"), "not-found")

    def test_empty_nonzero_response_is_not_treated_as_absence(self):
        from auto_cpufreq.systemd import SystemdQueryError, query_unit_file_state

        result = subprocess.CompletedProcess([], 1, "", "Failed to connect")
        with patch("auto_cpufreq.systemd.run", return_value=result):
            with self.assertRaises(SystemdQueryError):
                query_unit_file_state("example.service")

    def test_load_state_not_found_is_accepted_even_when_show_returns_nonzero(self):
        from auto_cpufreq.systemd import query_unit_properties

        result = subprocess.CompletedProcess(
            [],
            1,
            "LoadState=not-found\nActiveState=inactive\n",
            "",
        )
        with patch("auto_cpufreq.systemd.run", return_value=result):
            self.assertIsNone(
                query_unit_properties(
                    "example.service",
                    ("LoadState", "ActiveState"),
                )
            )


class DaemonPreflightTests(unittest.TestCase):
    def test_systemd_not_found_does_not_block_daemon_installation(self):
        import auto_cpufreq.daemon_preflight as preflight

        with (
            patch.object(preflight, "_init_name", return_value="systemd"),
            patch.object(preflight, "first_existing_path", return_value=None),
            patch.object(
                preflight,
                "query_unit_file_state",
                return_value="not-found",
            ),
        ):
            self.assertIsNone(preflight.daemon_service_conflict())

    def test_disabled_systemd_unit_remains_a_conflict(self):
        import auto_cpufreq.daemon_preflight as preflight

        with (
            patch.object(preflight, "_init_name", return_value="systemd"),
            patch.object(preflight, "first_existing_path", return_value=None),
            patch.object(
                preflight,
                "query_unit_file_state",
                return_value="disabled",
            ),
        ):
            self.assertEqual(
                preflight.daemon_service_conflict(),
                "systemd unit auto-cpufreq.service",
            )

    def test_old_missing_unit_fallback_requires_a_reachable_manager(self):
        from auto_cpufreq.systemd import SystemdQueryError, query_unit_properties

        show_missing = subprocess.CompletedProcess([], 1, "", "not found")
        unit_not_found = subprocess.CompletedProcess([], 4, "not-found\n", "")
        manager_failure = subprocess.CompletedProcess([], 1, "", "Failed to connect")
        with patch(
            "auto_cpufreq.systemd.run",
            side_effect=(show_missing, unit_not_found, manager_failure),
        ):
            with self.assertRaises(SystemdQueryError):
                query_unit_properties(
                    "example.service",
                    ("LoadState", "ActiveState"),
                )


class PowerServiceLifecycleTests(unittest.TestCase):
    def test_disable_masks_without_removing_enablement_links(self):
        click = types.ModuleType("click")
        click.command = lambda: lambda function: function
        click.option = lambda *args, **kwargs: lambda function: function
        click.ClickException = RuntimeError
        core = types.ModuleType("auto_cpufreq.core")
        globals_module = types.ModuleType("auto_cpufreq.globals")
        globals_module.GITHUB = "https://example.invalid"
        globals_module.IS_INSTALLED_WITH_SNAP = False
        parser = types.ModuleType("auto_cpufreq.tlp_stat_parser")
        parser.TLPStatusParser = object
        with patch.dict(
            sys.modules,
            {
                "click": click,
                "auto_cpufreq.core": core,
                "auto_cpufreq.globals": globals_module,
                "auto_cpufreq.tlp_stat_parser": parser,
            },
        ):
            sys.modules.pop("auto_cpufreq.power_helper", None)
            power_helper = importlib.import_module("auto_cpufreq.power_helper")

        with (
            patch.object(
                power_helper,
                "_systemd_unit_state",
                return_value=("loaded", "active"),
            ),
            patch.object(
                power_helper,
                "_run_required_power_command",
                return_value=True,
            ) as command,
        ):
            self.assertTrue(
                power_helper._disable_systemd_power_service(
                    "example.service",
                    "example service",
                )
            )

        command.assert_called_once_with(
            ["systemctl", "mask", "--now", "example.service"],
            "stop and mask example service",
        )

    def test_preserved_enablement_restore_does_not_call_enable_or_disable(self):
        import auto_cpufreq.power_state as power_state

        original = {
            "load_state": "loaded",
            "active_state": "active",
            "unit_file_state": "enabled",
            "fragment_path": "/usr/lib/systemd/system/example.service",
        }
        with patch.object(power_state, "_systemctl", return_value=True) as command:
            self.assertTrue(
                power_state.restore_service_state(
                    "example.service",
                    original,
                    enablement_links_preserved=True,
                )
            )

        actions = [call.args[1] for call in command.call_args_list]
        self.assertEqual(actions, ["unmask", "start"])

    def test_version_one_snapshot_without_transaction_restores_bluetooth(self):
        import auto_cpufreq.power_state as power_state

        snapshot = {
            "version": 1,
            "services": {},
            "power_profiles_profile": None,
            "bluetooth": {
                "config_present": False,
                "policy_present": False,
                "auto_enable_lines": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory)
            state_dir.chmod(0o700)
            state_file = state_dir / power_state.STATE_FILE_NAME
            state_file.write_text(json.dumps(snapshot))
            state_file.chmod(0o600)
            with (
                patch.object(
                    power_state,
                    "_restore_bluetooth_state",
                    return_value=True,
                ) as restore_bluetooth,
                patch.object(
                    power_state,
                    "_restore_power_profiles_profile",
                    return_value=True,
                ),
            ):
                self.assertTrue(power_state.restore_power_state(state_dir=state_dir))

        restore_bluetooth.assert_called_once()


class RecoveryStateStorageTests(unittest.TestCase):
    transaction = {
        "bluetooth_managed": False,
        "cpufreqctl_preexisting": False,
        "power_service_enablement_preserved": True,
    }

    def test_snapshot_publication_syncs_file_and_directory(self):
        import auto_cpufreq.power_state as power_state

        synced_types = []

        def record_fsync(descriptor):
            synced_types.append(stat.S_IFMT(os.fstat(descriptor).st_mode))

        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "state"
            with patch.object(power_state.os, "fsync", side_effect=record_fsync):
                self.assertTrue(
                    power_state.save_power_state(
                        state_dir=state_dir,
                        bluetooth_config=Path(directory) / "missing.conf",
                        init_comm=Path(directory) / "missing-init",
                        transaction=self.transaction,
                    )
                )

        self.assertIn(stat.S_IFREG, synced_types)
        self.assertIn(stat.S_IFDIR, synced_types)

    def test_version_two_writer_requires_complete_transaction_metadata(self):
        import auto_cpufreq.power_state as power_state

        incomplete = dict(self.transaction)
        incomplete.pop("power_service_enablement_preserved")
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(
                power_state.save_power_state(
                    state_dir=Path(directory) / "state",
                    bluetooth_config=Path(directory) / "missing.conf",
                    init_comm=Path(directory) / "missing-init",
                    transaction=incomplete,
                )
            )

    def test_first_snapshot_wins_repeated_creation(self):
        import auto_cpufreq.power_state as power_state

        with tempfile.TemporaryDirectory() as directory:
            state_dir = Path(directory) / "state"
            arguments = {
                "state_dir": state_dir,
                "bluetooth_config": Path(directory) / "missing.conf",
                "init_comm": Path(directory) / "missing-init",
                "transaction": self.transaction,
            }
            self.assertTrue(power_state.save_power_state(**arguments))
            original = (state_dir / power_state.STATE_FILE_NAME).read_bytes()
            self.assertFalse(power_state.save_power_state(**arguments))
            self.assertEqual(
                (state_dir / power_state.STATE_FILE_NAME).read_bytes(),
                original,
            )

    def test_concurrent_snapshot_publication_has_exactly_one_winner(self):
        import auto_cpufreq.power_state as power_state

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_dir = base / "state"
            barrier = threading.Barrier(2)
            real_link = os.link
            results = []

            def synchronized_link(*args, **kwargs):
                barrier.wait(timeout=5)
                return real_link(*args, **kwargs)

            def save():
                results.append(
                    power_state.save_power_state(
                        state_dir=state_dir,
                        bluetooth_config=base / "missing.conf",
                        init_comm=base / "missing-init",
                        transaction=self.transaction,
                    )
                )

            with (
                patch.object(power_state, "power_state_exists", return_value=False),
                patch.object(power_state.os, "link", side_effect=synchronized_link),
            ):
                threads = [threading.Thread(target=save) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)

            self.assertEqual(sorted(results), [False, True])

    def test_snapshot_creation_rejects_a_symlinked_state_directory(self):
        import auto_cpufreq.power_state as power_state

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real_directory = base / "real"
            real_directory.mkdir(mode=0o700)
            state_dir = base / "state"
            state_dir.symlink_to(real_directory, target_is_directory=True)

            self.assertFalse(
                power_state.save_power_state(
                    state_dir=state_dir,
                    bluetooth_config=base / "missing.conf",
                    init_comm=base / "missing-init",
                    transaction=self.transaction,
                )
            )
            self.assertFalse(
                (real_directory / power_state.STATE_FILE_NAME).exists()
            )

    def test_restore_rejects_a_symlinked_snapshot_file(self):
        import auto_cpufreq.power_state as power_state

        snapshot = {
            "version": 1,
            "services": {},
            "power_profiles_profile": None,
            "bluetooth": {
                "config_present": False,
                "policy_present": False,
                "auto_enable_lines": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            state_dir = base / "state"
            state_dir.mkdir(mode=0o700)
            external = base / "external.json"
            external.write_text(json.dumps(snapshot))
            external.chmod(0o600)
            (state_dir / power_state.STATE_FILE_NAME).symlink_to(external)

            self.assertFalse(power_state.restore_power_state(state_dir=state_dir))
            self.assertTrue(external.exists())


class OperationLockTests(unittest.TestCase):
    def test_competing_python_process_cannot_acquire_lock(self):
        from auto_cpufreq.operation_lock import operation_lock

        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "operation.lock"
            with operation_lock(lock_path):
                environment = os.environ.copy()
                environment.pop("AUTO_CPUFREQ_OPERATION_LOCK_FD", None)
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; "
                        "from auto_cpufreq.operation_lock import operation_lock, OperationLockError; "
                        "import sys; "
                        "\ntry:\n with operation_lock(Path(sys.argv[1])): pass"
                        "\nexcept OperationLockError:\n raise SystemExit(9)",
                        str(lock_path),
                    ],
                    env=environment,
                )

            self.assertEqual(result.returncode, 9)

    def test_inherited_python_descriptor_reuses_same_lock(self):
        from auto_cpufreq.operation_lock import (
            INHERITED_LOCK_FD_ENV,
            operation_lock,
        )

        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "operation.lock"
            with operation_lock(lock_path) as outer:
                with patch.dict(
                    os.environ,
                    {INHERITED_LOCK_FD_ENV: str(outer.fileno())},
                ):
                    with operation_lock(lock_path) as inherited:
                        self.assertEqual(
                            os.fstat(inherited.fileno()).st_ino,
                            os.fstat(outer.fileno()).st_ino,
                        )

    def test_python_lock_rejects_symlink_without_chmodding_target(self):
        from auto_cpufreq.operation_lock import OperationLockError, operation_lock

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "unrelated"
            target.write_text("do not touch")
            target.chmod(0o644)
            lock_path = base / "operation.lock"
            lock_path.symlink_to(target)

            with self.assertRaises(OperationLockError):
                with operation_lock(lock_path):
                    pass

            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_shell_lock_rejects_symlink_without_chmodding_target(self):
        installer = Path("auto-cpufreq-installer").read_text()
        start = installer.index("function acquire_operation_lock {")
        end = installer.index("\n}\n\nfunction manual_install", start) + 2
        function = installer[start:end]

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            target = base / "unrelated"
            target.write_text("do not touch")
            target.chmod(0o644)
            lock_path = base / "operation.lock"
            lock_path.symlink_to(target)
            script = (
                f"OPERATION_LOCK_PATH={str(lock_path)!r}\n"
                + function
                + "\nacquire_operation_lock\n"
            )
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)


class VersionedSourceLayoutTests(unittest.TestCase):
    def test_command_wrapper_follows_the_current_release(self):
        wrapper = Path("scripts/auto-cpufreq-venv-wrapper").read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            for name in ("old", "new"):
                bin_dir = releases / name / "venv/bin"
                bin_dir.mkdir(parents=True)
                python = bin_dir / "python"
                python.write_text(
                    "#!/bin/sh\n"
                    "entrypoint=$1\n"
                    "shift\n"
                    "exec /bin/sh \"$entrypoint\" \"$@\"\n"
                )
                python.chmod(0o755)
                entrypoint = bin_dir / "auto-cpufreq"
                entrypoint.write_text(f"printf '{name}:%s\\n' \"$1\"\n")
            (root / "current").symlink_to("releases/old")
            runnable = wrapper.replace("/opt/auto-cpufreq", str(root))

            old = subprocess.run(
                ["bash", "-s", "--", "before"],
                input=runnable,
                text=True,
                capture_output=True,
            )
            (root / "current").unlink()
            (root / "current").symlink_to("releases/new")
            new = subprocess.run(
                ["bash", "-s", "--", "after"],
                input=runnable,
                text=True,
                capture_output=True,
            )

            self.assertEqual(old.returncode, 0, old.stderr)
            self.assertEqual(old.stdout, "old:before\n")
            self.assertEqual(new.returncode, 0, new.stderr)
            self.assertEqual(new.stdout, "new:after\n")

    def test_activation_atomically_switches_current_and_syncs_parent(self):
        from auto_cpufreq.installation import activate_source_release

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            old_release = releases / "old"
            new_release = releases / "new"
            old_release.mkdir(parents=True)
            new_release.mkdir()
            (root / "current").symlink_to("releases/old")
            synced = []

            def record_fsync(descriptor):
                synced.append(stat.S_IFMT(os.fstat(descriptor).st_mode))

            with patch("auto_cpufreq.installation.os.fsync", side_effect=record_fsync):
                previous = activate_source_release(new_release, root=root)

            self.assertEqual(previous, "releases/old")
            self.assertEqual(os.readlink(root / "current"), "releases/new")
            self.assertTrue(old_release.is_dir())
            self.assertIn(stat.S_IFDIR, synced)

    def test_activation_rejects_a_release_outside_the_managed_directory(self):
        from auto_cpufreq.installation import activate_source_release

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "auto-cpufreq"
            (root / "releases").mkdir(parents=True)
            outside = base / "outside"
            outside.mkdir()

            with self.assertRaises(OSError):
                activate_source_release(outside, root=root)

            self.assertFalse((root / "current").exists())

    def test_activation_rejects_an_unmanaged_existing_current_link(self):
        from auto_cpufreq.installation import activate_source_release

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "auto-cpufreq"
            release = root / "releases/new"
            release.mkdir(parents=True)
            (root / "current").symlink_to(base / "outside")

            with self.assertRaises(OSError):
                activate_source_release(release, root=root)

            self.assertEqual(os.readlink(root / "current"), str(base / "outside"))

    def test_rollback_refuses_to_replace_an_unexpected_current_release(self):
        from auto_cpufreq.installation import restore_source_release

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            for name in ("old", "new", "other"):
                (releases / name).mkdir(parents=True, exist_ok=True)
            (root / "current").symlink_to("releases/other")

            with self.assertRaises(OSError):
                restore_source_release(
                    "releases/new",
                    "releases/old",
                    root=root,
                )

            self.assertEqual(os.readlink(root / "current"), "releases/other")

    def test_legacy_migration_preserves_the_original_venv_path(self):
        from auto_cpufreq.installation import ensure_legacy_source_current

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            legacy_venv = root / "venv"
            legacy_venv.mkdir(parents=True)
            (legacy_venv / "sentinel").write_text("legacy")

            target = ensure_legacy_source_current(root=root)

            self.assertIsNotNone(target)
            self.assertEqual((root / "current/venv/sentinel").read_text(), "legacy")
            self.assertTrue(legacy_venv.is_dir())
            self.assertTrue((root / "current/venv").is_symlink())

    def test_update_transaction_persists_rollback_inputs_before_mutation(self):
        from auto_cpufreq.installation import (
            begin_source_update,
            load_source_update,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            old_release = releases / "old"
            candidate = releases / "new"
            old_release.mkdir(parents=True)
            candidate.mkdir()
            (root / "current").symlink_to("releases/old")

            begin_source_update(candidate, daemon_was_installed=True, root=root)
            transaction = load_source_update(root=root)

            self.assertEqual(transaction.previous_target, "releases/old")
            self.assertEqual(transaction.candidate_target, "releases/new")
            self.assertTrue(transaction.daemon_was_installed)
            self.assertFalse(transaction.daemon_removed)

    def test_existing_update_transaction_cannot_be_overwritten(self):
        from auto_cpufreq.installation import begin_source_update

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            old_release = releases / "old"
            first = releases / "first"
            second = releases / "second"
            for release in (old_release, first, second):
                release.mkdir(parents=True, exist_ok=True)
            (root / "current").symlink_to("releases/old")
            begin_source_update(first, daemon_was_installed=False, root=root)

            with self.assertRaises(OSError):
                begin_source_update(second, daemon_was_installed=True, root=root)

            self.assertEqual(
                os.readlink(root / "update-transaction/candidate"),
                "../releases/first",
            )

    def test_daemon_removal_checkpoint_is_durable(self):
        from auto_cpufreq.installation import (
            begin_source_update,
            load_source_update,
            mark_source_update_daemon_removed,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            releases = root / "releases"
            old_release = releases / "old"
            candidate = releases / "new"
            old_release.mkdir(parents=True)
            candidate.mkdir()
            (root / "current").symlink_to("releases/old")
            begin_source_update(candidate, daemon_was_installed=True, root=root)

            mark_source_update_daemon_removed(root=root)

            self.assertTrue(load_source_update(root=root).daemon_removed)

    def test_source_detection_accepts_the_current_release_venv(self):
        from auto_cpufreq.installation import is_source_install

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            venv = root / "releases/revision/venv"
            venv.mkdir(parents=True)
            (root / "current").symlink_to("releases/revision")

            self.assertTrue(is_source_install(prefix=venv, root=root))

    def test_source_detection_keeps_legacy_installation_migratable(self):
        from auto_cpufreq.installation import is_source_install

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "auto-cpufreq"
            legacy_venv = root / "venv"
            legacy_venv.mkdir(parents=True)

            self.assertTrue(is_source_install(prefix=legacy_venv, root=root))


class DaemonHelperPublicationTests(unittest.TestCase):
    def test_snapshot_preflight_rejects_broken_helper_symlink(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_prepare_power_state_snapshot"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            install_helper = base / "install"
            install_helper.symlink_to(base / "missing")
            save = unittest.mock.Mock(return_value=True)
            namespace = {
                "DAEMON_INSTALL_HELPER": install_helper,
                "DAEMON_REMOVE_HELPER": base / "remove",
                "CPUFREQCTL_PATH": base / "cpufreqctl",
                "bluetoothctl_exists": False,
                "power_state_exists": lambda: False,
                "save_power_state": save,
            }
            exec(compile(ast.Module([function], []), "core.py", "exec"), namespace)

            self.assertFalse(namespace["_prepare_power_state_snapshot"]())
            save.assert_not_called()

    def test_source_installer_preflight_rejects_broken_install_symlink(self):
        installer = Path("auto-cpufreq-installer").read_text()
        start = installer.index("function source_install_preflight {")
        end = installer.index("\n}\n\nfunction install_el_pygobject", start) + 2
        function = installer[start:end]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            command = base / "auto-cpufreq"
            (base / "auto-cpufreq-install").symlink_to(base / "missing")
            script = (
                f"AUTO_CPUFREQ_FILE={str(command)!r}\n"
                f"POWER_STATE_SNAPSHOT={str(base / 'snapshot')!r}\n"
                + function
                + "\nsource_install_preflight\n"
            )
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
            )

            self.assertNotEqual(result.returncode, 0)

    def test_prepare_only_install_does_not_require_daemon_removal(self):
        installer = Path("auto-cpufreq-installer").read_text()
        start = installer.index("function source_install_preflight {")
        end = installer.index("\n}\n\nfunction install_el_pygobject", start) + 2
        function = installer[start:end]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            command = base / "auto-cpufreq"
            (base / "auto-cpufreq-remove").write_text("installed daemon")
            script = (
                f"AUTO_CPUFREQ_FILE={str(command)!r}\n"
                f"POWER_STATE_SNAPSHOT={str(base / 'snapshot')!r}\n"
                + function
                + "\nsource_install_preflight prepare\n"
            )
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _load_publication_function(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_publish_new_owned_helper"
        )
        namespace = {
            "os": os,
            "stat": stat,
            "Path": Path,
            "uuid4": __import__("uuid").uuid4,
        }
        exec(compile(ast.Module([function], []), "core.py", "exec"), namespace)
        return namespace["_publish_new_owned_helper"]

    def test_helper_publication_is_atomic_and_never_overwrites(self):
        publish = self._load_publication_function()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            source.write_text("complete helper\n")
            destination = base / "destination"

            publish(source, destination)
            self.assertEqual(destination.read_text(), "complete helper\n")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)

            destination.write_text("replacement\n")
            with self.assertRaises(OSError):
                publish(source, destination)
            self.assertEqual(destination.read_text(), "replacement\n")

    def test_cpufreqctl_does_not_follow_a_broken_destination_symlink(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_publish_new_owned_helper", "cpufreqctl"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts = base / "scripts"
            scripts.mkdir()
            (scripts / "cpufreqctl.sh").write_text("owned helper\n")
            external = base / "external"
            destination = base / "cpufreqctl"
            destination.symlink_to(external)
            namespace = {
                "os": os,
                "stat": stat,
                "Path": Path,
                "uuid4": __import__("uuid").uuid4,
                "IS_INSTALLED_WITH_SNAP": False,
                "SCRIPTS_DIR": scripts,
                "CPUFREQCTL_PATH": destination,
                "copy": __import__("shutil").copy,
            }
            exec(compile(ast.Module(functions, []), "core.py", "exec"), namespace)

            with self.assertRaises(OSError):
                namespace["cpufreqctl"]()
            self.assertFalse(external.exists())

    def test_remove_marker_is_published_before_install_helper(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_deploy_daemon_helpers"
        )
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            namespace = {
                "SCRIPTS_DIR": base / "source",
                "DAEMON_INSTALL_HELPER": base / "installed/install",
                "DAEMON_REMOVE_HELPER": base / "installed/remove",
            }
            (base / "installed").mkdir()
            exec(compile(ast.Module([function], []), "core.py", "exec"), namespace)

            destinations = []

            def record_publication(source, destination):
                destinations.append(destination)
                destination.touch()

            with patch.dict(
                namespace,
                {"_publish_new_owned_helper": record_publication},
            ):
                namespace["_deploy_daemon_helpers"]()

            self.assertEqual(
                destinations,
                [
                    namespace["DAEMON_REMOVE_HELPER"],
                    namespace["DAEMON_INSTALL_HELPER"],
                ],
            )

    def test_replaced_lifecycle_helper_is_preserved_and_cleanup_fails(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_owned_file_matches",
                "_remove_owned_file",
                "_remove_daemon_helpers",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts = base / "scripts"
            scripts.mkdir()
            install_source = scripts / "auto-cpufreq-install.sh"
            remove_source = scripts / "auto-cpufreq-remove.sh"
            install_source.write_text("owned install\n")
            remove_source.write_text("owned remove\n")
            install_helper = base / "install"
            remove_helper = base / "remove"
            install_helper.write_text("host replacement\n")
            remove_helper.write_text("owned remove\n")
            namespace = {
                "Path": Path,
                "SCRIPTS_DIR": scripts,
                "DAEMON_INSTALL_HELPER": install_helper,
                "DAEMON_REMOVE_HELPER": remove_helper,
            }
            exec(compile(ast.Module(functions, []), "core.py", "exec"), namespace)

            self.assertFalse(namespace["_remove_daemon_helpers"]())
            self.assertTrue(install_helper.exists())
            self.assertTrue(remove_helper.exists())

    def test_replaced_cpufreqctl_is_not_reported_as_cleaned(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {
                "_owned_file_matches",
                "_remove_owned_file",
                "_remove_owned_cpufreqctl",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts = base / "scripts"
            scripts.mkdir()
            (scripts / "cpufreqctl.sh").write_text("owned\n")
            destination = base / "cpufreqctl"
            destination.write_text("replacement\n")
            namespace = {
                "Path": Path,
                "CPUFREQCTL_PATH": destination,
                "SCRIPTS_DIR": scripts,
            }
            exec(compile(ast.Module(functions, []), "core.py", "exec"), namespace)

            self.assertFalse(namespace["_remove_owned_cpufreqctl"]())
            self.assertTrue(destination.exists())

    def test_replaced_remove_helper_is_not_executed(self):
        tree = ast.parse(Path("auto_cpufreq/core.py").read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"_owned_file_matches", "_run_daemon_helper"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            scripts = base / "scripts"
            scripts.mkdir()
            (scripts / "auto-cpufreq-remove.sh").write_text("owned\n")
            helper = base / "remove"
            helper.write_text("replacement\n")
            command = unittest.mock.Mock(
                return_value=subprocess.CompletedProcess([], 0)
            )
            namespace = {
                "Path": Path,
                "SCRIPTS_DIR": scripts,
                "DAEMON_INSTALL_HELPER": base / "install",
                "DAEMON_REMOVE_HELPER": helper,
                "run": command,
            }
            exec(compile(ast.Module(functions, []), "core.py", "exec"), namespace)

            self.assertFalse(namespace["_run_daemon_helper"](helper, "remove"))
            command.assert_not_called()


class InitArtifactRemovalTests(unittest.TestCase):
    def test_s6_frontend_full_removal_and_retry_order(self):
        script = Path("scripts/auto-cpufreq-remove.sh").read_text()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            service_dir = base / "s6/auto-cpufreq"
            source_dir = base / "source-s6"
            service_dir.mkdir(parents=True)
            source_dir.mkdir()
            for name, content in (("run", "run\n"), ("type", "longrun\n")):
                (service_dir / name).write_text(content)
                (source_dir / name).write_text(content)
            script = script.replace(
                "/etc/s6/sv/auto-cpufreq",
                str(service_dir),
            ).replace(
                "/usr/local/share/auto-cpufreq/scripts/auto-cpufreq-s6",
                str(source_dir),
            ).replace(
                "/etc/s6/adminsv/default/contents.d/auto-cpufreq",
                str(base / "bundle-entry"),
            )

            commands = base / "bin"
            commands.mkdir()
            ps = commands / "ps"
            ps.write_text("#!/bin/sh\nprintf 's6-svscan\\n'\n")
            ps.chmod(0o755)
            log = base / "commands.log"
            s6 = commands / "s6"
            s6.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' \"$*\" >> {str(log)!r}\n"
                "exit 0\n"
            )
            s6.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{commands}:{environment['PATH']}"
            environment["TERM"] = "xterm"

            first = subprocess.run(
                ["bash"], input=script, text=True, capture_output=True, env=environment
            )
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertFalse(service_dir.exists())
            self.assertEqual(
                log.read_text().splitlines(),
                [
                    "live stop auto-cpufreq",
                    "set mask auto-cpufreq",
                    "set commit -f",
                    "live install",
                    "repository sync",
                ],
            )

            log.write_text("")
            second = subprocess.run(
                ["bash"], input=script, text=True, capture_output=True, env=environment
            )
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertEqual(
                log.read_text().splitlines(),
                [
                    "live stop auto-cpufreq",
                    "repository sync",
                    "set commit -f",
                    "live install",
                ],
            )

    def test_systemd_refreshes_state_after_stop_and_disable(self):
        script = Path("scripts/auto-cpufreq-remove.sh").read_text()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = base / "installed.service"
            source = base / "source.service"
            installed.write_text("owned unit\n")
            source.write_text("owned unit\n")
            state = base / "state"
            state.write_text("active enabled\n")
            script = script.replace(
                "/etc/systemd/system/auto-cpufreq.service",
                str(installed),
            ).replace(
                "/usr/local/share/auto-cpufreq/scripts/auto-cpufreq.service",
                str(source),
            )

            commands = base / "bin"
            commands.mkdir()
            ps = commands / "ps"
            ps.write_text("#!/bin/sh\nprintf 'systemd\\n'\n")
            ps.chmod(0o755)
            systemctl = commands / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\n"
                f"state={str(state)!r}\n"
                "command=$1\n"
                "set -- $(cat \"$state\")\n"
                "active=$1\n"
                "enabled=$2\n"
                "case \"$command\" in\n"
                "  show) printf 'LoadState=loaded\\nActiveState=%s\\nUnitFileState=%s\\nFragmentPath=%s\\n' \"$active\" \"$enabled\" "
                + repr(str(installed))
                + ";;\n"
                "  stop) printf 'inactive %s\\n' \"$enabled\" > \"$state\";;\n"
                "  disable) printf '%s disabled\\n' \"$active\" > \"$state\";;\n"
                "  daemon-reload|reset-failed) :;;\n"
                "  *) exit 1;;\n"
                "esac\n"
            )
            systemctl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{commands}:{environment['PATH']}"
            environment["TERM"] = "xterm"
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertFalse(installed.exists())

    def test_systemd_removes_owned_unit_even_when_manager_has_not_loaded_it(self):
        script = Path("scripts/auto-cpufreq-remove.sh").read_text()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            installed = base / "installed.service"
            source = base / "source.service"
            installed.write_text("owned unit\n")
            source.write_text("owned unit\n")
            script = script.replace(
                "/etc/systemd/system/auto-cpufreq.service",
                str(installed),
            ).replace(
                "/usr/local/share/auto-cpufreq/scripts/auto-cpufreq.service",
                str(source),
            )

            commands = base / "bin"
            commands.mkdir()
            ps = commands / "ps"
            ps.write_text("#!/bin/sh\nprintf 'systemd\\n'\n")
            ps.chmod(0o755)
            systemctl = commands / "systemctl"
            systemctl.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  show) printf 'LoadState=not-found\\n'; exit 1;;\n"
                "  is-enabled) printf 'not-found\\n'; exit 4;;\n"
                "  daemon-reload|reset-failed) exit 0;;\n"
                "esac\n"
                "exit 1\n"
            )
            systemctl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{commands}:{environment['PATH']}"
            environment["TERM"] = "xterm"
            result = subprocess.run(
                ["bash"],
                input=script,
                text=True,
                capture_output=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(installed.exists())

    def test_s6_frontend_removes_service_from_sets_before_store_definition(self):
        script = Path("scripts/auto-cpufreq-remove.sh").read_text()
        start = script.index('  s6-svscan)')
        end = script.index('\n  ;;', start)
        frontend = script[start:end]

        mask = frontend.index("s6 set mask auto-cpufreq")
        commit = frontend.index("s6 set commit", mask)
        install = frontend.index("s6 live install", commit)
        remove = frontend.index("rm -rf /etc/s6/sv/auto-cpufreq", install)
        sync = frontend.index("s6 repository sync", remove)
        self.assertLess(mask, commit)
        self.assertLess(commit, install)
        self.assertLess(install, remove)
        self.assertLess(remove, sync)


if __name__ == "__main__":
    unittest.main()
