# Persistent recovery state for host power-management changes.
#
# The first pre-install snapshot remains authoritative until restoration
# completes: never replace it with a later partially modified state, and keep it
# on incomplete restoration so removal can retry. Bluetooth restoration is
# deliberately conservative and must preserve later user or system changes.

import json
import os
import stat
from pathlib import Path
from shutil import copystat, which
from subprocess import run
from uuid import uuid4

from auto_cpufreq.systemd import SystemdQueryError, query_unit_properties


DEFAULT_STATE_DIR = Path("/var/lib/auto-cpufreq")
DEFAULT_BLUETOOTH_CONFIG = Path("/etc/bluetooth/main.conf")
DEFAULT_INIT_COMM = Path("/proc/1/comm")
STATE_FILE_NAME = "power-state.json"
# Restore TuneD before tuned-ppd because the compatibility daemon requires
# tuned.service. Snapshot capture uses the same stable service list.
POWER_SERVICES = (
    "power-profiles-daemon.service",
    "tuned.service",
    "tuned-ppd.service",
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
_TRANSACTION_BOOL_FIELDS = {
    "bluetooth_managed",
    "cpufreqctl_preexisting",
    "power_service_enablement_preserved",
}
_SNAPSHOT_VERSION = 2


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
    required = ("LoadState", "ActiveState", "UnitFileState", "FragmentPath")
    try:
        properties = query_unit_properties(
            unit,
            required,
            systemctl=systemctl,
        )
    except SystemdQueryError:
        return None

    if properties is None:
        return _missing_service_state()

    return {
        "load_state": properties["LoadState"],
        "active_state": properties["ActiveState"],
        "unit_file_state": properties["UnitFileState"],
        "fragment_path": properties["FragmentPath"],
    }


def _systemctl(systemctl: str, *args: str) -> bool:
    result = _run([systemctl, *args])
    return result is not None and result.returncode == 0


def restore_service_state(
    unit: str,
    state,
    *,
    systemctl: str = "systemctl",
    enablement_links_preserved: bool = False,
) -> bool:
    if not state or state.get("load_state") == "not-found":
        return True

    success = _systemctl(systemctl, "unmask", unit)
    unit_file_state = state.get("unit_file_state", "")
    fragment_path = state.get("fragment_path", "")

    if not enablement_links_preserved:
        # Version 1 installs used `systemctl disable`; retain the best-effort
        # reconstruction needed by those already-published recovery snapshots.
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


def _service_active(services, unit: str) -> bool:
    state = services.get(unit)
    return (
        state is not None
        and state.get("active_state") in _ACTIVE_STATES
    )


def _state_path(state_dir: Path):
    return state_dir / STATE_FILE_NAME


def _open_state_directory(state_dir: Path, *, create: bool = False):
    state_dir = Path(state_dir)
    if create:
        try:
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            return None

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(state_dir, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0:
            os.close(descriptor)
            return None
        # Tighten a legacy root-owned directory before trusting files inside it.
        os.fchmod(descriptor, 0o700)
        return descriptor
    except OSError:
        return None


def _load_snapshot(state_dir: Path):
    directory = _open_state_directory(state_dir)
    if directory is None:
        return None

    descriptor = None
    try:
        descriptor = os.open(
            STATE_FILE_NAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            return None
        identity = metadata.st_dev, metadata.st_ino
        with os.fdopen(descriptor) as handle:
            descriptor = None
            return json.load(handle), identity
    except (OSError, ValueError, TypeError, UnicodeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        os.close(directory)


def _remove_snapshot(state_dir: Path, identity) -> bool:
    directory = _open_state_directory(state_dir)
    if directory is None:
        return False
    try:
        try:
            current = os.stat(
                STATE_FILE_NAME,
                dir_fd=directory,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return True
        if (current.st_dev, current.st_ino) != identity:
            return False
        os.unlink(STATE_FILE_NAME, dir_fd=directory)
        os.fsync(directory)
        return True
    except OSError:
        return False
    finally:
        os.close(directory)


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


def _atomic_write_text_preserving_metadata(path: Path, content: str) -> bool:
    path = Path(path)
    try:
        target = path.resolve(strict=True)
        metadata = target.stat()
    except OSError:
        return False

    temporary = target.parent / (
        f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        copystat(target, temporary)
        with os.fdopen(descriptor, "w") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory = os.open(
            target.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, UnicodeError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _try_unlink(temporary)

    return True


def _restore_bluetooth_state(bluetooth_config: Path, original_state) -> bool:
    if not original_state.get("config_present"):
        return True

    current_state = _capture_bluetooth_state(bluetooth_config)
    if current_state is None:
        return False

    # Restore only while AutoEnable still matches the value managed by
    # auto-cpufreq. If the file disappeared or AutoEnable was changed later,
    # preserve that newer user/system state. Unrelated config edits are kept.
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

    return _atomic_write_text_preserving_metadata(
        bluetooth_config,
        "".join(new_lines),
    )


def _valid_transaction_state(state) -> bool:
    if not isinstance(state, dict):
        return False
    return all(
        key in _TRANSACTION_BOOL_FIELDS and isinstance(value, bool)
        for key, value in state.items()
    )


def _valid_snapshot(snapshot) -> bool:
    if not isinstance(snapshot, dict):
        return False
    version = snapshot.get("version")
    if version not in (1, _SNAPSHOT_VERSION):
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

    transaction = snapshot.get("transaction", {})
    if not _valid_transaction_state(transaction):
        return False
    if version == _SNAPSHOT_VERSION and set(transaction) != _TRANSACTION_BOOL_FIELDS:
        return False

    return _valid_bluetooth_state(snapshot.get("bluetooth"))


def power_state_exists(*, state_dir: Path = DEFAULT_STATE_DIR) -> bool:
    state_file = _state_path(Path(state_dir))
    return state_file.exists() or state_file.is_symlink()


def get_power_state_transaction(*, state_dir: Path = DEFAULT_STATE_DIR):
    loaded = _load_snapshot(Path(state_dir))
    if loaded is None:
        return None
    snapshot, _ = loaded
    if not _valid_snapshot(snapshot):
        return None
    return dict(snapshot.get("transaction", {}))


def save_power_state(
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    bluetooth_config: Path = DEFAULT_BLUETOOTH_CONFIG,
    systemctl: str = "systemctl",
    powerprofilesctl: str = "powerprofilesctl",
    init_comm: Path = DEFAULT_INIT_COMM,
    transaction=None,
) -> bool:
    state_dir = Path(state_dir)
    bluetooth_config = Path(bluetooth_config)
    transaction = {} if transaction is None else transaction

    if (
        not _valid_transaction_state(transaction)
        or set(transaction) != _TRANSACTION_BOOL_FIELDS
    ):
        return False

    # Never replace the original pre-install snapshot with a later state.
    if power_state_exists(state_dir=state_dir):
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

        ppd_active = _service_active(
            services,
            "power-profiles-daemon.service",
        )
        tuned_ppd_active = _service_active(
            services,
            "tuned-ppd.service",
        )

        if ppd_active:
            power_profiles_profile = _capture_power_profiles_profile(
                powerprofilesctl
            )
            if power_profiles_profile is None:
                return False
        elif tuned_ppd_active and _command_exists(powerprofilesctl):
            # tuned-ppd exposes the PPD API but does not itself provide the
            # powerprofilesctl client. Capture the API profile when a compatible
            # client is present; TuneD's own persisted profile remains the
            # recovery source when it is not.
            power_profiles_profile = _capture_power_profiles_profile(
                powerprofilesctl
            )

    bluetooth_state = _capture_bluetooth_state(bluetooth_config)
    if bluetooth_state is None:
        return False

    directory = _open_state_directory(state_dir, create=True)
    if directory is None:
        return False

    snapshot = {
        "version": _SNAPSHOT_VERSION,
        "services": services,
        "power_profiles_profile": power_profiles_profile,
        "bluetooth": bluetooth_state,
        "transaction": dict(transaction),
    }

    # Publish through a unique file in the same directory. link() creates
    # the canonical snapshot name atomically and refuses to replace a snapshot
    # that another installer created after the pre-check above.
    temporary = f".{STATE_FILE_NAME}.{os.getpid()}.{uuid4().hex}.tmp"
    descriptor = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            return False
        os.fchmod(descriptor, 0o600)
        content = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
        with os.fdopen(descriptor, "w") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(
            temporary,
            STATE_FILE_NAME,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )
        os.fsync(directory)
    except (OSError, UnicodeError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass
        os.close(directory)

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
    loaded = _load_snapshot(state_dir)
    if loaded is None:
        return False
    snapshot, snapshot_identity = loaded

    if not _valid_snapshot(snapshot):
        return False

    success = True
    services = snapshot["services"]
    transaction = snapshot.get("transaction", {})
    enablement_links_preserved = (
        snapshot["version"] == _SNAPSHOT_VERSION
        and transaction["power_service_enablement_preserved"]
    )
    if services:
        if not _command_exists(systemctl):
            success = False
        else:
            # Do not rely on JSON key order: tuned-ppd requires tuned.service,
            # so the backend must be restored before its PPD compatibility API.
            for unit in POWER_SERVICES:
                state = services.get(unit)
                if state is None:
                    continue
                if not restore_service_state(
                    unit,
                    state,
                    systemctl=systemctl,
                    enablement_links_preserved=enablement_links_preserved,
                ):
                    success = False

    # Published version 1 snapshots predate transaction metadata and always
    # managed Bluetooth. Honor an intermediate v1 flag when it is present.
    bluetooth_was_managed = transaction.get("bluetooth_managed")
    if snapshot["version"] == 1 and bluetooth_was_managed is None:
        bluetooth_was_managed = True
    if bluetooth_was_managed is True:
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
    if not _remove_snapshot(state_dir, snapshot_identity):
        return False

    try:
        state_dir.rmdir()
    except OSError:
        pass

    return True
