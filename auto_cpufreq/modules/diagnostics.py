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


def _available(value: object | None) -> str:
    return str(value) if value is not None else "Unavailable"


def _enabled_state(value: bool | None) -> str:
    if value is True:
        return "Enabled"
    if value is False:
        return "Disabled"
    return "Unavailable"


def _battery_status(battery_info) -> str:
    if battery_info.is_charging is True:
        return "Charging"
    if battery_info.is_ac_plugged is False:
        return "Discharging"
    if battery_info.is_ac_plugged is None:
        return "Unknown"
    return "Not charging"


def _ac_state(is_ac_plugged: bool | None) -> str:
    if is_ac_plugged is True:
        return "Connected"
    if is_ac_plugged is False:
        return "Disconnected"
    return "Unknown"


def _profile_selection(is_ac_plugged: bool | None) -> str:
    if is_ac_plugged is False:
        return "battery"
    if is_ac_plugged is None:
        return "charger (AC state unknown)"
    return "charger"


def _turbo_state(state: tuple[bool | None, bool | None]) -> str:
    enabled, driver_managed = state
    if enabled is True:
        return "Enabled"
    if enabled is False:
        return "Disabled"
    if driver_managed is True:
        return "Driver managed"
    return "Unavailable"


def _override_state(value: str | None, *, default_label: str | None = None) -> str:
    if value is None:
        return "Unavailable"
    if value == "default" and default_label is not None:
        return default_label
    return value


def _threshold(value: int | None) -> str:
    return f"{value}%" if value is not None else "Unavailable"


def _format_service(status: ServiceStatus) -> str:
    if status.installed is False:
        return "Not installed"
    if status.installed is None:
        return "Unavailable"

    details = tuple(
        value
        for value in (status.active_state, status.unit_file_state)
        if value
    )
    return ", ".join(details) if details else "Installed"


def format_diagnostics_report(system_report, diagnostics: DiagnosticsReport) -> str:
    """Format already-collected debug state without performing new I/O."""
    battery = system_report.battery_info
    config_value = diagnostics.config_path or "defaults (no config file)"

    lines = [
        "Configuration",
        f"Configuration: {config_value}",
        "",
        "Power Source",
        f"AC power: {_ac_state(battery.is_ac_plugged)}",
        f"Battery status: {_battery_status(battery)}",
        f"Battery level: {_threshold(battery.battery_level)}",
        "Auto-cpufreq profile selection: "
        f"{_profile_selection(battery.is_ac_plugged)}",
    ]

    if battery.power_consumption is not None:
        lines.append(f"Battery power: {battery.power_consumption:g} W")

    threshold_info = diagnostics.battery_thresholds
    if threshold_info.batteries or threshold_info.conservation_mode is not None:
        lines.extend(["", "Battery Thresholds"])
        for item in threshold_info.batteries:
            lines.append(
                f"{item.name}: start {_threshold(item.start_threshold)}, "
                f"stop {_threshold(item.stop_threshold)}"
            )
        if threshold_info.conservation_mode is not None:
            lines.append(
                "Ideapad conservation mode: "
                f"{_enabled_state(threshold_info.conservation_mode)}"
            )

    lines.extend(
        [
            "",
            "CPU Power State",
            f"Governor: {_available(system_report.current_gov)}",
            "Governor override: "
            f"{_override_state(diagnostics.governor_override, default_label='none (profile-controlled)')}",
            f"EPP: {_available(system_report.current_epp)}",
            f"EPB: {_available(system_report.current_epb)}",
            "HWP Dynamic Boost: "
            f"{_enabled_state(system_report.current_hwp_dynamic_boost)}",
            f"Turbo Boost: {_turbo_state(system_report.is_turbo_on)}",
            f"Turbo override: {_override_state(diagnostics.turbo_override)}",
        ]
    )

    intel = diagnostics.intel_pstate
    if any(
        value is not None
        for value in (intel.mode, intel.min_perf_pct, intel.max_perf_pct)
    ):
        lines.append(f"Intel P-State mode: {_available(intel.mode)}")
        lines.append(
            "Intel P-State min performance: "
            f"{_threshold(intel.min_perf_pct)}"
        )
        lines.append(
            "Intel P-State max performance: "
            f"{_threshold(intel.max_perf_pct)}"
        )

    temperature = (
        f"{system_report.cpu_avg_temp:g} °C"
        if system_report.cpu_avg_temp is not None
        else "Unavailable"
    )
    lines.extend(
        [
            "",
            "System Load",
            f"Total CPU usage: {system_report.cpu_usage:g}%",
            f"Total system load: {system_report.load:.2f}",
            f"Average temp. of all cores: {temperature}",
            "",
            "Power Management Services",
        ]
    )

    services = diagnostics.power_services
    if services.init_system != "systemd":
        lines.append(
            "Service status: Unavailable (PID 1: "
            f"{services.init_system or 'unknown'})"
        )
    elif not services.services:
        lines.append("Service status: Unavailable")
    else:
        lines.extend(
            f"{service.name}: {_format_service(service)}"
            for service in services.services
        )

    return "\n".join(lines)
