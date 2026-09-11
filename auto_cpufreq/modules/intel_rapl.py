import json
import os
import tempfile
from configparser import ConfigParser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery


MICROWATTS_PER_WATT = Decimal("1000000")
INTEL_POWER_SECTION = "intel_power"
ENABLE_RAPL_OPTION = "enable_rapl_envelopes"
LONG_TERM_OPTION = "rapl_package_long_term_w"
SHORT_TERM_OPTION = "rapl_package_short_term_w"
SUPPORTED_CONTROL_TYPES = frozenset({"intel-rapl", "intel-rapl-mmio"})
SUPPORTED_PACKAGE_CONSTRAINTS = frozenset({"long_term", "short_term"})
RAPL_STATE_VERSION = 1
DEFAULT_RAPL_STATE_PATH = Path("/run/auto-cpufreq/power-state.json")


class RaplConfigError(ValueError):
    """Raised when opt-in Intel RAPL configuration is invalid."""


class RaplStateError(RuntimeError):
    """Raised when RAPL ownership state cannot be trusted or persisted."""


@dataclass(frozen=True)
class RaplEnvelopeTargets:
    long_term_uw: Optional[int] = None
    short_term_uw: Optional[int] = None


@dataclass(frozen=True)
class RaplPolicyConfig:
    enabled: bool
    targets: RaplEnvelopeTargets


@dataclass(frozen=True)
class RaplConstraintRef:
    control_type: str
    zone_id: str
    zone_name: str
    constraint_index: int
    constraint_name: str
    power_limit_path: Path
    min_power_path: Path
    max_power_path: Path


@dataclass(frozen=True)
class RaplConstraintIdentity:
    control_type: str
    zone_id: str
    zone_name: str
    constraint_index: int
    constraint_name: str


@dataclass(frozen=True)
class RaplOwnershipRecord:
    identity: RaplConstraintIdentity
    original_power_limit_uw: int
    last_written_power_limit_uw: int


def _watts_to_microwatts(raw: str, option: str) -> int:
    try:
        watts = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise RaplConfigError(f"Invalid value for '{option}': {raw}") from exc

    if not watts.is_finite() or watts <= 0:
        raise RaplConfigError(f"Invalid value for '{option}': {raw}")

    microwatts = watts * MICROWATTS_PER_WATT
    if microwatts != microwatts.to_integral_value():
        raise RaplConfigError(
            f"'{option}' has precision below one microwatt: {raw}"
        )

    return int(microwatts)


def _rapl_enabled(conf: ConfigParser) -> bool:
    if not conf.has_option(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION):
        return False

    try:
        return conf.getboolean(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION)
    except ValueError as exc:
        raw = conf.get(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION, fallback="")
        raise RaplConfigError(
            f"Invalid value for '{ENABLE_RAPL_OPTION}': {raw}"
        ) from exc


def _optional_target(
    conf: ConfigParser,
    profile: str,
    option: str,
) -> Optional[int]:
    if not conf.has_option(profile, option):
        return None
    return _watts_to_microwatts(conf.get(profile, option), option)


