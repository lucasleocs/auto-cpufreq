from dataclasses import dataclass
from pathlib import Path

from auto_cpufreq.power_state import capture_service_state


INTEL_PSTATE_ROOT = Path("/sys/devices/system/cpu/intel_pstate")
SYSTEMD_INIT_COMM = Path("/proc/1/comm")
POWER_SERVICE_UNITS = (
    ("auto-cpufreq", "auto-cpufreq.service"),
    ("power-profiles-daemon", "power-profiles-daemon.service"),
    ("tuned", "tuned.service"),
    ("TLP", "tlp.service"),
)


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    status: str


@dataclass(frozen=True)
class PowerServicesInfo:
    systemd_pid1: bool
    services: tuple[ServiceStatus, ...] = ()


@dataclass(frozen=True)
class IntelPstateInfo:
    mode: str | None = None
    min_perf_pct: int | None = None
    max_perf_pct: int | None = None


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


def read_intel_pstate_info(root: Path = INTEL_PSTATE_ROOT) -> IntelPstateInfo:
    """Read global intel_pstate diagnostics without changing driver state."""
    return IntelPstateInfo(
        mode=_read_text(root / "status"),
        min_perf_pct=_read_int(root / "min_perf_pct"),
        max_perf_pct=_read_int(root / "max_perf_pct"),
    )


def read_debug_override(getter, allowed_values) -> str:
    """Read an override without letting stale state break debug output."""
    try:
        value = getter()
    except Exception:
        return "Unavailable"

    if not isinstance(value, str):
        return "Unavailable"
    return value if value in allowed_values else "Unavailable"


def _format_service_state(state) -> str:
    if not isinstance(state, dict):
        return "Unavailable"
    if state.get("load_state") == "not-found":
        return "Not installed"

    active_state = state.get("active_state") or "unknown"
    unit_file_state = state.get("unit_file_state") or "unknown"
    return f"{active_state} / {unit_file_state}"


def read_power_services_info(
    init_comm: Path = SYSTEMD_INIT_COMM,
    capture_state=capture_service_state,
) -> PowerServicesInfo:
    """Read relevant systemd service state without changing service state."""
    if _read_text(init_comm) != "systemd":
        return PowerServicesInfo(systemd_pid1=False)

    services = tuple(
        ServiceStatus(name, _format_service_state(capture_state(unit)))
        for name, unit in POWER_SERVICE_UNITS
    )
    return PowerServicesInfo(systemd_pid1=True, services=services)


def format_power_services_info(info: PowerServicesInfo) -> str:
    lines = [
        "------------------------- Power Management Services -------------------------",
    ]
    if not info.systemd_pid1:
        lines.append("Service status: Unavailable (systemd is not PID 1)")
    else:
        lines.extend(
            f"{service.name}: {service.status}" for service in info.services
        )
    return "\n".join(lines)


def _battery_status(battery_info) -> str:
    if battery_info.is_charging is True:
        return "Charging"
    if battery_info.is_ac_plugged is False:
        return "Discharging"
    if battery_info.is_ac_plugged is True:
        return "Not charging"
    return "Unknown"


def _ac_status(is_ac_plugged: bool | None) -> str:
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


def _turbo_status(turbo_state) -> str:
    enabled, driver_managed = turbo_state
    if enabled is True:
        return "On"
    if enabled is False:
        return "Off"
    if driver_managed:
        return "Driver managed"
    return "Unavailable"


def _hwp_status(value: bool | None) -> str:
    if value is True:
        return "On"
    if value is False:
        return "Off"
    return "Unavailable"


def _value_or_unavailable(value) -> str:
    return str(value) if value is not None else "Unavailable"


def format_debug_diagnostics(
    report,
    *,
    config_path: str | None = None,
    intel_pstate: IntelPstateInfo | None = None,
    governor_override: str | None = None,
    turbo_override: str | None = None,
    power_services: PowerServicesInfo | None = None,
) -> str:
    """Format effective power-state details for issue/debug output."""
    lines = []

    if config_path:
        lines.append(f"Configuration: {config_path}")
        lines.append("")

    battery = report.battery_info
    lines.extend(
        [
            "------------------------------- Power Source --------------------------------",
            f"Battery status: {_battery_status(battery)}",
            "Battery charge: "
            + (
                f"{battery.battery_level}%"
                if battery.battery_level is not None
                else "Unavailable"
            ),
            f"AC power: {_ac_status(battery.is_ac_plugged)}",
            "Auto-cpufreq profile selection: "
            f"{_profile_selection(battery.is_ac_plugged)}",
        ]
    )

    if battery.power_consumption is not None:
        lines.append(f"Battery power: {battery.power_consumption:.2f} W")

    cpu_lines = [
        "",
        "----------------------------- CPU Power State -------------------------------",
        f"Driver: {_value_or_unavailable(report.cpu_driver)}",
        f"Governor: {_value_or_unavailable(report.current_gov)}",
    ]
    if governor_override is not None:
        cpu_lines.append(f"Governor override: {governor_override}")
    cpu_lines.extend(
        [
            f"EPP: {_value_or_unavailable(report.current_epp)}",
            f"EPB: {_value_or_unavailable(report.current_epb)}",
            f"HWP Dynamic Boost: {_hwp_status(report.current_hwp_dynamic_boost)}",
            f"Turbo Boost: {_turbo_status(report.is_turbo_on)}",
        ]
    )
    if turbo_override is not None:
        cpu_lines.append(f"Turbo override: {turbo_override}")
    lines.extend(cpu_lines)

    if intel_pstate is not None and intel_pstate.mode is not None:
        lines.extend(
            [
                f"intel_pstate mode: {intel_pstate.mode}",
                "intel_pstate min_perf_pct: "
                + (
                    f"{intel_pstate.min_perf_pct}%"
                    if intel_pstate.min_perf_pct is not None
                    else "Unavailable"
                ),
                "intel_pstate max_perf_pct: "
                + (
                    f"{intel_pstate.max_perf_pct}%"
                    if intel_pstate.max_perf_pct is not None
                    else "Unavailable"
                ),
            ]
        )

    if power_services is not None:
        lines.extend(
            ["", *format_power_services_info(power_services).splitlines()]
        )

    return "\n".join(lines)
