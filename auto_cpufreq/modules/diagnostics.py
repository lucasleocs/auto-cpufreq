from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from auto_cpufreq.power_state import capture_service_state


INTEL_PSTATE_ROOT = Path("/sys/devices/system/cpu/intel_pstate")
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
SYSTEMD_INIT_COMM = Path("/proc/1/comm")
IDEAPAD_ROOTS = (
    Path("/sys/bus/platform/drivers/ideapad_acpi"),
    Path("/sys/devices/platform/ideapad_acpi"),
)
POWER_SERVICE_UNITS = (
    ("auto-cpufreq", "auto-cpufreq.service"),
    ("power-profiles-daemon", "power-profiles-daemon.service"),
    ("tuned", "tuned.service"),
    ("TLP", "tlp.service"),
)


@dataclass(frozen=True)
class IntelPstateInfo:
    mode: str | None = None
    min_perf_pct: int | None = None
    max_perf_pct: int | None = None


@dataclass(frozen=True)
class BatteryThresholdInfo:
    name: str
    start_threshold: int | None = None
    stop_threshold: int | None = None


@dataclass(frozen=True)
class BatteryThresholdDiagnostics:
    batteries: tuple[BatteryThresholdInfo, ...] = ()
    conservation_mode: bool | None = None


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    installed: bool | None
    active_state: str | None = None
    unit_file_state: str | None = None


@dataclass(frozen=True)
class PowerServicesInfo:
    init_system: str | None
    services: tuple[ServiceStatus, ...] = ()


@dataclass(frozen=True)
class DiagnosticsReport:
    config_path: str | None
    governor_override: str | None
    turbo_override: str | None
    intel_pstate: IntelPstateInfo
    battery_thresholds: BatteryThresholdDiagnostics
    power_services: PowerServicesInfo


def _read_text(path: Path) -> str | None:
    try:
        value = path.read_text().strip()
    except OSError:
        return None
    return value or None


def _read_int(path: Path) -> int | None:
    value = _read_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _read_first_int(directory: Path, names: tuple[str, ...]) -> int | None:
    for name in names:
        value = _read_int(directory / name)
        if value is not None:
            return value
    return None


def read_debug_override(
    getter: Callable[[], object],
    allowed_values: set[str],
) -> str | None:
    """Read persisted override state without letting stale data break debug."""
    try:
        value = getter()
    except Exception:
        return None

    if not isinstance(value, str):
        return None
    return value if value in allowed_values else None


def read_intel_pstate_info(
    root: Path = INTEL_PSTATE_ROOT,
) -> IntelPstateInfo:
    """Read global Intel P-State diagnostics without changing driver state."""
    return IntelPstateInfo(
        mode=_read_text(root / "status"),
        min_perf_pct=_read_int(root / "min_perf_pct"),
        max_perf_pct=_read_int(root / "max_perf_pct"),
    )


def _system_battery_paths(root: Path) -> tuple[Path, ...]:
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()

    batteries = []
    for entry in entries:
        if not entry.is_dir():
            continue
        supply_type = _read_text(entry / "type")
        if supply_type is not None and supply_type.lower() == "battery":
            batteries.append(entry)
    return tuple(batteries)


def _read_conservation_mode(roots: Iterable[Path]) -> bool | None:
    for root in roots:
        root = Path(root)
        candidates = [root / "conservation_mode"]
        try:
            candidates.extend(sorted(root.glob("*/conservation_mode")))
        except OSError:
            pass

        for candidate in candidates:
            value = _read_text(candidate)
            if value == "0":
                return False
            if value == "1":
                return True
    return None


def read_battery_threshold_diagnostics(
    power_supply_root: Path = POWER_SUPPLY_ROOT,
    ideapad_roots: Iterable[Path] = IDEAPAD_ROOTS,
) -> BatteryThresholdDiagnostics:
    """Read per-battery threshold state without using write-oriented devices."""
    batteries = tuple(
        BatteryThresholdInfo(
            name=path.name,
            start_threshold=_read_first_int(
                path,
                (
                    "charge_control_start_threshold",
                    "charge_start_threshold",
                ),
            ),
            stop_threshold=_read_first_int(
                path,
                (
                    "charge_control_end_threshold",
                    "charge_stop_threshold",
                ),
            ),
        )
        for path in _system_battery_paths(Path(power_supply_root))
    )

    return BatteryThresholdDiagnostics(
        batteries=batteries,
        conservation_mode=_read_conservation_mode(ideapad_roots),
    )


def _service_status(name: str, state) -> ServiceStatus:
    if not isinstance(state, dict):
        return ServiceStatus(name=name, installed=None)
    if state.get("load_state") == "not-found":
        return ServiceStatus(name=name, installed=False)

    return ServiceStatus(
        name=name,
        installed=True,
        active_state=state.get("active_state") or None,
        unit_file_state=state.get("unit_file_state") or None,
    )


def read_power_services_info(
    init_comm: Path = SYSTEMD_INIT_COMM,
    capture_state=capture_service_state,
) -> PowerServicesInfo:
    """Read relevant systemd service state without changing service state."""
    init_system = _read_text(Path(init_comm))
    if init_system != "systemd":
        return PowerServicesInfo(init_system=init_system)

    services = []
    for name, unit in POWER_SERVICE_UNITS:
        try:
            state = capture_state(unit)
        except Exception:
            state = None
        services.append(_service_status(name, state))

    return PowerServicesInfo(
        init_system=init_system,
        services=tuple(services),
    )


def collect_diagnostics(
    _system_report,
    *,
    config_path: str | None,
    governor_override_getter: Callable[[], object],
    turbo_override_getter: Callable[[], object],
    intel_pstate_root: Path = INTEL_PSTATE_ROOT,
    power_supply_root: Path = POWER_SUPPLY_ROOT,
    ideapad_roots: Iterable[Path] = IDEAPAD_ROOTS,
    init_comm: Path = SYSTEMD_INIT_COMM,
    capture_state=capture_service_state,
) -> DiagnosticsReport:
    """Collect one-shot debug state without recollecting fast telemetry."""
    return DiagnosticsReport(
        config_path=config_path,
        governor_override=read_debug_override(
            governor_override_getter,
            {"default", "powersave", "performance"},
        ),
        turbo_override=read_debug_override(
            turbo_override_getter,
            {"auto", "always", "never"},
        ),
        intel_pstate=read_intel_pstate_info(Path(intel_pstate_root)),
        battery_thresholds=read_battery_threshold_diagnostics(
            Path(power_supply_root),
            ideapad_roots,
        ),
        power_services=read_power_services_info(
            Path(init_comm),
            capture_state,
        ),
    )
