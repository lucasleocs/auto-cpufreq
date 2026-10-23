"""Read-only Intel RAPL package capability discovery.

This module never writes Powercap attributes. A readable limit is not
necessarily writable: firmware, drivers, or policy can lock a constraint.
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CONTROL_TYPES = ("intel-rapl", "intel-rapl-mmio")
PACKAGE_CONSTRAINTS = frozenset(("long_term", "short_term"))
DEFAULT_POWERCAP_ROOT = Path("/sys/class/powercap")


@dataclass(frozen=True)
class RaplPackageLimit:
    control_type: str
    package: str
    zone_id: str
    constraint_index: int
    constraint_name: str
    limit_uw: Optional[int]
    zone_enabled: Optional[bool]
    control_enabled: Optional[bool]

    @property
    def state(self) -> str:
        if self.control_enabled is False or self.zone_enabled is False:
            return "disabled"
        if self.limit_uw is None:
            return "unreadable"
        return "available"


@dataclass(frozen=True)
class RaplCapabilities:
    control_types: tuple[str, ...]
    limits: tuple[RaplPackageLimit, ...]

    @property
    def state(self) -> str:
        if not self.control_types:
            return "not_exposed"
        if not self.limits:
            return "no_package_constraints"
        if any(limit.state == "available" for limit in self.limits):
            return "available"
        if all(limit.state == "disabled" for limit in self.limits):
            return "disabled"
        return "unavailable"


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _read_positive_int(path: Path) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        value = int(raw, 10)
    except ValueError:
        return None
    return value if value > 0 else None


def _read_enabled(path: Path) -> Optional[bool]:
    raw = _read_text(path)
    return {"0": False, "1": True}.get(raw)


def _subdirectories(path: Path) -> tuple[Path, ...]:
    try:
        return tuple(sorted(
            (child for child in path.iterdir() if child.is_dir()),
            key=lambda child: child.name,
        ))
    except OSError:
        return ()


def _package_zones(
    root: Path, control_type: str
) -> tuple[tuple[Path, Optional[bool]], ...]:
    """Discover package-level zones through canonical control trees and aliases.

    Do not descend into core/uncore/DRAM subzones or interpret numeric suffixes
    as package IDs. Top-level class symlinks are allowed for read-only status.
    """
    controls = root / control_type
    control_is_dir = controls.is_dir()
    control_enabled = _read_enabled(controls / "enabled") if control_is_dir else None
    candidates = []

    if control_is_dir:
        candidates.extend(
            zone for zone in _subdirectories(controls)
            if zone.name.startswith(f"{control_type}:")
            and zone.name.count(":") == 1
        )

    # sysfs class directories may expose top-level aliases rather than
    # (or in addition to) nested zone names.
    candidates.extend(
        zone for zone in _subdirectories(root)
        if zone.name.startswith(f"{control_type}:")
        and zone.name.count(":") == 1
    )

    seen: set[Path] = set()
    zones = []
    for zone in candidates:
        try:
            resolved = zone.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        zone_name = _read_text(zone / "name")
        if zone_name is None or not zone_name.startswith("package-"):
            continue
        zones.append((zone, control_enabled))

    return tuple(zones)


def inspect_rapl(
    root: Path = DEFAULT_POWERCAP_ROOT,
) -> RaplCapabilities:
    """Read named package power limits without modifying the system."""
    root = Path(root)
    exposed = []
    limits = []

    for control in CONTROL_TYPES:
        control_dir = root / control
        zones = _package_zones(root, control)
        if control_dir.is_dir() or zones:
            exposed.append(control)

        for zone, control_enabled in zones:
            zone_enabled = _read_enabled(zone / "enabled")
            try:
                names = tuple(sorted(zone.glob("constraint_*_name")))
            except OSError:
                names = ()
            for name_path in names:
                stem = name_path.name
                number = stem[len("constraint_"):-len("_name")]
                if not number.isdecimal():
                    continue
                constraint_name = _read_text(name_path)
                if constraint_name not in PACKAGE_CONSTRAINTS:
                    continue
                index = int(number)
                limits.append(RaplPackageLimit(
                    control_type=control,
                    package=_read_text(zone / "name") or "",
                    zone_id=zone.name,
                    constraint_index=index,
                    constraint_name=constraint_name,
                    limit_uw=_read_positive_int(
                        zone / f"constraint_{index}_power_limit_uw"
                    ),
                    zone_enabled=zone_enabled,
                    control_enabled=control_enabled,
                ))

    limits.sort(key=lambda entry: (
        entry.control_type, entry.package,
        entry.constraint_name, entry.constraint_index
    ))
    return RaplCapabilities(tuple(exposed), tuple(limits))


def format_rapl_capabilities(snapshot: RaplCapabilities) -> tuple[str, ...]:
    """Format a compact shared summary for terminal and GTK consumers."""
    if snapshot.state == "not_exposed":
        return ("Intel RAPL: Not exposed by kernel",)
    if snapshot.state == "no_package_constraints":
        return ("Intel RAPL: No supported package power limits",)
    lines = []
    for entry in snapshot.limits:
        watts = (
            f"{entry.limit_uw / 1_000_000:.3f} W"
            if entry.limit_uw is not None else "Unknown"
        )
        lines.append(
            f"{entry.control_type} / {entry.package} / "
            f"{entry.constraint_name}: {watts}"
            + (f" ({entry.state})" if entry.state != "available" else "")
        )
    return tuple(lines)
