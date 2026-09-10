from dataclasses import dataclass
from pathlib import Path
import subprocess

from auto_cpufreq.power_state import capture_service_state


INTEL_PSTATE_ROOT = Path("/sys/devices/system/cpu/intel_pstate")
AMD_PSTATE_ROOT = Path("/sys/devices/system/cpu/amd_pstate")
CPUFREQ_POLICY_ROOT = Path("/sys/devices/system/cpu/cpufreq")
SYSTEMD_INIT_COMM = Path("/proc/1/comm")
POWER_SERVICE_UNITS = (
    ("auto-cpufreq", "auto-cpufreq.service"),
    ("power-profiles-daemon", "power-profiles-daemon.service"),
    ("tuned", "tuned.service"),
    ("tuned-ppd", "tuned-ppd.service"),
    ("TLP", "tlp.service"),
)
SNAP_HOST_SERVICE_NAMES = (
    "power-profiles-daemon",
    "tuned",
    "tuned-ppd",
    "TLP",
)


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    status: str


@dataclass(frozen=True)
class PowerServicesInfo:
    systemd_pid1: bool
    services: tuple[ServiceStatus, ...] = ()
    init_system: str | None = None


@dataclass(frozen=True)
class IntelPstateInfo:
    mode: str | None = None
    min_perf_pct: int | None = None
    max_perf_pct: int | None = None


@dataclass(frozen=True)
class AmdPstateInfo:
    mode: str | None = None
    preferred_core: str | None = None


@dataclass(frozen=True)
class CpuFreqPolicyInfo:
    name: str
    scaling_governor: str | None = None
    energy_performance_preference: str | None = None
    available_governors: tuple[str, ...] | None = None
    available_epp_preferences: tuple[str, ...] | None = None
    scaling_min_freq_khz: int | None = None
    scaling_max_freq_khz: int | None = None


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


def _read_words(path: Path) -> tuple[str, ...] | None:
    value = _read_text(path)
    if value is None:
        return None
    words = tuple(dict.fromkeys(value.split()))
    return words or None


def read_intel_pstate_info(root: Path = INTEL_PSTATE_ROOT) -> IntelPstateInfo:
    """Read global intel_pstate diagnostics without changing driver state."""
    return IntelPstateInfo(
        mode=_read_text(root / "status"),
        min_perf_pct=_read_int(root / "min_perf_pct"),
        max_perf_pct=_read_int(root / "max_perf_pct"),
    )


def read_amd_pstate_info(root: Path = AMD_PSTATE_ROOT) -> AmdPstateInfo:
    """Read global amd_pstate diagnostics without changing driver state."""
    return AmdPstateInfo(
        mode=_read_text(root / "status"),
        preferred_core=_read_text(root / "prefcore"),
    )


def _policy_sort_key(path: Path):
    suffix = path.name.removeprefix("policy")
    try:
        return (0, int(suffix))
    except ValueError:
        return (1, path.name)


