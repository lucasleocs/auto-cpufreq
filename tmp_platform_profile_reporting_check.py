from pathlib import Path

from auto_cpufreq.modules.platform_profile import (
    PlatformProfileDevice,
    PlatformProfileSnapshot,
)
from auto_cpufreq.modules.system_info import (
    SystemReport,
    format_platform_profile_summary,
    format_system_report,
)


def snapshot(*, choices_known, profiles=()):
    return PlatformProfileSnapshot(
        devices=(
            PlatformProfileDevice(
                provider="test-provider",
                profile="balanced",
                choices=tuple(profiles),
                profile_path=Path("/tmp/platform-profile"),
                choices_known=choices_known,
            ),
        ),
        interface="modern",
        current="balanced",
        available_profiles=tuple(profiles),
        choices_known=choices_known,
        writable=bool(choices_known and profiles),
    )


def report(platform_state):
    return SystemReport(
        distro_name="Test Linux",
        distro_ver="1",
        arch="x86_64",
        processor_model="Test CPU",
        total_core=1,
        kernel_version="test",
        current_gov="powersave",
        current_epp=None,
        current_epb=None,
        current_hwp_dynamic_boost=None,
        cpu_driver="test",
        cpu_fan_speed=None,
        cpu_usage=0.0,
        cpu_max_freq=None,
        cpu_min_freq=None,
        load=0.0,
        avg_load=None,
        cores_info=[],
        battery_info=None,
        is_turbo_on=(None, None),
        platform_profile=platform_state,
    )


unknown = snapshot(choices_known=False)
unknown_summary = format_platform_profile_summary(unknown)
assert "Available profiles: Could not be determined" in unknown_summary
assert all("Available profiles (0)" not in line for line in unknown_summary)
unknown_report = format_system_report(
    report(unknown), include_distro=False, include_config=False
)
assert "Available platform profiles: Could not be determined" in unknown_report
assert "Available platform profiles (0)" not in unknown_report

known_empty = snapshot(choices_known=True)
empty_summary = format_platform_profile_summary(known_empty)
assert "Available profiles (0): None available" in empty_summary
empty_report = format_system_report(
    report(known_empty), include_distro=False, include_config=False
)
assert "Available platform profiles (0): None available" in empty_report

known = snapshot(
    choices_known=True,
    profiles=("low-power", "balanced", "performance"),
)
known_summary = format_platform_profile_summary(known)
assert (
    "Available profiles (3): low-power, balanced, performance"
    in known_summary
)
known_report = format_system_report(
    report(known), include_distro=False, include_config=False
)
assert (
    "Available platform profiles (3): low-power, balanced, performance"
    in known_report
)

print("temporary Platform Profile reporting checks passed")
