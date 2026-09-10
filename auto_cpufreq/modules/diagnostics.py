from dataclasses import dataclass
from pathlib import Path


INTEL_PSTATE_ROOT = Path("/sys/devices/system/cpu/intel_pstate")


@dataclass(frozen=True)
class IntelPstateInfo:
    mode: str | None = None
    min_perf_pct: int | None = None
    max_perf_pct: int | None = None


def format_source_version(version: str) -> str:
    """Keep the package release readable while retaining source revision metadata."""
    release, separator, revision = version.partition("+")
    if separator and revision:
        return f"{release} (git: {revision})"
    return release


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

    lines.extend(
        [
            "",
            "----------------------------- CPU Power State -------------------------------",
            f"Driver: {_value_or_unavailable(report.cpu_driver)}",
            f"Governor: {_value_or_unavailable(report.current_gov)}",
            f"EPP: {_value_or_unavailable(report.current_epp)}",
            f"EPB: {_value_or_unavailable(report.current_epb)}",
            f"HWP Dynamic Boost: {_hwp_status(report.current_hwp_dynamic_boost)}",
            f"Turbo Boost: {_turbo_status(report.is_turbo_on)}",
        ]
    )

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

    return "\n".join(lines)
