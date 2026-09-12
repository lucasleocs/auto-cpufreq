import json
import os
from pathlib import Path
from shutil import which
from subprocess import run
from uuid import uuid4


DEFAULT_STATE_DIR = Path("/var/lib/auto-cpufreq")
DEFAULT_BLUETOOTH_CONFIG = Path("/etc/bluetooth/main.conf")
DEFAULT_INIT_COMM = Path("/proc/1/comm")
STATE_FILE_NAME = "power-state.json"
POWER_SERVICES = (
    "power-profiles-daemon.service",
    "tuned.service",
)

_ACTIVE_STATES = {"active", "activating", "reloading"}
_NO_ENABLE_ACTION_STATES = {
    "static",
    "indirect",
    "generated",
    "transient",
    "alias",
    "invalid",
    "",
}


def _command_exists(command: str) -> bool:
    return Path(command).is_file() or which(command) is not None


def _run(args, *, capture_output=False):
    try:
        return run(args, capture_output=capture_output, text=True)
    except (OSError, FileNotFoundError, PermissionError):
        return None


def _systemd_is_pid1(init_comm: Path = DEFAULT_INIT_COMM) -> bool:
    try:
        return Path(init_comm).read_text().strip() == "systemd"
    except OSError:
        return False


def _missing_service_state():
    return {
        "load_state": "not-found",
        "active_state": "inactive",
        "unit_file_state": "",
        "fragment_path": "",
    }


