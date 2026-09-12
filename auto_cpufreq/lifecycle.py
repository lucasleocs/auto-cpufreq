import os
from pathlib import Path
from subprocess import run

from requests import exceptions, get

from auto_cpufreq import core
from auto_cpufreq.daemon_preflight import (
    DaemonPreflightError,
    ensure_daemon_service_available,
)
from auto_cpufreq.globals import IS_INSTALLED_WITH_SNAP
from auto_cpufreq.installation import SOURCE_COMMAND, SOURCE_VENV, is_source_install
from auto_cpufreq.operation_lock import (
    INHERITED_LOCK_FD_ENV,
    OperationLockError,
    operation_lock,
)
from auto_cpufreq import power_helper
from auto_cpufreq.release_update import (
    cleanup_staging_workspace,
    extract_git_commit,
    new_staging_destination,
    stage_release,
    staged_release_commit,
    version_matches_exact_commit,
    version_matches_release,
)


class LifecycleError(RuntimeError):
    pass


def set_bluetooth_boot_enabled(enabled: bool) -> bool:
    if IS_INSTALLED_WITH_SNAP:
        if enabled:
            power_helper.bluetooth_on_notif_snap()
        else:
            power_helper.bluetooth_notif_snap()
        return True

    if not power_helper.bluetoothctl_exists:
        state = "on" if enabled else "off"
        print(
            f"* Turn {state} bluetooth on boot [skipping] "
            "(package providing bluetooth access is not present)"
        )
        return True

    if enabled:
        print("* Turn on bluetooth on boot")
    else:
        print("* Turn off Bluetooth on boot (only)!")
        print(
            "  If you want bluetooth enabled on boot run: "
            "auto-cpufreq --bluetooth_boot_on"
        )

    if power_helper.set_bluetooth_auto_enable(enabled):
        return True

    action = "turn on" if enabled else "turn off"
    print(f"\nERROR:\nWas unable to {action} bluetooth on boot")
    return False


def _safe_deploy_daemon() -> None:
    try:
        ensure_daemon_service_available()
    except DaemonPreflightError as exc:
        raise LifecycleError(str(exc)) from exc

    print(
        "\n" + "-" * 21
        + " Deploying auto-cpufreq as a daemon "
        + "-" * 22 + "\n"
    )

    if not core._prepare_power_state_snapshot():
        raise LifecycleError(
            "Unable to prepare the host power-state snapshot for daemon installation."
        )

    remove_cpufreqctl_on_rollback = (
        not IS_INSTALLED_WITH_SNAP and not core.CPUFREQCTL_PATH.exists()
    )
    remove_stats_on_rollback = not core.auto_cpufreq_stats_path.exists()

    try:
        core.cpufreqctl()
        if not set_bluetooth_boot_enabled(False):
            raise OSError("failed to update the Bluetooth boot policy")
        if remove_stats_on_rollback:
            core.auto_cpufreq_stats_path.touch()
        core._deploy_daemon_helpers()
    except OSError as exc:
        print(
            f"\nERROR: Unable to prepare auto-cpufreq daemon "
            f"installation: {exc}"
        )
        print("Rolling back daemon setup before restoring power state.")
        core._rollback_daemon_setup(
            remove_stats=remove_stats_on_rollback,
            remove_cpufreqctl=remove_cpufreqctl_on_rollback,
        )
        raise LifecycleError("Daemon setup failed and was rolled back.") from exc

    power_helper.gnome_power_detect_install()
    if not power_helper.gnome_power_svc_disable():
        print(
            "\nThe GNOME power profiles service could not be disabled "
            "safely. Rolling back daemon setup before restoring the "
            "pre-install power-management state."
        )
        core._rollback_daemon_setup(
            remove_stats=remove_stats_on_rollback,
            remove_cpufreqctl=remove_cpufreqctl_on_rollback,
        )
        raise LifecycleError("Unable to disable GNOME Power Profiles safely.")

    if not power_helper.tuned_svc_disable():
        print(
            "\nThe TuneD service could not be disabled safely. "
            "Rolling back daemon setup before restoring the pre-install "
            "power-management state."
        )
        core._rollback_daemon_setup(
            remove_stats=remove_stats_on_rollback,
            remove_cpufreqctl=remove_cpufreqctl_on_rollback,
        )
        raise LifecycleError("Unable to disable TuneD safely.")

    power_helper.tlp_service_detect()

    if not core._run_daemon_helper(core.DAEMON_INSTALL_HELPER, "install"):
        print(
            "\nThe daemon was not installed successfully. "
            "Rolling back the partial daemon installation before "
            "restoring the pre-install power-management state."
        )
        core._rollback_failed_daemon_install(
            remove_stats=remove_stats_on_rollback,
            remove_cpufreqctl=remove_cpufreqctl_on_rollback,
        )
        raise LifecycleError("The daemon installation helper failed.")


def install_daemon() -> None:
    try:
        with operation_lock(operation="daemon installation"):
            core.running_daemon_check()
            core.gov_check()
            _safe_deploy_daemon()
    except OperationLockError as exc:
        raise LifecycleError(str(exc)) from exc


def remove_daemon() -> None:
    try:
        with operation_lock(operation="daemon removal"):
            core.remove_daemon()
    except OperationLockError as exc:
        raise LifecycleError(str(exc)) from exc


