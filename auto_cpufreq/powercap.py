from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from subprocess import DEVNULL, run
from typing import Dict, List, Optional, Tuple


POWERCAP_PATH = Path("/sys/class/powercap")

RAPL_CONFIG_OPTIONS = {
    "rapl_long_term_power_limit_w": "long_term",
    "rapl_short_term_power_limit_w": "short_term",
}

MICROWATTS_PER_WATT = Decimal("1000000")

# Linux Intel RAPL exposes power units in whole microwatts. Most CPUs use
# MICROWATT_PER_WATT >> exponent, while legacy Atom paths use
# (1 << exponent) * MILLIWATT_PER_WATT. Both use a 4-bit exponent.
# Keep both kernel encodings here so quantized readbacks can be validated
# without hardcoding CPU models or generations.
RAPL_POWER_UNITS_UW = tuple(
    sorted(
        {
            int(MICROWATTS_PER_WATT) >> exponent
            for exponent in range(16)
        }
        | {
            1_000 << exponent
            for exponent in range(16)
        }
    )
)

# The daemon reapplies profiles periodically. Cache the requested/effective
# pair after a quantized write so the same limit is not rewritten every cycle.
# If another component changes the live value, the pair stops matching and
# the configured limit is applied again.
_effective_power_limit_cache: Dict[Path, Tuple[int, int]] = {}

# Avoid repeating the same thermald coexistence notice every daemon cycle.
_thermald_skip_notified = False


@dataclass(frozen=True)
class RaplZone:
    path: Path
    constraints: Dict[str, Path]


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> Optional[int]:
    value = _read_text(path)
    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def _write_int(path: Path, value: int) -> bool:
    try:
        path.write_text(f"{value}\n")
    except OSError:
        return False

    return True


def _thermald_is_active() -> bool:
    try:
        return run(
            ["systemctl", "is-active", "--quiet", "thermald.service"],
            stdout=DEVNULL,
            stderr=DEVNULL,
            check=False,
        ).returncode == 0
    except OSError:
        return False


def _zone_is_enabled(path: Path) -> bool:
    enabled_path = path / "enabled"

    if not enabled_path.exists():
        return True

    return _read_text(enabled_path) == "1"


def _powercap_parents_are_enabled(path: Path) -> bool:
    try:
        parent = path.resolve().parent
    except (OSError, RuntimeError):
        return True

    while (parent / "enabled").exists():
        if not _zone_is_enabled(parent):
            return False
        parent = parent.parent

    return True


def _discover_constraints(zone_path: Path) -> Dict[str, Path]:
    constraints = {}

    for name_path in sorted(zone_path.glob("constraint_*_name")):
        constraint_name = _read_text(name_path)
        if not constraint_name:
            continue

        prefix = name_path.name.removesuffix("_name")
        power_limit_path = zone_path / f"{prefix}_power_limit_uw"

        if not power_limit_path.exists():
            continue

        constraints[constraint_name] = power_limit_path

    return constraints


def _discover_rapl_zone_paths(
    powercap_path: Path,
) -> List[Path]:
    try:
        entries = sorted(powercap_path.iterdir())
    except OSError:
        return []

    zone_paths = []
    seen_paths = set()

    def add_zone_path(zone_path: Path) -> None:
        try:
            canonical_path = zone_path.resolve()
        except (OSError, RuntimeError):
            canonical_path = zone_path

        if canonical_path in seen_paths:
            return

        seen_paths.add(canonical_path)
        zone_paths.append(zone_path)

    for entry in entries:
        if not entry.name.startswith("intel-rapl"):
            continue

        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue

        # Some systems expose RAPL zones directly below the powercap
        # directory, while others expose them below an Intel RAPL
        # control-type directory.
        if _read_text(entry / "name") is not None:
            add_zone_path(entry)
            continue

        # A disabled control type disables controls for all child zones.
        if not _zone_is_enabled(entry):
            continue

        try:
            children = sorted(entry.iterdir())
        except OSError:
            continue

        for child in children:
            if not child.name.startswith("intel-rapl"):
                continue

            try:
                if child.is_dir():
                    add_zone_path(child)
            except OSError:
                continue

    return zone_paths


def discover_rapl_package_zones(
    powercap_path: Path = POWERCAP_PATH,
) -> List[RaplZone]:
    zones = []

    for zone_path in _discover_rapl_zone_paths(powercap_path):
        zone_name = _read_text(zone_path / "name")

        if not zone_name or not zone_name.startswith("package-"):
            continue

        if not _zone_is_enabled(zone_path):
            continue

        if not _powercap_parents_are_enabled(zone_path):
            continue

        constraints = _discover_constraints(zone_path)

        if not constraints:
            continue

        zones.append(
            RaplZone(
                path=zone_path,
                constraints=constraints,
            )
        )

    return zones