def parse_rapl_policy_config(
    conf: ConfigParser,
    profile: str,
) -> RaplPolicyConfig:
    """Parse opt-in package RAPL limits for one power-source profile.

    RAPL targets are intentionally ignored while the feature is disabled so
    existing or example configuration cannot cause validation errors, much
    less hardware writes, without the explicit global opt-in.
    """
    enabled = _rapl_enabled(conf)
    if not enabled:
        return RaplPolicyConfig(
            enabled=False,
            targets=RaplEnvelopeTargets(),
        )

    return RaplPolicyConfig(
        enabled=True,
        targets=RaplEnvelopeTargets(
            long_term_uw=_optional_target(conf, profile, LONG_TERM_OPTION),
            short_term_uw=_optional_target(conf, profile, SHORT_TERM_OPTION),
        ),
    )


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _require_nonempty_string(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RaplStateError(f"Invalid RAPL state field '{field}'")
    return value


def _require_int(value, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RaplStateError(f"Invalid RAPL state field '{field}'")
    return value


def _identity_from_json(raw) -> RaplConstraintIdentity:
    if not isinstance(raw, dict):
        raise RaplStateError("Invalid RAPL constraint identity")

    expected = {
        "control_type",
        "zone_id",
        "zone_name",
        "constraint_index",
        "constraint_name",
    }
    if set(raw) != expected:
        raise RaplStateError("Unexpected fields in RAPL constraint identity")

    return RaplConstraintIdentity(
        control_type=_require_nonempty_string(raw["control_type"], "control_type"),
        zone_id=_require_nonempty_string(raw["zone_id"], "zone_id"),
        zone_name=_require_nonempty_string(raw["zone_name"], "zone_name"),
        constraint_index=_require_int(
            raw["constraint_index"],
            "constraint_index",
            minimum=0,
        ),
        constraint_name=_require_nonempty_string(
            raw["constraint_name"],
            "constraint_name",
        ),
    )


def _record_from_json(raw) -> RaplOwnershipRecord:
    if not isinstance(raw, dict):
        raise RaplStateError("Invalid RAPL ownership record")

    expected = {
        "identity",
        "original_power_limit_uw",
        "last_written_power_limit_uw",
    }
    if set(raw) != expected:
        raise RaplStateError("Unexpected fields in RAPL ownership record")

    return RaplOwnershipRecord(
        identity=_identity_from_json(raw["identity"]),
        original_power_limit_uw=_require_int(
            raw["original_power_limit_uw"],
            "original_power_limit_uw",
            minimum=1,
        ),
        last_written_power_limit_uw=_require_int(
            raw["last_written_power_limit_uw"],
            "last_written_power_limit_uw",
            minimum=1,
        ),
    )


def _record_to_json(record: RaplOwnershipRecord) -> dict:
    if not isinstance(record, RaplOwnershipRecord):
        raise RaplStateError("Invalid RAPL ownership record")

    # Reuse the load validators so programmatically-created state receives the
    # same strict validation as state read back from disk.
    return {
        "identity": {
            "control_type": _require_nonempty_string(
                record.identity.control_type,
                "control_type",
            ),
            "zone_id": _require_nonempty_string(record.identity.zone_id, "zone_id"),
            "zone_name": _require_nonempty_string(
                record.identity.zone_name,
                "zone_name",
            ),
            "constraint_index": _require_int(
                record.identity.constraint_index,
                "constraint_index",
                minimum=0,
            ),
            "constraint_name": _require_nonempty_string(
                record.identity.constraint_name,
                "constraint_name",
            ),
        },
        "original_power_limit_uw": _require_int(
            record.original_power_limit_uw,
            "original_power_limit_uw",
            minimum=1,
        ),
        "last_written_power_limit_uw": _require_int(
            record.last_written_power_limit_uw,
            "last_written_power_limit_uw",
            minimum=1,
        ),
    }


class RaplStateStore:
    """Persist RAPL ownership state atomically under a root-only runtime path."""

    def __init__(self, path: Path = DEFAULT_RAPL_STATE_PATH) -> None:
        self.path = Path(path)

    def load(self) -> tuple[RaplOwnershipRecord, ...]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RaplStateError(
                f"Unable to read trusted RAPL ownership state: {exc}"
            ) from exc

        if not isinstance(payload, dict) or set(payload) != {"version", "records"}:
            raise RaplStateError("Invalid RAPL ownership state document")
        if payload["version"] != RAPL_STATE_VERSION:
            raise RaplStateError(
                f"Unsupported RAPL state version: {payload['version']!r}"
            )
        if not isinstance(payload["records"], list):
            raise RaplStateError("Invalid RAPL ownership record list")

        records = tuple(_record_from_json(raw) for raw in payload["records"])
        identities = [record.identity for record in records]
        if len(set(identities)) != len(identities):
            raise RaplStateError("Duplicate RAPL ownership identities")
        return records

    def save(self, records: tuple[RaplOwnershipRecord, ...]) -> None:
        records = tuple(records)
        if not records:
            self.clear()
            return

        identities = [record.identity for record in records]
        if len(set(identities)) != len(identities):
            raise RaplStateError("Duplicate RAPL ownership identities")

        payload = {
            "version": RAPL_STATE_VERSION,
            "records": [_record_to_json(record) for record in records],
        }

        parent = self.path.parent
        temp_path: Optional[Path] = None
        try:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            os.chmod(parent, 0o700)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                dir=str(parent),
            )
            temp_path = Path(temp_name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            self._fsync_parent()
        except (OSError, TypeError, ValueError) as exc:
            raise RaplStateError(f"Unable to persist RAPL ownership state: {exc}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RaplStateError(f"Unable to clear RAPL ownership state: {exc}") from exc
        self._fsync_parent()

    def _fsync_parent(self) -> None:
        try:
            fd = os.open(self.path.parent, os.O_RDONLY)
        except OSError as exc:
            raise RaplStateError(
                f"Unable to open RAPL state directory for sync: {exc}"
            ) from exc
        try:
            os.fsync(fd)
        except OSError as exc:
            raise RaplStateError(
                f"Unable to sync RAPL state directory: {exc}"
            ) from exc
        finally:
            os.close(fd)


class IntelRaplController:
    """Discover package RAPL controls without changing hardware state."""

    def __init__(self, discovery: Optional[IntelPowerDiscovery] = None) -> None:
        self.discovery = discovery or IntelPowerDiscovery()

    def discover_package_constraints(self) -> tuple[RaplConstraintRef, ...]:
        refs: list[RaplConstraintRef] = []

        for zone_path, control_type in self.discovery.powercap_zone_paths():
            if control_type not in SUPPORTED_CONTROL_TYPES:
                continue

            zone_name = _read_text(zone_path / "name")
            if zone_name is None or not zone_name.startswith("package-"):
                continue

            try:
                zone_id = str(zone_path.relative_to(self.discovery.powercap_root))
            except ValueError:
                continue

            try:
                name_paths = sorted(
                    zone_path.glob("constraint_*_name"),
                    key=lambda path: path.name,
                )
            except OSError:
                continue

            for name_path in name_paths:
                suffix = name_path.name.removeprefix("constraint_").removesuffix(
                    "_name"
                )
                if not suffix.isdigit():
                    continue

                constraint_name = _read_text(name_path)
                if constraint_name not in SUPPORTED_PACKAGE_CONSTRAINTS:
                    continue

                index = int(suffix)
                power_limit_path = zone_path / f"constraint_{index}_power_limit_uw"
                try:
                    has_power_limit = power_limit_path.is_file()
                except OSError:
                    has_power_limit = False
                if not has_power_limit:
                    continue

                refs.append(
                    RaplConstraintRef(
                        control_type=control_type,
                        zone_id=zone_id,
                        zone_name=zone_name,
                        constraint_index=index,
                        constraint_name=constraint_name,
                        power_limit_path=power_limit_path,
                        min_power_path=zone_path / f"constraint_{index}_min_power_uw",
                        max_power_path=zone_path / f"constraint_{index}_max_power_uw",
                    )
                )

        return tuple(
            sorted(
                refs,
                key=lambda ref: (
                    ref.control_type,
                    ref.zone_id,
                    ref.constraint_index,
                ),
            )
        )