def _installed_source_version() -> str | None:
    python = SOURCE_VENV / "bin/python"
    if not python.is_file():
        return None

    try:
        result = run(
            [
                str(python),
                "-c",
                "from importlib.metadata import version; "
                "print(version('auto-cpufreq'))",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    version = result.stdout.strip()
    return version or None


def _staged_commit_is_descendant(installed_version: str, staged_commit: str) -> bool:
    installed_commit = extract_git_commit(installed_version)
    if installed_commit is None:
        return False

    api_repository = core.GITHUB.replace("github.com", "api.github.com/repos")
    compare_url = f"{api_repository}/compare/{installed_commit}...{staged_commit}"
    try:
        response = get(compare_url, timeout=core.GITHUB_REQUEST_TIMEOUT)
    except (
        exceptions.ConnectionError,
        exceptions.Timeout,
        exceptions.RequestException,
    ):
        return False

    if response.status_code != 200:
        return False

    try:
        return response.json().get("status") == "ahead"
    except ValueError:
        return False


def _install_staged_source(staged_source: Path, lock_handle) -> bool:
    installer = Path(staged_source) / "auto-cpufreq-installer"
    if not installer.is_file():
        print("Error: The staged release does not contain auto-cpufreq-installer.")
        return False

    fd = lock_handle.fileno()
    env = os.environ.copy()
    env[INHERITED_LOCK_FD_ENV] = str(fd)

    try:
        result = run(
            ["bash", str(installer), "--install"],
            cwd=str(staged_source),
            env=env,
            pass_fds=(fd,),
        )
    except OSError as exc:
        print(f"Error: Failed to start the staged installer: {exc}")
        return False

    if result.returncode != 0:
        print(
            "Error: The staged auto-cpufreq installer failed with status "
            f"{result.returncode}."
        )
        return False

    return True


def _reenable_daemon(lock_handle) -> bool:
    if not SOURCE_COMMAND.is_file():
        print(
            "auto-cpufreq was updated, but the installed source command "
            f"is unavailable at {SOURCE_COMMAND}."
        )
        return False

    fd = lock_handle.fileno()
    env = os.environ.copy()
    env[INHERITED_LOCK_FD_ENV] = str(fd)

    try:
        result = run(
            [str(SOURCE_COMMAND), "--install"],
            env=env,
            pass_fds=(fd,),
        )
    except OSError as exc:
        print(f"auto-cpufreq was updated, but the daemon could not be re-enabled: {exc}")
        return False

    if result.returncode != 0:
        print("auto-cpufreq was updated, but the daemon could not be re-enabled.")
        print("Run `sudo auto-cpufreq --install` after reviewing the error above.")
        return False

    return True


def update_source_install(custom_dir: str) -> bool:
    if not is_source_install():
        raise LifecycleError(
            "Automatic self-update is only supported for installations made "
            "with auto-cpufreq-installer. Use the package manager that owns "
            "this installation instead."
        )

    try:
        with operation_lock(operation="update") as lock_handle:
            release_tag = core.check_for_update()
            if not release_tag:
                return False

            answer = input(
                "Do you want to update auto-cpufreq to the latest stable "
                "release? [Y/n]: "
            ).strip().lower()
            if answer not in ("", "y", "yes"):
                print("Aborted")
                return False

            workspace = new_staging_destination(Path(custom_dir))
            staged_source = stage_release(
                core.GITHUB + ".git",
                release_tag,
                workspace,
            )
            if staged_source is None:
                print(f"Error: Failed to stage stable release {release_tag}.")
                print("The current auto-cpufreq installation was not changed.")
                return False

            try:
                staged_commit = staged_release_commit(staged_source)
                if staged_commit is None:
                    print(
                        "Error: Unable to determine the Git revision of the "
                        "staged stable release."
                    )
                    print("The current auto-cpufreq installation was not changed.")
                    return False

                installed_version = core.get_literal_version("auto-cpufreq")
                if not _staged_commit_is_descendant(installed_version, staged_commit):
                    print(
                        "Error: The exact staged Git revision could not be "
                        "verified as a descendant of the installed source revision."
                    )
                    print("The current auto-cpufreq installation was not changed.")
                    return False

                daemon_was_installed = core.DAEMON_REMOVE_HELPER.exists()
                power_state_pending = core.power_state_exists()

                if daemon_was_installed or power_state_pending:
                    core.remove_daemon()
                    if daemon_was_installed:
                        core.remove_complete_msg()

                if not _install_staged_source(staged_source, lock_handle):
                    print("The stable release could not be installed.")
                    if daemon_was_installed:
                        print(
                            "The previous daemon was removed before installation "
                            "and was not re-enabled."
                        )
                    return False

                installed_version = _installed_source_version()
                if installed_version is None:
                    print(
                        "The update command cannot read the version metadata "
                        "from the newly installed source environment."
                    )
                    return False

                if not version_matches_release(installed_version, release_tag):
                    print(
                        "The installed version does not match the staged release "
                        f"{release_tag}."
                    )
                    print(f"Reported installed version: {installed_version}")
                    return False

                if not version_matches_exact_commit(installed_version, staged_commit):
                    print(
                        "The update command cannot confirm that the exact staged "
                        "Git revision was installed."
                    )
                    print(f"Expected staged revision: {staged_commit}")
                    print(f"Installed package version: {installed_version}")
                    return False

                if daemon_was_installed and not _reenable_daemon(lock_handle):
                    return False

                print(
                    "auto-cpufreq successfully updated to stable release "
                    f"{release_tag}"
                )
                return True
            finally:
                if not cleanup_staging_workspace(staged_source):
                    print(
                        "Warning: The update staging workspace could not be "
                        "removed."
                    )
    except OperationLockError as exc:
        raise LifecycleError(str(exc)) from exc
