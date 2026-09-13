# Protect service-manager artifacts before daemon installation.
#
# The lifecycle helper must never overwrite a service definition merely because
# it has auto-cpufreq's name: the artifact may belong to a distro package or a
# different installation method. This module only detects conflicts; ownership
# and rollback remain the responsibility of the lifecycle layer.

from pathlib import Path
from typing import Optional

from auto_cpufreq.systemd import SystemdQueryError, query_unit_file_state


class DaemonPreflightError(RuntimeError):
    pass

def first_existing_path(paths):
    for path in map(Path, paths):
        if path.exists() or path.is_symlink():
            return path
    return None


def _init_name() -> str:
    try:
        return Path("/proc/1/comm").read_text().strip()
    except OSError as exc:
        raise DaemonPreflightError(
            f"Unable to determine init system: {exc}"
        ) from exc


def _os_release_id() -> str:
    for candidate in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            lines = candidate.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("ID="):
                return line.partition("=")[2].strip().strip('"')
    return ""


def daemon_service_conflict() -> Optional[str]:
    init_name = _init_name()

    if init_name == "systemd":
        # Check common unit locations first, then ask systemd itself. The latter
        # catches vendor paths or aliases that are outside this explicit list.
        path = first_existing_path(
            [
                "/etc/systemd/system/auto-cpufreq.service",
                "/run/systemd/system/auto-cpufreq.service",
                "/usr/local/lib/systemd/system/auto-cpufreq.service",
                "/usr/lib/systemd/system/auto-cpufreq.service",
                "/lib/systemd/system/auto-cpufreq.service",
            ]
        )
        if path is not None:
            return str(path)
        try:
            unit_file_state = query_unit_file_state("auto-cpufreq.service")
        except SystemdQueryError as exc:
            raise DaemonPreflightError(
                "Unable to inspect existing systemd service definitions."
            ) from exc
        if unit_file_state != "not-found":
            return "systemd unit auto-cpufreq.service"
        return None

    if init_name == "dinit":
        path = first_existing_path(["/etc/dinit.d/auto-cpufreq"])
        return None if path is None else str(path)

    if init_name in ("init", "openrc-init"):
        path = first_existing_path(["/etc/init.d/auto-cpufreq"])
        return None if path is None else str(path)

    if init_name == "runit":
        distro = _os_release_id()
        if distro == "void":
            paths = ["/etc/sv/auto-cpufreq", "/var/service/auto-cpufreq"]
        elif distro == "artix":
            paths = [
                "/etc/runit/sv/auto-cpufreq",
                "/run/runit/service/auto-cpufreq",
            ]
        else:
            return None
        path = first_existing_path(paths)
        return None if path is None else str(path)

    if init_name == "s6-svscan":
        path = first_existing_path(
            [
                "/etc/s6/sv/auto-cpufreq",
                "/etc/s6/adminsv/default/contents.d/auto-cpufreq",
            ]
        )
        return None if path is None else str(path)

    return None


def ensure_daemon_service_available() -> None:
    conflict = daemon_service_conflict()
    if conflict is not None:
        raise DaemonPreflightError(
            "Refusing to overwrite an existing auto-cpufreq service artifact: "
            f"{conflict}. Remove the existing package/service integration first."
        )