def read_cpufreq_policy_info(
    root: Path = CPUFREQ_POLICY_ROOT,
) -> tuple[CpuFreqPolicyInfo, ...]:
    """Read CPUFreq policy capabilities and effective limits from sysfs."""
    try:
        policy_paths = sorted(
            (
                path
                for path in root.iterdir()
                if path.name.startswith("policy") and path.is_dir()
            ),
            key=_policy_sort_key,
        )
    except OSError:
        return ()

    return tuple(
        CpuFreqPolicyInfo(
            name=path.name,
            scaling_governor=_read_text(path / "scaling_governor"),
            energy_performance_preference=_read_text(
                path / "energy_performance_preference"
            ),
            available_governors=_read_words(path / "scaling_available_governors"),
            available_epp_preferences=_read_words(
                path / "energy_performance_available_preferences"
            ),
            scaling_min_freq_khz=_read_int(path / "scaling_min_freq"),
            scaling_max_freq_khz=_read_int(path / "scaling_max_freq"),
        )
        for path in policy_paths
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


def _read_snap_daemon_status(snap_runner=subprocess.run) -> str:
    try:
        result = snap_runner(
            ["snapctl", "services", "auto-cpufreq.service"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "Unavailable"

    if result.returncode != 0:
        return "Unavailable"

    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "auto-cpufreq.service":
            startup, current = fields[1], fields[2]
            return f"{current} / {startup}"
    return "Unavailable"


def read_power_services_info(
    init_comm: Path = SYSTEMD_INIT_COMM,
    capture_state=capture_service_state,
    *,
    is_snap: bool = False,
    snap_runner=subprocess.run,
) -> PowerServicesInfo:
    """Read relevant service state without changing service state."""
    init_system = _read_text(init_comm)

    if is_snap:
        services = [
            ServiceStatus("auto-cpufreq", _read_snap_daemon_status(snap_runner)),
        ]
        services.extend(
            ServiceStatus(name, "Unavailable (Snap confinement)")
            for name in SNAP_HOST_SERVICE_NAMES
        )
        return PowerServicesInfo(
            systemd_pid1=init_system == "systemd",
            services=tuple(services),
            init_system=init_system,
        )

    if init_system != "systemd":
        return PowerServicesInfo(
            systemd_pid1=False,
            init_system=init_system,
        )

    services = tuple(
        ServiceStatus(name, _format_service_state(capture_state(unit)))
        for name, unit in POWER_SERVICE_UNITS
    )
    return PowerServicesInfo(
        systemd_pid1=True,
        services=services,
        init_system=init_system,
    )


def format_power_services_info(info: PowerServicesInfo) -> str:
    lines = [
        "------------------------- Power Management Services -------------------------",
        f"Init system: {info.init_system or 'Unknown'}",
    ]
    if not info.systemd_pid1 and not info.services:
        lines.append("Service status: Unavailable (systemd is not PID 1)")
    else:
        lines.extend(
            f"{service.name}: {service.status}" for service in info.services
        )
    return "\n".join(lines)


def _format_policy_groups(policies, value_getter, value_formatter) -> str:
    if not policies:
        return "Unavailable"

    groups = {}
    for policy in policies:
        value = value_getter(policy)
        groups.setdefault(value, []).append(policy.name)

    if len(groups) == 1:
        return value_formatter(next(iter(groups)))

    details = "; ".join(
        f"{value_formatter(value)} [{', '.join(names)}]"
        for value, names in groups.items()
    )
    return f"mixed across policies ({details})"


def _format_words(value: tuple[str, ...] | None) -> str:
    return " ".join(value) if value else "Unavailable"


def _policy_limits(policy: CpuFreqPolicyInfo) -> tuple[int, int] | None:
    if (
        policy.scaling_min_freq_khz is None
        or policy.scaling_max_freq_khz is None
    ):
        return None
    return policy.scaling_min_freq_khz, policy.scaling_max_freq_khz


def _format_mhz(khz: int) -> str:
    mhz = khz / 1000
    return f"{mhz:g}"


def _format_limits(value: tuple[int, int] | None) -> str:
    if value is None:
        return "Unavailable"
    minimum, maximum = value
    return f"{_format_mhz(minimum)}–{_format_mhz(maximum)} MHz"


def format_cpufreq_policy_info(
    policies: tuple[CpuFreqPolicyInfo, ...],
) -> str:
    """Format CPUFreq policy capabilities without hiding policy divergence."""
    return "\n".join(
        [
            "Available governors: "
            + _format_policy_groups(
                policies,
                lambda policy: policy.available_governors,
                _format_words,
            ),
            "Available EPP preferences: "
            + _format_policy_groups(
                policies,
                lambda policy: policy.available_epp_preferences,
                _format_words,
            ),
            "CPUFreq policy limits: "
            + _format_policy_groups(
                policies,
                _policy_limits,
                _format_limits,
            ),
        ]
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
        return "Enabled"
    if enabled is False:
        return "Disabled"
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
    amd_pstate: AmdPstateInfo | None = None,
    cpufreq_policies: tuple[CpuFreqPolicyInfo, ...] | None = None,
    governor_override: str | None = None,
    turbo_override: str | None = None,
    power_services: PowerServicesInfo | None = None,
) -> str:
    """Format effective power-state details for issue/debug output."""
    lines = [
        "Configuration: "
        + (config_path if config_path else "defaults (no config file)"),
        "",
    ]

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

    governor = _value_or_unavailable(report.current_gov)
    if cpufreq_policies and any(
        policy.scaling_governor is not None for policy in cpufreq_policies
    ):
        governor = _format_policy_groups(
            cpufreq_policies,
            lambda policy: policy.scaling_governor,
            _value_or_unavailable,
        )

    epp = _value_or_unavailable(report.current_epp)
    if cpufreq_policies and any(
        policy.energy_performance_preference is not None
        for policy in cpufreq_policies
    ):
        epp = _format_policy_groups(
            cpufreq_policies,
            lambda policy: policy.energy_performance_preference,
            _value_or_unavailable,
        )

    cpu_lines = [
        "",
        "----------------------------- CPU Power State -------------------------------",
        f"Driver: {_value_or_unavailable(report.cpu_driver)}",
        f"Governor: {governor}",
    ]
    if governor_override is not None:
        formatted_override = (
            "none (profile-controlled)"
            if governor_override == "default"
            else governor_override
        )
        cpu_lines.append(f"Governor override: {formatted_override}")
    cpu_lines.extend(
        [
            f"EPP: {epp}",
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

    if amd_pstate is not None and amd_pstate.mode is not None:
        lines.append(f"amd_pstate mode: {amd_pstate.mode}")
        if amd_pstate.preferred_core is not None:
            lines.append(f"amd_pstate preferred core: {amd_pstate.preferred_core}")

    if cpufreq_policies is not None:
        lines.extend(format_cpufreq_policy_info(cpufreq_policies).splitlines())

    if power_services is not None:
        lines.extend(
            ["", *format_power_services_info(power_services).splitlines()]
        )

    return "\n".join(lines)
