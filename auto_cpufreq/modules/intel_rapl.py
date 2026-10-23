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


@dataclass(frozen=True)
class RaplApplyResult:
    identity: RaplConstraintIdentity
    action: str
    before_uw: Optional[int]
    requested_uw: Optional[int]
    effective_uw: Optional[int]
    message: str


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


def _read_int(path: Path) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _write_int(path: Path, value: int) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(str(value))


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
    if not isinstance(record.identity, RaplConstraintIdentity):
        raise RaplStateError("Invalid RAPL constraint identity")

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
        if isinstance(payload["version"], bool) or payload["version"] != RAPL_STATE_VERSION:
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

        for record in records:
            _record_to_json(record)
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
    """Discover and conservatively control named package RAPL constraints."""

    def __init__(
        self,
        discovery: Optional[IntelPowerDiscovery] = None,
        state_store: Optional[RaplStateStore] = None,
    ) -> None:
        self.discovery = discovery or IntelPowerDiscovery()
        self.state_store = state_store if state_store is not None else RaplStateStore()

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

    def apply(self, targets: RaplEnvelopeTargets) -> tuple[RaplApplyResult, ...]:
        ownership = {
            record.identity: record
            for record in self.state_store.load()
        }
        results: list[RaplApplyResult] = []

        for ref in self.discover_package_constraints():
            identity = self._identity(ref)
            target = self._target_for(ref, targets)
            record = ownership.get(identity)
            current = _read_int(ref.power_limit_path)

            if record is not None:
                results.append(
                    self._apply_owned(ref, record, current, target, ownership)
                )
            elif target is not None:
                results.append(
                    self._acquire(ref, current, target, ownership)
                )

        return tuple(results)

    def restore_owned(self) -> tuple[RaplApplyResult, ...]:
        ownership = {
            record.identity: record
            for record in self.state_store.load()
        }
        refs = {
            self._identity(ref): ref
            for ref in self.discover_package_constraints()
        }
        results: list[RaplApplyResult] = []

        for identity, record in tuple(ownership.items()):
            ref = refs.get(identity)
            if ref is None:
                results.append(
                    RaplApplyResult(
                        identity=identity,
                        action="restore-failed",
                        before_uw=None,
                        requested_uw=record.original_power_limit_uw,
                        effective_uw=None,
                        message="Owned RAPL constraint is not currently discoverable",
                    )
                )
                continue

            current = _read_int(ref.power_limit_path)
            if current is None:
                results.append(
                    RaplApplyResult(
                        identity=identity,
                        action="restore-failed",
                        before_uw=None,
                        requested_uw=record.original_power_limit_uw,
                        effective_uw=None,
                        message="Owned RAPL constraint is unreadable",
                    )
                )
                continue

            if current != record.last_written_power_limit_uw:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
                results.append(
                    RaplApplyResult(
                        identity=identity,
                        action="retired-drift",
                        before_uw=current,
                        requested_uw=record.original_power_limit_uw,
                        effective_uw=current,
                        message="Ownership lost because the current value drifted",
                    )
                )
                continue

            results.append(self._restore(ref, record, current, ownership))

        return tuple(results)

    @staticmethod
    def _identity(ref: RaplConstraintRef) -> RaplConstraintIdentity:
        return RaplConstraintIdentity(
            control_type=ref.control_type,
            zone_id=ref.zone_id,
            zone_name=ref.zone_name,
            constraint_index=ref.constraint_index,
            constraint_name=ref.constraint_name,
        )

    @staticmethod
    def _target_for(
        ref: RaplConstraintRef,
        targets: RaplEnvelopeTargets,
    ) -> Optional[int]:
        if ref.constraint_name == "long_term":
            return targets.long_term_uw
        if ref.constraint_name == "short_term":
            return targets.short_term_uw
        return None

    @staticmethod
    def _ownership_sort_key(record: RaplOwnershipRecord) -> tuple:
        identity = record.identity
        return (
            identity.control_type,
            identity.zone_id,
            identity.constraint_index,
            identity.constraint_name,
        )

    def _save_ownership(
        self,
        ownership: dict[RaplConstraintIdentity, RaplOwnershipRecord],
    ) -> None:
        self.state_store.save(
            tuple(sorted(ownership.values(), key=self._ownership_sort_key))
        )

    @staticmethod
    def _validation_failure(
        ref: RaplConstraintRef,
        current: int,
        target: int,
    ) -> Optional[tuple[str, str]]:
        min_uw = _read_int(ref.min_power_path)
        if min_uw is not None and min_uw > 0 and target < min_uw:
            return (
                "rejected-min",
                f"Requested value {target} is below kernel-reported minimum {min_uw}",
            )

        max_uw = _read_int(ref.max_power_path)
        if (
            max_uw is not None
            and max_uw > 0
            and current <= max_uw < target
        ):
            return (
                "rejected-max",
                f"Requested value {target} exceeds kernel-reported maximum {max_uw}",
            )
        return None

    def _acquire(
        self,
        ref: RaplConstraintRef,
        current: Optional[int],
        target: int,
        ownership: dict[RaplConstraintIdentity, RaplOwnershipRecord],
    ) -> RaplApplyResult:
        identity = self._identity(ref)

        if current is None or current <= 0:
            return RaplApplyResult(
                identity,
                "unavailable",
                current,
                target,
                current,
                "Current external RAPL limit is unavailable or unusable",
            )

        if target > current:
            return RaplApplyResult(
                identity,
                "skipped-external-limit",
                current,
                target,
                current,
                "First acquisition never raises an external RAPL limit",
            )

        if target == current:
            return RaplApplyResult(
                identity,
                "unchanged-external",
                current,
                target,
                current,
                "Target already equals the external limit; ownership was not claimed",
            )

        validation = self._validation_failure(ref, current, target)
        if validation is not None:
            action, message = validation
            return RaplApplyResult(identity, action, current, target, current, message)

        try:
            _write_int(ref.power_limit_path, target)
        except OSError as exc:
            return RaplApplyResult(
                identity,
                "write-failed",
                current,
                target,
                _read_int(ref.power_limit_path),
                f"RAPL write failed: {exc}",
            )

        effective = _read_int(ref.power_limit_path)
        if effective is None:
            return RaplApplyResult(
                identity,
                "readback-failed",
                current,
                target,
                None,
                "RAPL write could not be verified by readback",
            )

        if effective == current:
            return RaplApplyResult(
                identity,
                "readback-rejected",
                current,
                target,
                effective,
                "RAPL limit did not change after the write",
            )

        if effective <= 0 or effective > current:
            return self._recover_unsafe_readback(
                ref,
                identity,
                original=current,
                before=current,
                requested=target,
                unsafe_effective=effective,
                ownership=ownership,
            )

        record = RaplOwnershipRecord(
            identity=identity,
            original_power_limit_uw=current,
            last_written_power_limit_uw=effective,
        )
        ownership[identity] = record
        try:
            self._save_ownership(ownership)
        except RaplStateError:
            ownership.pop(identity, None)
            self._best_effort_rollback(ref, current)
            raise

        return RaplApplyResult(
            identity,
            "acquired",
            current,
            target,
            effective,
            "RAPL limit lowered and ownership established by verified readback",
        )

    def _apply_owned(
        self,
        ref: RaplConstraintRef,
        record: RaplOwnershipRecord,
        current: Optional[int],
        target: Optional[int],
        ownership: dict[RaplConstraintIdentity, RaplOwnershipRecord],
    ) -> RaplApplyResult:
        identity = record.identity

        if current is None:
            return RaplApplyResult(
                identity,
                "unavailable",
                None,
                target,
                None,
                "Owned RAPL constraint is unreadable; ownership state is retained",
            )

        if current != record.last_written_power_limit_uw:
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "drift",
                current,
                target,
                current,
                "Ownership lost because the current value drifted",
            )

        if target is None:
            return self._restore(ref, record, current, ownership)

        if target > record.original_power_limit_uw:
            return RaplApplyResult(
                identity,
                "skipped-above-original",
                current,
                target,
                current,
                "Owned limit may not exceed its original external baseline",
            )

        if target == current:
            return RaplApplyResult(
                identity,
                "unchanged-owned",
                current,
                target,
                current,
                "Owned RAPL limit already matches the requested target",
            )

        validation = self._validation_failure(ref, current, target)
        if validation is not None:
            action, message = validation
            return RaplApplyResult(identity, action, current, target, current, message)

        try:
            _write_int(ref.power_limit_path, target)
        except OSError as exc:
            effective = _read_int(ref.power_limit_path)
            if effective is not None and effective != current:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "write-failed",
                current,
                target,
                effective,
                f"Owned RAPL write failed: {exc}",
            )

        effective = _read_int(ref.power_limit_path)
        if effective is None:
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "readback-failed",
                current,
                target,
                None,
                "Owned RAPL write could not be verified; ownership was retired",
            )

        if effective == current:
            return RaplApplyResult(
                identity,
                "readback-rejected",
                current,
                target,
                effective,
                "Owned RAPL limit did not change after the write",
            )

        if effective <= 0 or effective > record.original_power_limit_uw:
            return self._recover_unsafe_readback(
                ref,
                identity,
                original=record.original_power_limit_uw,
                before=current,
                requested=target,
                unsafe_effective=effective,
                ownership=ownership,
            )

        updated = RaplOwnershipRecord(
            identity=identity,
            original_power_limit_uw=record.original_power_limit_uw,
            last_written_power_limit_uw=effective,
        )
        ownership[identity] = updated
        try:
            self._save_ownership(ownership)
        except RaplStateError:
            ownership[identity] = record
            self._best_effort_rollback(ref, current)
            raise

        return RaplApplyResult(
            identity,
            "updated",
            current,
            target,
            effective,
            "Owned RAPL limit updated and verified",
        )

    def _recover_unsafe_readback(
        self,
        ref: RaplConstraintRef,
        identity: RaplConstraintIdentity,
        *,
        original: int,
        before: int,
        requested: int,
        unsafe_effective: int,
        ownership: dict[RaplConstraintIdentity, RaplOwnershipRecord],
    ) -> RaplApplyResult:
        try:
            _write_int(ref.power_limit_path, original)
        except OSError as exc:
            restored = _read_int(ref.power_limit_path)
            if restored is not None and restored > 0:
                ownership[identity] = RaplOwnershipRecord(
                    identity=identity,
                    original_power_limit_uw=original,
                    last_written_power_limit_uw=restored,
                )
                self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "unsafe-readback-restore-failed",
                before,
                requested,
                restored,
                f"Unexpected RAPL readback could not be restored: {exc}",
            )

        restored = _read_int(ref.power_limit_path)
        if restored == original:
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "unsafe-readback-restored",
                before,
                requested,
                restored,
                "Unexpected RAPL readback exceeded the safe baseline and was restored",
            )

        if restored is not None and restored > 0:
            ownership[identity] = RaplOwnershipRecord(
                identity=identity,
                original_power_limit_uw=original,
                last_written_power_limit_uw=restored,
            )
            self._save_ownership(ownership)

        return RaplApplyResult(
            identity,
            "unsafe-readback-restore-failed",
            before,
            requested,
            restored,
            "Unexpected RAPL readback was not restored to the original baseline",
        )

    def _restore(
        self,
        ref: RaplConstraintRef,
        record: RaplOwnershipRecord,
        current: int,
        ownership: dict[RaplConstraintIdentity, RaplOwnershipRecord],
    ) -> RaplApplyResult:
        identity = record.identity
        original = record.original_power_limit_uw

        try:
            _write_int(ref.power_limit_path, original)
        except OSError as exc:
            effective = _read_int(ref.power_limit_path)
            if effective == original:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
                return RaplApplyResult(
                    identity,
                    "restored",
                    current,
                    original,
                    effective,
                    "Original RAPL limit was restored despite a reported write error",
                )
            if effective is not None and effective != current:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "restore-failed",
                current,
                original,
                effective,
                f"Original RAPL limit could not be restored: {exc}",
            )

        effective = _read_int(ref.power_limit_path)
        if effective == original:
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity,
                "restored",
                current,
                original,
                effective,
                "Original RAPL limit restored and ownership retired",
            )

        if effective is not None and effective > 0 and effective != current:
            ownership[identity] = RaplOwnershipRecord(
                identity=identity,
                original_power_limit_uw=original,
                last_written_power_limit_uw=effective,
            )
            self._save_ownership(ownership)

        return RaplApplyResult(
            identity,
            "restore-failed",
            current,
            original,
            effective,
            "Original RAPL limit was not confirmed by readback",
        )

    @staticmethod
    def _best_effort_rollback(ref: RaplConstraintRef, value: int) -> None:
        try:
            _write_int(ref.power_limit_path, value)
        except OSError:
            return
