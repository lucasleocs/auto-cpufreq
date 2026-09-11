from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one match in {path} but found {text.count(old)}"
        )
    path.write_text(text.replace(old, new, 1))


intel_path = Path("auto_cpufreq/modules/intel_power.py")
intel_text = intel_path.read_text()
if "\nintel_power = IntelPowerDiscovery()\n" not in intel_text:
    intel_path.write_text(intel_text.rstrip() + "\n\n\nintel_power = IntelPowerDiscovery()\n")


system_info_path = Path("auto_cpufreq/modules/system_info.py")
replace_once(
    system_info_path,
    '''from auto_cpufreq.modules.platform_profile import (
    PlatformProfileSnapshot,
    platform_profile,
)
''',
    '''from auto_cpufreq.modules.intel_power import (
    IntelPowerSnapshot,
    ReadStatus,
    intel_power,
)
from auto_cpufreq.modules.platform_profile import (
    PlatformProfileSnapshot,
    platform_profile,
)
''',
)

replace_once(
    system_info_path,
    '''    offline_cpus: tuple[int, ...] = ()
    platform_profile: PlatformProfileSnapshot = PlatformProfileSnapshot()
''',
    '''    offline_cpus: tuple[int, ...] = ()
    platform_profile: PlatformProfileSnapshot = PlatformProfileSnapshot()
    intel_power: IntelPowerSnapshot | None = None
''',
)

replace_once(
    system_info_path,
    '''    def generate_system_report(self) -> SystemReport:
        """Collect one reporting snapshot without changing system state."""
''',
    '''    def generate_system_report(
        self,
        include_intel_power: bool = False,
        sample_intel_energy: bool = False,
    ) -> SystemReport:
        """Collect one reporting snapshot without changing system state."""
''',
)

replace_once(
    system_info_path,
    '''        total_usage, per_cpu_usage = self.cpu_usage_snapshot(online_cpus)
        load_average = self.avg_load()

        return SystemReport(
''',
    '''        total_usage, per_cpu_usage = self.cpu_usage_snapshot(online_cpus)
        load_average = self.avg_load()
        intel_snapshot = (
            intel_power.snapshot(sample_energy=sample_intel_energy)
            if include_intel_power
            else None
        )

        return SystemReport(
''',
)

replace_once(
    system_info_path,
    '''            offline_cpus=tuple(self.offline_cpu_ids()),
            platform_profile=platform_profile.snapshot(),
        )
''',
    '''            offline_cpus=tuple(self.offline_cpu_ids()),
            platform_profile=platform_profile.snapshot(),
            intel_power=intel_snapshot,
        )
''',
)