def _is_valid_rapl_power_readback(
    requested_value: int,
    effective_value: int,
) -> bool:
    if effective_value == requested_value:
        return True

    if effective_value <= 0 or effective_value > requested_value:
        return False

    return any(
        (requested_value // power_unit) * power_unit == effective_value
        for power_unit in RAPL_POWER_UNITS_UW
    )


def _power_limit_matches_request(
    path: Path,
    requested_value: int,
    current_value: int,
) -> bool:
    if current_value == requested_value:
        return True

    return _effective_power_limit_cache.get(path) == (
        requested_value,
        current_value,
    )


def _rollback_power_limits(original_values: Dict[Path, int]) -> bool:
    restore_failed = False

    for path, value in original_values.items():
        current_value = _read_int(path)

        if current_value == value:
            continue

        if not _write_int(path, value):
            restore_failed = True
            continue

        if _read_int(path) != value:
            restore_failed = True

    if restore_failed:
        print("Failed to restore one or more RAPL power limits after an error")

    return False


def apply_rapl_power_limits(
    power_limits_uw: Dict[str, int],
    powercap_path: Path = POWERCAP_PATH,
) -> bool:
    global _thermald_skip_notified

    if not power_limits_uw:
        return True

    if any(value <= 0 for value in power_limits_uw.values()):
        return False

    zones = discover_rapl_package_zones(powercap_path)
    if not zones:
        return False

    targets = []
    found_constraints = set()

    # Keep separate RAPL powercap control types independent. A system may
    # expose the same package through e.g. intel-rapl (MSR) and
    # intel-rapl-mmio, and a write to one does not necessarily affect the
    # other. Explicit settings are therefore applied to every compatible
    # package zone instead of deduplicating zones by package name.
    for zone in zones:
        for constraint_name, power_limit_uw in power_limits_uw.items():
            power_limit_path = zone.constraints.get(constraint_name)

            if power_limit_path is None:
                continue

            targets.append((power_limit_path, power_limit_uw))
            found_constraints.add(constraint_name)

    if found_constraints != set(power_limits_uw):
        return False

    original_values = {}

    for path, _ in targets:
        if path in original_values:
            continue

        current_value = _read_int(path)

        if current_value is None:
            return False

        original_values[path] = current_value

    pending_targets = [
        (path, power_limit_uw)
        for path, power_limit_uw in targets
        if not _power_limit_matches_request(
            path,
            power_limit_uw,
            original_values[path],
        )
    ]

    if not pending_targets:
        return True

    # thermald can manage the same RAPL package limits. If it is active,
    # leave those limits under thermald's control instead of competing with
    # it by repeatedly rewriting the same powercap attributes.
    if _thermald_is_active():
        if not _thermald_skip_notified:
            print("Not setting Intel RAPL power limits (thermald is active)")
            _thermald_skip_notified = True
        return True

    effective_values = {}
    modified_values = {}

    # Treat a configured profile as one operation: if any target fails,
    # restore every target changed by this application so packages/control
    # types are not left partially configured.
    for path, power_limit_uw in pending_targets:
        current_value = _read_int(path)

        if current_value is None:
            return _rollback_power_limits(modified_values)

        if _power_limit_matches_request(
            path,
            power_limit_uw,
            current_value,
        ):
            continue

        if not _write_int(path, power_limit_uw):
            return _rollback_power_limits(modified_values)

        # From this point onward the target may need restoration if a later
        # write or readback fails.
        modified_values[path] = original_values[path]

        effective_value = _read_int(path)
        if (
            effective_value is None
            or not _is_valid_rapl_power_readback(
                power_limit_uw,
                effective_value,
            )
        ):
            return _rollback_power_limits(modified_values)

        effective_values[path] = (
            power_limit_uw,
            effective_value,
        )

    _effective_power_limit_cache.update(effective_values)
    return True


def get_rapl_package_power_limits(
    powercap_path: Path = POWERCAP_PATH,
) -> Dict[str, Dict[str, int]]:
    power_limits = {}

    for zone in discover_rapl_package_zones(powercap_path):
        zone_limits = {}

        for constraint_name in RAPL_CONFIG_OPTIONS.values():
            power_limit_path = zone.constraints.get(constraint_name)

            if power_limit_path is None:
                continue

            value = _read_int(power_limit_path)

            if value is not None:
                zone_limits[constraint_name] = value

        if zone_limits:
            power_limits[zone.path.name] = zone_limits

    return power_limits


def get_configured_rapl_power_limits(
    conf,
    profile: str,
    warn: bool = True,
) -> Dict[str, int]:
    power_limits = {}

    for option, constraint_name in RAPL_CONFIG_OPTIONS.items():
        if not conf.has_option(profile, option):
            continue

        raw_value = conf[profile].get(option, "").strip()

        if "," in raw_value:
            if warn:
                print(
                    f'Invalid value for "{option}" '
                    f'in [{profile}]: {raw_value!r}. '
                    'Decimal commas are not supported; use "." as the '
                    "decimal separator (for example, 17.5). "
                    "Ignoring setting."
                )
            continue

        try:
            watts = Decimal(raw_value)
        except InvalidOperation:
            watts = None

        if watts is None or not watts.is_finite() or watts <= 0:
            if warn:
                print(
                    f'Invalid value for "{option}" '
                    f'in [{profile}]: {raw_value!r}. '
                    "Expected a positive power value in watts. "
                    "Ignoring setting."
                )
            continue

        microwatts = watts * MICROWATTS_PER_WATT

        if microwatts != microwatts.to_integral_value():
            if warn:
                print(
                    f'Invalid value for "{option}" '
                    f'in [{profile}]: {raw_value!r}. '
                    "The value cannot be represented in whole microwatts. "
                    "Ignoring setting."
                )
            continue

        power_limits[constraint_name] = int(microwatts)

    return power_limits


def apply_configured_rapl_power_limits(conf, profile: str) -> bool:
    power_limits = get_configured_rapl_power_limits(conf, profile)

    if not power_limits:
        return True

    if apply_rapl_power_limits(power_limits):
        return True

    print(f"Failed to apply configured RAPL power limits for [{profile}]")
    return False