def capture_service_state(unit: str, *, systemctl: str = "systemctl"):
    result = _run(
        [
            systemctl,
            "show",
            unit,
            "--no-pager",
            "--property=LoadState",
            "--property=ActiveState",
            "--property=UnitFileState",
            "--property=FragmentPath",
        ],
        capture_output=True,
    )
    if result is None:
        return None

    properties = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value

    # systemctl show has differed across systemd versions for missing units:
    # some return success with LoadState=not-found, while others return a
    # non-zero status. Treat absence as a valid snapshot state, but keep real
    # systemctl failures fatal.
    if properties.get("LoadState") == "not-found":
        return _missing_service_state()

    if result.returncode != 0:
        installed = _run(
            [
                systemctl,
                "list-unit-files",
                unit,
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
        )
        if (
            installed is not None
            and installed.returncode == 0
            and not installed.stdout.strip()
        ):
            return _missing_service_state()
        return None

    required = ("LoadState", "ActiveState", "UnitFileState", "FragmentPath")
    if any(key not in properties for key in required):
        return None

    return {
        "load_state": properties["LoadState"],
        "active_state": properties["ActiveState"],
        "unit_file_state": properties["UnitFileState"],
        "fragment_path": properties["FragmentPath"],
    }


def _systemctl(systemctl: str, *args: str) -> bool:
    result = _run([systemctl, *args])
    return result is not None and result.returncode == 0


def restore_service_state(unit: str, state, *, systemctl: str = "systemctl") -> bool:
    if not state or state.get("load_state") == "not-found":
        return True

    success = _systemctl(systemctl, "unmask", unit)
    unit_file_state = state.get("unit_file_state", "")
    fragment_path = state.get("fragment_path", "")

    if unit_file_state == "enabled":
        success = _systemctl(systemctl, "enable", unit) and success
    elif unit_file_state == "enabled-runtime":
        success = _systemctl(systemctl, "enable", "--runtime", unit) and success
    elif unit_file_state == "disabled":
        success = _systemctl(systemctl, "disable", unit) and success
    elif unit_file_state == "linked" and fragment_path:
        success = _systemctl(systemctl, "link", fragment_path) and success
    elif unit_file_state == "linked-runtime" and fragment_path:
        success = _systemctl(systemctl, "link", "--runtime", fragment_path) and success
    elif unit_file_state not in _NO_ENABLE_ACTION_STATES | {"masked", "masked-runtime"}:
        return False

    if state.get("active_state") in _ACTIVE_STATES:
        success = _systemctl(systemctl, "start", unit) and success
    else:
        success = _systemctl(systemctl, "stop", unit) and success

    if unit_file_state == "masked":
        success = _systemctl(systemctl, "mask", unit) and success
    elif unit_file_state == "masked-runtime":
        success = _systemctl(systemctl, "mask", "--runtime", unit) and success

    return success


def _capture_power_profiles_profile(powerprofilesctl: str):
    if not _command_exists(powerprofilesctl):
        return None
    result = _run([powerprofilesctl, "get"], capture_output=True)
    if result is None or result.returncode != 0:
        return None
    profile = result.stdout.strip()
    return profile or None


def _state_path(state_dir: Path):
    return state_dir / STATE_FILE_NAME


def _try_unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _is_policy_header(line: str) -> bool:
    return line.strip().lower() == "[policy]"


def _is_section_header(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def _is_auto_enable_line(line: str) -> bool:
    stripped = line.strip()
    candidate = stripped.lstrip("#").strip()
    return candidate.startswith("AutoEnable=")


def _capture_bluetooth_state(bluetooth_config: Path):
    bluetooth_config = Path(bluetooth_config)
    if not bluetooth_config.exists():
        return {
            "config_present": False,
            "policy_present": False,
            "auto_enable_lines": [],
        }

    try:
        lines = bluetooth_config.read_text().splitlines(keepends=True)
    except OSError:
        return None

    policy_present = False
    in_policy = False
    auto_enable_lines = []

    for line in lines:
        if _is_section_header(line):
            in_policy = _is_policy_header(line)
            policy_present = policy_present or in_policy
            continue

        if in_policy and _is_auto_enable_line(line):
            auto_enable_lines.append(line)

    return {
        "config_present": True,
        "policy_present": policy_present,
        "auto_enable_lines": auto_enable_lines,
    }


def _valid_bluetooth_state(state) -> bool:
    if not isinstance(state, dict):
        return False
    if not isinstance(state.get("config_present"), bool):
        return False
    if not isinstance(state.get("policy_present"), bool):
        return False
    lines = state.get("auto_enable_lines")
    return isinstance(lines, list) and all(isinstance(line, str) for line in lines)


def _bluetooth_state_is_managed_false(state) -> bool:
    lines = state.get("auto_enable_lines", [])
    if not lines:
        return False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            return False
        _, separator, value = stripped.partition("=")
        if not separator or value.strip().lower() != "false":
            return False

    return True


def _drop_empty_created_policy_section(lines):
    result = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if not _is_policy_header(line):
            result.append(line)
            index += 1
            continue

        end = index + 1
        while end < len(lines) and not _is_section_header(lines[end]):
            end += 1

        body = lines[index + 1:end]
        if all(not item.strip() for item in body):
            if result and not result[-1].strip():
                result.pop()
            index = end
            continue

        result.extend(lines[index:end])
        index = end

    return result


def _restore_bluetooth_state(bluetooth_config: Path, original_state) -> bool:
    if not original_state.get("config_present"):
        return True

    current_state = _capture_bluetooth_state(bluetooth_config)
    if current_state is None:
        return False

    # If the file or AutoEnable state changed after installation, leave the
    # newer user/system state untouched instead of overwriting it during
    # daemon removal.
    if not current_state.get("config_present"):
        return True
    if not _bluetooth_state_is_managed_false(current_state):
        return True

    try:
        lines = Path(bluetooth_config).read_text().splitlines(keepends=True)
    except OSError:
        return False

    original_lines = original_state.get("auto_enable_lines", [])
    new_lines = []
    in_policy = False
    inserted = False

    for line in lines:
        if _is_section_header(line):
            in_policy = _is_policy_header(line)
            new_lines.append(line)
            continue

        if in_policy and _is_auto_enable_line(line):
            if not inserted:
                new_lines.extend(original_lines)
                inserted = True
            continue

        new_lines.append(line)

    if not original_state.get("policy_present") and not original_lines:
        new_lines = _drop_empty_created_policy_section(new_lines)

    try:
        Path(bluetooth_config).write_text("".join(new_lines))
    except OSError:
        return False

    return True


def _valid_snapshot(snapshot) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if snapshot.get("version") != 1:
        return False

    services = snapshot.get("services")
    if not isinstance(services, dict):
        return False

    required_service_fields = (
        "load_state",
        "active_state",
        "unit_file_state",
        "fragment_path",
    )
    for unit, state in services.items():
        if unit not in POWER_SERVICES or not isinstance(state, dict):
            return False
        if any(
            not isinstance(state.get(field), str)
            for field in required_service_fields
        ):
            return False

    profile = snapshot.get("power_profiles_profile")
    if profile is not None and not isinstance(profile, str):
        return False

    return _valid_bluetooth_state(snapshot.get("bluetooth"))


def power_state_exists(*, state_dir: Path = DEFAULT_STATE_DIR) -> bool:
    state_file = _state_path(Path(state_dir))
    return state_file.exists()


def save_power_state(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    bluetooth_config: Path = DEFAULT_BLUETOOTH_CONFIG,
    systemctl: str = "systemctl",
    powerprofilesctl: str = "powerprofilesctl",
    init_comm: Path = DEFAULT_INIT_COMM,
) -> bool:
    state_dir = Path(state_dir)
    bluetooth_config = Path(bluetooth_config)
    state_file = _state_path(state_dir)

    # Never replace the original pre-install snapshot with a later state.
    if state_file.exists():
        return False

    services = {}
    power_profiles_profile = None
    if _systemd_is_pid1(init_comm):
        if not _command_exists(systemctl):
            return False

        for unit in POWER_SERVICES:
            state = capture_service_state(unit, systemctl=systemctl)
            if state is None:
                return False
            services[unit] = state

        ppd_state = services.get("power-profiles-daemon.service")
        if (
            ppd_state is not None
            and ppd_state.get("active_state") in _ACTIVE_STATES
        ):
            power_profiles_profile = _capture_power_profiles_profile(
                powerprofilesctl
            )
            if power_profiles_profile is None:
                return False

    bluetooth_state = _capture_bluetooth_state(bluetooth_config)
    if bluetooth_state is None:
        return False

    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_dir.chmod(0o700)
    except OSError:
        return False

    snapshot = {
        "version": 1,
        "services": services,
        "power_profiles_profile": power_profiles_profile,
        "bluetooth": bluetooth_state,
    }

    # Publish through a unique file in the same directory. link() creates
    # the canonical snapshot name atomically and refuses to replace a snapshot
    # that another installer created after the pre-check above.
    temporary = state_dir / (
        f".{STATE_FILE_NAME}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        temporary.chmod(0o600)
        os.link(temporary, state_file)
    except OSError:
        return False
    finally:
        _try_unlink(temporary)

    return True


def _restore_power_profiles_profile(profile, powerprofilesctl: str) -> bool:
    if not profile:
        return True
    if not _command_exists(powerprofilesctl):
        return False
    result = _run([powerprofilesctl, "set", profile])
    return result is not None and result.returncode == 0


def restore_power_state(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    bluetooth_config: Path = DEFAULT_BLUETOOTH_CONFIG,
    systemctl: str = "systemctl",
    powerprofilesctl: str = "powerprofilesctl",
) -> bool:
    state_dir = Path(state_dir)
    bluetooth_config = Path(bluetooth_config)
    state_file = _state_path(state_dir)

    try:
        snapshot = json.loads(state_file.read_text())
    except (OSError, ValueError, TypeError):
        return False

    if not _valid_snapshot(snapshot):
        return False

    success = True
    services = snapshot["services"]
    if services:
        if not _command_exists(systemctl):
            success = False
        else:
            for unit, state in services.items():
                if not restore_service_state(unit, state, systemctl=systemctl):
                    success = False

    bluetooth_state = snapshot["bluetooth"]

    if bluetooth_state is None or not _restore_bluetooth_state(
        bluetooth_config,
        bluetooth_state,
    ):
        success = False

    if not _restore_power_profiles_profile(
        snapshot.get("power_profiles_profile"),
        powerprofilesctl,
    ):
        success = False

    # Keep the snapshot intact on any restoration failure so a later retry can
    # use the original state rather than whatever partial state now exists.
    if not success:
        return False

    # Keep the canonical snapshot marker if it cannot be removed. Callers
    # must then report restoration as incomplete so a later retry remains
    # possible instead of claiming that recovery state has been cleared.
    if not _try_unlink(state_file):
        return False

    try:
        state_dir.rmdir()
    except OSError:
        pass

    return True