formatter = r'''

def _format_intel_read(result, formatter=str) -> str:
    if result.status is ReadStatus.MISSING:
        return "Unavailable"
    if result.status is ReadStatus.UNREADABLE:
        return "Could not be read"
    if result.status is ReadStatus.INVALID or result.value is None:
        return "Unknown"
    return formatter(result.value)


def _format_cpu_ids(result) -> str:
    return _format_intel_read(
        result,
        lambda values: ",".join(str(value) for value in values) or "None",
    )


def format_intel_power_summary(snapshot: IntelPowerSnapshot) -> list[str]:
    """Format read-only modern Intel power diagnostics."""
    lines = [
        f"intel_pstate: {_format_intel_read(snapshot.intel_pstate_status)}",
        "Turbo permission: "
        + _format_intel_read(
            snapshot.turbo_allowed,
            lambda value: "Allowed" if value else "Disabled",
        ),
        "HWP Dynamic Boost: "
        + _format_intel_read(
            snapshot.hwp_dynamic_boost,
            lambda value: "On" if value else "Off",
        ),
    ]

    if snapshot.cpu_topology:
        lines.append("CPU topology:")
        for cpu in snapshot.cpu_topology:
            package = _format_intel_read(cpu.physical_package_id)
            core = _format_intel_read(cpu.core_id)
            siblings = _format_cpu_ids(cpu.thread_siblings)
            lines.append(
                f"  CPU{cpu.cpu_id}: package {package}, core {core}, "
                f"thread siblings {siblings}"
            )
    else:
        lines.append("CPU topology: Unavailable")

    if snapshot.cpufreq_policies:
        lines.append("CPUFreq policies:")
        for policy in snapshot.cpufreq_policies:
            cpus = _format_cpu_ids(policy.related_cpus)
            driver = _format_intel_read(policy.scaling_driver)
            governor = _format_intel_read(policy.scaling_governor)
            epp = _format_intel_read(policy.epp)
            cpuinfo_min = _format_intel_read(
                policy.cpuinfo_min_freq_khz,
                lambda value: f"{value / 1000:.0f} MHz",
            )
            cpuinfo_max = _format_intel_read(
                policy.cpuinfo_max_freq_khz,
                lambda value: f"{value / 1000:.0f} MHz",
            )
            scaling_min = _format_intel_read(
                policy.scaling_min_freq_khz,
                lambda value: f"{value / 1000:.0f} MHz",
            )
            scaling_max = _format_intel_read(
                policy.scaling_max_freq_khz,
                lambda value: f"{value / 1000:.0f} MHz",
            )
            lines.append(
                f"  {policy.policy}: CPUs {cpus}; driver {driver}; "
                f"governor {governor}; EPP {epp}; "
                f"hardware range {cpuinfo_min} - {cpuinfo_max}; "
                f"scaling range {scaling_min} - {scaling_max}"
            )
    else:
        lines.append("CPUFreq policies: Unavailable")

    if snapshot.powercap_zones:
        lines.append("Powercap zones:")
        for zone in snapshot.powercap_zones:
            zone_name = _format_intel_read(zone.name)
            lines.append(f"  Zone: {zone_name} ({zone.zone_id})")
            if zone.control_type is not None:
                lines.append(f"    Control type: {zone.control_type}")
            lines.append(
                "    Energy: "
                + _format_intel_read(
                    zone.energy_uj,
                    lambda value: f"{value / 1_000_000:.3f} J",
                )
            )
            if zone.energy_uj.status is ReadStatus.AVAILABLE:
                if zone.power_sample is None:
                    lines.append("    Average power: Awaiting next sample")
                else:
                    lines.append(
                        f"    Average power: {zone.power_sample.average_power_w:.3f} W"
                    )
            for constraint in zone.constraints:
                name = _format_intel_read(
                    constraint.name,
                    lambda value: value,
                )
                power = _format_intel_read(
                    constraint.power_limit_uw,
                    lambda value: f"{value / 1_000_000:.3f} W",
                )
                window = _format_intel_read(
                    constraint.time_window_us,
                    lambda value: f"{value / 1_000_000:.3f} s",
                )
                lines.append(f"    {name}: {power}, {window}")
    else:
        lines.append("Powercap zones: Unavailable")

    if snapshot.thermal_packages:
        for thermal in snapshot.thermal_packages:
            count = _format_intel_read(thermal.throttle_count)
            total = _format_intel_read(
                thermal.total_time_ms,
                lambda value: f"{value} ms",
            )
            maximum = _format_intel_read(
                thermal.max_time_ms,
                lambda value: f"{value} ms",
            )
            lines.append(
                f"Package {thermal.package_id} thermal throttling: "
                f"count {count}; total {total}; max event {maximum}; "
                f"CPU{thermal.representative_cpu} representative"
            )
    else:
        lines.append("Package thermal throttling: Unavailable")

    return lines
'''

replace_once(
    system_info_path,
    '''\ndef format_system_report(\n''',
    formatter + '''\n\ndef format_system_report(\n''',
)

replace_once(
    system_info_path,
    '''    if report.cpu_fan_speed is not None:
        lines.extend(["", f"CPU fan speed: {report.cpu_fan_speed} RPM"])

    return "\\n".join(lines)
''',
    '''    if report.cpu_fan_speed is not None:
        lines.extend(["", f"CPU fan speed: {report.cpu_fan_speed} RPM"])

    if report.intel_power is not None:
        lines.extend(
            [
                "",
                "-" * 25 + " Intel Power Diagnostics " + "-" * 25,
                "",
            ]
        )
        lines.extend(format_intel_power_summary(report.intel_power))

    return "\\n".join(lines)
''',
)


cli_path = Path("auto_cpufreq/bin/auto_cpufreq.py")
replace_once(
    cli_path,
    '''from auto_cpufreq.modules.system_info import (
    format_platform_profile_summary,
    print_system_report,
)
''',
    '''from auto_cpufreq.modules.system_info import (
    format_platform_profile_summary,
    print_system_report,
    system_info,
)
''',
)

replace_once(
    cli_path,
    '''            footer()
            print_system_report()
            print()
            app_version()
''',
    '''            footer()
            report = system_info.generate_system_report(
                include_intel_power=True,
                sample_intel_energy=False,
            )
            print_system_report(report)
            print()
            app_version()
''',
)

print("task4 production patch applied")
