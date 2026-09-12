from pathlib import Path
from subprocess import run
from typing import Optional


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
        result = run(
            [
                "systemctl",
                "list-unit-files",
                "auto-cpufreq.service",
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise DaemonPreflightError(
                "Unable to inspect existing systemd service definitions."
            )
        if result.stdout.strip():
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
        path = first_existing_path(["/etc/s6/sv/auto-cpufreq"])
        return None if path is None else str(path)

    return None


def ensure_daemon_service_available() -> None:
    conflict = daemon_service_conflict()
    if conflict is not None:
        raise DaemonPreflightError(
            "Refusing to overwrite an existing auto-cpufreq service artifact: "
            f"{conflict}. Remove the existing package/service integration first."
        )
