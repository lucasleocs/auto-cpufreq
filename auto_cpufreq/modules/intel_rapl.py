import fcntl
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery
from auto_cpufreq.modules.rapl_capabilities import _package_zones
from auto_cpufreq.modules.rapl_policy import RaplPolicyError, parse_rapl_configuration


SUPPORTED_CONTROL_TYPES = frozenset({"intel-rapl", "intel-rapl-mmio"})
SUPPORTED_PACKAGE_CONSTRAINTS = frozenset({"long_term", "short_term"})
RAPL_STATE_VERSION = 1
DEFAULT_RAPL_STATE_PATH = Path("/run/auto-cpufreq-rapl/power-state.json")


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
    # Persist a write intention BEFORE mutating Powercap. On an interrupted
    # readback, the helper must be able to distinguish pending from owned.
    pending_target_uw: Optional[int] = None


@dataclass(frozen=True)
class RaplApplyResult:
    identity: RaplConstraintIdentity
    action: str
    before_uw: Optional[int]
    requested_uw: Optional[int]
    effective_uw: Optional[int]
    message: str


def parse_rapl_policy_config(
    conf: ConfigParser,
    profile: str,
) -> RaplPolicyConfig:
    """Compatibility adapter around the single validated RAPL parser.

    Keep this old public entry point while preventing divergent validation
    of selectors, decimal limits and opt-in between daemon and writer.
    """
    try:
        parsed = parse_rapl_configuration(conf, profile)
    except RaplPolicyError as exc:
        raise RaplConfigError(str(exc)) from exc
    return RaplPolicyConfig(
        enabled=parsed.enabled,
        targets=RaplEnvelopeTargets(
            long_term_uw=parsed.targets.long_term_uw,
            short_term_uw=parsed.targets.short_term_uw,
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
    if not expected.issubset(raw) or set(raw) - expected - {"pending_target_uw"}:
        raise RaplStateError("Unexpected fields in RAPL ownership record")

    pending_target = raw.get("pending_target_uw")
    if pending_target is not None:
        pending_target = _require_int(pending_target, "pending_target_uw", minimum=1)

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
        pending_target_uw=pending_target,
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
        "pending_target_uw": (
            _require_int(record.pending_target_uw, "pending_target_uw", minimum=1)
            if record.pending_target_uw is not None else None
        ),
    }


class RaplStateStore:
    """Persist RAPL ownership state atomically under a root-only runtime path."""

    def __init__(self, path: Path = DEFAULT_RAPL_STATE_PATH) -> None:
        self.path = Path(path)

    def _private_parent(self) -> Path:
        """Require a private, owned, real runtime directory.

        Do not chmod an unexpected pre-existing directory. In production
        systemd creates /run/auto-cpufreq with RuntimeDirectoryMode=0700.
        """
        parent = self.path.parent
        try:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            info = parent.lstat()
        except OSError as exc:
            raise RaplStateError(f"Unable to inspect RAPL state directory: {exc}") from exc
        if (not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid()
                or (info.st_mode & 0o077)):
            raise RaplStateError(
                "RAPL state directory must be a private directory owned "
                "by the service account"
            )
        return parent

    @staticmethod
    def _validate_private_file(fd: int, kind: str) -> None:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or (info.st_mode & 0o077)):
            raise RaplStateError(
                f"Untrusted RAPL {kind}: expected a private, owned regular file"
            )

    @contextmanager
    def locked(self):
        """Cooperatively serialize local journal and Powercap operations."""
        self._private_parent()
        try:
            flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
            fd = os.open(str(self.path) + ".lock", flags, 0o600)
        except OSError as exc:
            raise RaplStateError(f"Unable to open RAPL state lock: {exc}") from exc
        try:
            self._validate_private_file(fd, "lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RaplStateError(
                    f"Unable to acquire exclusive RAPL lock: {exc}"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def load(self) -> tuple[RaplOwnershipRecord, ...]:
        fd = -1
        try:
            # O_NOFOLLOW protects against untrusted journal aliases;
            # O_NONBLOCK avoids blocking on a substituted FIFO.
            fd = os.open(
                self.path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            self._validate_private_file(fd, "ownership journal")
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                payload = json.load(handle)
        except FileNotFoundError:
            return ()
        except RaplStateError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RaplStateError(
                f"Unable to read trusted RAPL ownership state: {exc}"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)

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

        parent = self._private_parent()
        temp_path: Optional[Path] = None
        try:
            if self.path.is_symlink():
                raise RaplStateError("RAPL journal is a symbolic link")
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
        self._private_parent()
        try:
            if self.path.is_symlink():
                raise RaplStateError("RAPL journal is a symbolic link")
            self.path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise RaplStateError(f"Unable to clear RAPL ownership state: {exc}") from exc
        self._fsync_parent()

    def _fsync_parent(self) -> None:
        self._private_parent()
        try:
            fd = os.open(
                self.path.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
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
        # This must live across daemon policy cycles (cache controller in core).
        self._yielded_external: set[RaplConstraintIdentity] = set()

    def _zone_write_allowed(self, ref: RaplConstraintRef) -> bool:
        """Do not attempt writes to explicitly disabled/unreadable enabled nodes."""
        for path in (
            self.discovery.powercap_root / ref.control_type / "enabled",
            ref.power_limit_path.parent / "enabled",
        ):
            # A dangling symlink or unreadable attribute is not the same
            # as an optional attribute omitted by the kernel.
            if (path.exists() or path.is_symlink()) and _read_text(path) != "1":
                return False
        return True

    def discover_package_constraints(self) -> tuple[RaplConstraintRef, ...]:
        refs: list[RaplConstraintRef] = []

        # This is the exact physical package-zone discovery used by the
        # --stats/GTK reader. Generic intel_power discovery also traverses
        # subzones and may label direct class aliases with control_type=None;
        # neither behavior is appropriate for a package-cap writer.
        for control_type in sorted(SUPPORTED_CONTROL_TYPES):
            for zone_path, _control_enabled in _package_zones(
                self.discovery.powercap_root, control_type
            ):
                refs.extend(self._refs_for_package_zone(zone_path, control_type))

        return tuple(
            sorted(refs, key=lambda ref: (
                ref.control_type, ref.zone_id, ref.constraint_index,
            ))
        )

    def _refs_for_package_zone(
        self, zone_path: Path, control_type: str
    ) -> tuple[RaplConstraintRef, ...]:
        refs: list[RaplConstraintRef] = []
        zone_name = _read_text(zone_path / "name")
        if zone_name is None or re.fullmatch(r"package-[0-9]+", zone_name) is None:
            return ()

        # The class alias and canonical directory name refer to the same
        # kernel package. Keep an alias-invariant, same-boot journal identity.
        zone_id = zone_path.name
        try:
            name_paths = sorted(
                zone_path.glob("constraint_*_name"),
                key=lambda path: path.name,
            )
        except OSError:
            return ()

        for name_path in name_paths:
            if name_path.is_symlink():
                # A real Powercap constraint is a sysfs attribute, not an
                # arbitrary symlink to a writable file outside the zone.
                continue
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
                has_power_limit = (
                    power_limit_path.is_file()
                    and not power_limit_path.is_symlink()
                )
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
        return tuple(refs)

    def apply(
        self,
        targets: RaplEnvelopeTargets,
        *,
        control_type: Optional[str] = None,
        package: Optional[str] = None,
    ) -> tuple[RaplApplyResult, ...]:
        with self.state_store.locked():
            return self._apply_locked(targets, control_type, package)

    def _apply_locked(
        self,
        targets: RaplEnvelopeTargets,
        control_type: Optional[str],
        package: Optional[str],
    ) -> tuple[RaplApplyResult, ...]:
        ownership = {
            record.identity: record
            for record in self.state_store.load()
        }
        discovered = self.discover_package_constraints()
        controls = {ref.control_type for ref in discovered}
        if not controls:
            return ()
        if control_type is None:
            if len(controls) != 1:
                raise RaplStateError(
                    "Ambiguous Intel RAPL control types; select MSR or MMIO explicitly"
                )
            control_type = next(iter(controls))
        elif control_type not in controls:
            raise RaplStateError(
                f"Selected Intel RAPL control type is not exposed: {control_type}"
            )

        relevant = tuple(
            ref for ref in discovered if ref.control_type == control_type
        )
        packages = {ref.zone_name for ref in relevant}
        if not packages:
            return ()
        if package is None:
            if len(packages) != 1:
                raise RaplStateError(
                    "Multiple Intel RAPL packages; select an exact package"
                )
            package = next(iter(packages))
        elif package not in packages:
            raise RaplStateError(
                f"Selected Intel RAPL package is not exposed: {package}"
            )

        # A package name is the user's selector, not a guarantee that
        # there is only one physical Powercap zone with that name. Reject
        # duplicated package names and duplicate requested constraint names
        # BEFORE any sysfs write or ownership-journal mutation.
        selected_refs = tuple(
            ref for ref in relevant if ref.zone_name == package
        )
        if len({ref.zone_id for ref in selected_refs}) != 1:
            raise RaplStateError(
                "Multiple Powercap zones expose the selected package name"
            )
        selected_names = [
            ref.constraint_name for ref in selected_refs
            if self._target_for(ref, targets) is not None
            or self._identity(ref) in ownership
        ]
        if len(selected_names) != len(set(selected_names)):
            raise RaplStateError(
                "Duplicate named Powercap constraints in selected package"
            )

        # Switching selection while holding an old/unfinished transaction
        # could strand its restore state. Require explicit reconciliation.
        if any(
            identity.control_type != control_type
            or identity.zone_name != package
            for identity in ownership
        ):
            raise RaplStateError(
                "RAPL ownership exists outside selected scope; restore it first"
            )

        results: list[RaplApplyResult] = []
        for ref in relevant:
            if ref.zone_name != package:
                continue
            identity = self._identity(ref)
            target = self._target_for(ref, targets)
            record = ownership.get(identity)
            current = _read_int(ref.power_limit_path)

            if identity in self._yielded_external:
                results.append(RaplApplyResult(
                    identity, "yielded-external", current, target, current,
                    "RAPL control suspended after external interference",
                ))
                continue
            if not self._zone_write_allowed(ref):
                results.append(RaplApplyResult(
                    identity, "skipped-zone-disabled", current, target, current,
                    "RAPL zone or control is disabled or unavailable",
                ))
                continue

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
        with self.state_store.locked():
            return self._restore_owned_locked()

    def _restore_owned_locked(self) -> tuple[RaplApplyResult, ...]:
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

            if current == record.original_power_limit_uw:
                results.append(self._restore(ref, record, current, ownership))
                continue

            if record.pending_target_uw is not None:
                if current in (
                    record.last_written_power_limit_uw,
                    record.pending_target_uw,
                ):
                    # The value is either still the previously owned limit or
                    # exactly the in-flight requested limit. Both are safe to
                    # restore to the recorded external baseline.
                    results.append(self._restore(ref, record, current, ownership))
                else:
                    results.append(RaplApplyResult(
                        identity, "restore-failed", current,
                        record.original_power_limit_uw, current,
                        "Unresolved pending RAPL write; external state is ambiguous",
                    ))
                continue

            if current != record.last_written_power_limit_uw:
                self._yielded_external.add(identity)
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
        if max_uw is not None and max_uw > 0 and target > max_uw:
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

        # Write-ahead intent: loss of the process after the hardware write must
        # not erase the original externally supplied limit.
        pending = RaplOwnershipRecord(
            identity=identity,
            original_power_limit_uw=current,
            last_written_power_limit_uw=current,
            pending_target_uw=target,
        )
        ownership[identity] = pending
        try:
            self._save_ownership(ownership)
        except RaplStateError:
            ownership.pop(identity, None)
            raise

        try:
            _write_int(ref.power_limit_path, target)
        except OSError as exc:
            effective = _read_int(ref.power_limit_path)
            if effective == current:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
            # If the write may have changed hardware, keep pending metadata.
            return RaplApplyResult(
                identity, "write-failed", current, target, effective,
                f"RAPL write failed; recovery intent retained if necessary: {exc}",
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
            ownership.pop(identity, None)
            self._save_ownership(ownership)
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
            # The saved on-disk record remains pending. Do not discard it after
            # a possible hardware write; a subsequent restore pass needs it.
            ownership[identity] = pending
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

        if record.pending_target_uw is not None:
            if current in (
                record.last_written_power_limit_uw,
                record.pending_target_uw,
            ):
                # Previous cycle interrupted during a write. Resolve the old
                # envelope first; never stack a new write onto uncertainty.
                return self._restore(ref, record, current, ownership)
            return RaplApplyResult(
                identity, "pending-unresolved", current, target, current,
                "Pending RAPL transaction differs from observed hardware",
            )

        if current != record.last_written_power_limit_uw:
            self._yielded_external.add(identity)
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

        pending = RaplOwnershipRecord(
            identity=identity,
            original_power_limit_uw=record.original_power_limit_uw,
            last_written_power_limit_uw=current,
            pending_target_uw=target,
        )
        ownership[identity] = pending
        try:
            self._save_ownership(ownership)
        except RaplStateError:
            ownership[identity] = record
            raise

        try:
            _write_int(ref.power_limit_path, target)
        except OSError as exc:
            effective = _read_int(ref.power_limit_path)
            if effective == current:
                ownership[identity] = record
                self._save_ownership(ownership)
            return RaplApplyResult(
                identity, "write-failed", current, target, effective,
                f"Owned RAPL write failed; recovery intent retained: {exc}",
            )

        effective = _read_int(ref.power_limit_path)
        if effective is None:
            return RaplApplyResult(
                identity,
                "readback-failed",
                current,
                target,
                None,
                "Owned RAPL write not verifiable; pending recovery remains saved",
            )

        if effective == current:
            ownership[identity] = record
            self._save_ownership(ownership)
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
            # The old durable record still contains the pending transaction.
            ownership[identity] = pending
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
        # A readback above the external baseline (or an invalid zero)
        # cannot be attributed to this process. In particular, it could
        # already be a firmware/thermal-daemon update. Never overwrite it
        # to "recover", and never promote it to last_written ownership.
        #
        # _acquire/_apply_owned persisted a pending write-ahead record
        # before calling _write_int. Keep that record so cleanup reports
        # the unresolved state and a later apply cannot silently reacquire.
        record = ownership.get(identity)
        if record is None or record.pending_target_uw is None:
            raise RaplStateError(
                "Unsafe RAPL readback without a durable pending write"
            )
        self._yielded_external.add(identity)
        return RaplApplyResult(
            identity,
            "unsafe-readback-unresolved",
            before,
            requested,
            unsafe_effective,
            "Unexpected RAPL readback: preserved recovery intent and "
            "yielded to external control without overwriting the value",
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

        if current == original:
            # Firmware may already have restored the baseline; a redundant
            # write can fail or race with a new platform power policy.
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity, "already-restored", current, original, current,
                "Original RAPL value already observed; no sysfs write needed",
            )

        # Persist the intended *restore* before touching hardware. The
        # observed 'current' is an accepted previously owned or in-flight
        # value, as checked by the caller. If restoration fails after a
        # partial write, the pending intent survives a process crash.
        pending = RaplOwnershipRecord(
            identity=identity,
            original_power_limit_uw=original,
            last_written_power_limit_uw=current,
            pending_target_uw=original,
        )
        ownership[identity] = pending
        self._save_ownership(ownership)

        try:
            _write_int(ref.power_limit_path, original)
        except OSError as exc:
            effective = _read_int(ref.power_limit_path)
            if effective == original:
                ownership.pop(identity, None)
                self._save_ownership(ownership)
                return RaplApplyResult(
                    identity, "restored", current, original, effective,
                    "Original RAPL limit confirmed despite a write error",
                )
            # Never delete the journal if the baseline was not confirmed.
            # Also do not assign an unexpected value to last_written.
            return RaplApplyResult(
                identity,
                "restore-failed",
                current,
                original,
                effective,
                f"Unconfirmed RAPL restoration; pending intent retained: {exc}",
            )

        effective = _read_int(ref.power_limit_path)
        if effective == original:
            ownership.pop(identity, None)
            self._save_ownership(ownership)
            return RaplApplyResult(
                identity, "restored", current, original, effective,
                "Original RAPL limit restored and ownership retired",
            )

        # Even a successful write may be rejected, rounded, or raced by
        # firmware. Do not claim an unverified readback as self-owned.
        return RaplApplyResult(
            identity,
            "restore-failed",
            current,
            original,
            effective,
            "Original RAPL baseline not confirmed; pending intent retained",
        )

    @staticmethod
    def _best_effort_rollback(ref: RaplConstraintRef, value: int) -> None:
        try:
            _write_int(ref.power_limit_path, value)
        except OSError:
            return
