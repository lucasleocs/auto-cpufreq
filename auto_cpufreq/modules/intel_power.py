from dataclasses import dataclass
from pathlib import Path
import time
from typing import Optional

from auto_cpufreq.modules.sysfs import (
    ReadResult,
    ReadStatus,
    read_bool01 as _read_bool01,
    read_int as _read_int,
    read_text as _read_text,
)


@dataclass(frozen=True)
class CpuTopologySnapshot:
    cpu_id: int
    physical_package_id: ReadResult[int]
    core_id: ReadResult[int]
    thread_siblings: ReadResult[tuple[int, ...]]


@dataclass(frozen=True)
class CpuFreqPolicySnapshot:
    policy: str
    related_cpus: ReadResult[tuple[int, ...]]
    scaling_driver: ReadResult[str]
    scaling_governor: ReadResult[str]
    cpuinfo_min_freq_khz: ReadResult[int]
    cpuinfo_max_freq_khz: ReadResult[int]
    scaling_min_freq_khz: ReadResult[int]
    scaling_max_freq_khz: ReadResult[int]
    epp: ReadResult[str]
    available_epp: ReadResult[tuple[str, ...]]


@dataclass(frozen=True)
class PowercapConstraintSnapshot:
    index: int
    name: ReadResult[str]
    power_limit_uw: ReadResult[int]
    time_window_us: ReadResult[int]
    min_power_uw: ReadResult[int]
    max_power_uw: ReadResult[int]
    min_time_window_us: ReadResult[int]
    max_time_window_us: ReadResult[int]


@dataclass(frozen=True)
class PowerSample:
    delta_energy_uj: int
    elapsed_ns: int
    average_power_w: float


@dataclass(frozen=True)
class PowercapZoneSnapshot:
    zone_id: str
    control_type: Optional[str]
    sysfs_name: str
    name: ReadResult[str]
    energy_uj: ReadResult[int]
    max_energy_range_uj: ReadResult[int]
    constraints: tuple[PowercapConstraintSnapshot, ...]
    power_sample: Optional[PowerSample] = None


@dataclass(frozen=True)
class ThermalThrottleSnapshot:
    package_id: int
    representative_cpu: int
    throttle_count: ReadResult[int]
    total_time_ms: ReadResult[int]
    max_time_ms: ReadResult[int]


@dataclass(frozen=True)
class IntelPowerSnapshot:
    intel_pstate_status: ReadResult[str]
    turbo_allowed: ReadResult[bool]
    hwp_dynamic_boost: ReadResult[bool]
    cpu_topology: tuple[CpuTopologySnapshot, ...]
    cpufreq_policies: tuple[CpuFreqPolicySnapshot, ...]
    powercap_zones: tuple[PowercapZoneSnapshot, ...] = ()
    thermal_packages: tuple[ThermalThrottleSnapshot, ...] = ()


def _parse_cpu_list(value: str) -> tuple[int, ...]:
    cpus: list[int] = []
    for token in value.replace(",", " ").split():
        if "-" not in token:
            cpus.append(int(token))
            continue

        start_text, end_text = token.split("-", 1)
        start = int(start_text)
        end = int(end_text)
        if end < start:
            raise ValueError("invalid CPU range")
        cpus.extend(range(start, end + 1))
    return tuple(cpus)


def _read_cpu_list(path: Path) -> ReadResult[tuple[int, ...]]:
    result = _read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    try:
        return ReadResult(ReadStatus.AVAILABLE, _parse_cpu_list(result.value or ""))
    except ValueError:
        return ReadResult(ReadStatus.INVALID)


def _read_word_list(path: Path) -> ReadResult[tuple[str, ...]]:
    result = _read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    return ReadResult(ReadStatus.AVAILABLE, tuple((result.value or "").split()))


def _numeric_suffix(path: Path, prefix: str) -> tuple[int, str]:
    suffix = path.name.removeprefix(prefix)
    try:
        return int(suffix), path.name
    except ValueError:
        return 2**31 - 1, path.name


def _is_within(path: Path, boundary: Path) -> bool:
    return path == boundary or boundary in path.parents


class EnergySampler:
    def __init__(self) -> None:
        self._previous: dict[str, tuple[int, int]] = {}

    def sample(
        self,
        zone_id: str,
        energy_uj: int,
        max_range_uj: Optional[int],
        now_ns: Optional[int] = None,
    ) -> Optional[PowerSample]:
        """Return average power since the previous sample for one zone.

        Correctness assumes no more than one hardware energy-counter wrap
        between consecutive samples for the same zone.
        """
        now = time.monotonic_ns() if now_ns is None else now_ns
        previous = self._previous.get(zone_id)
        self._previous[zone_id] = (energy_uj, now)
        if previous is None:
            return None

        previous_energy, previous_ns = previous
        elapsed_ns = now - previous_ns
        if elapsed_ns <= 0:
            return None

        if energy_uj >= previous_energy:
            delta_uj = energy_uj - previous_energy
        elif max_range_uj is not None and max_range_uj > 0:
            delta_uj = (max_range_uj - previous_energy) + energy_uj
        else:
            return None

        return PowerSample(
            delta_energy_uj=delta_uj,
            elapsed_ns=elapsed_ns,
            average_power_w=(delta_uj / 1_000_000)
            / (elapsed_ns / 1_000_000_000),
        )


class IntelPowerDiscovery:
    def __init__(
        self,
        cpu_root: Path = Path("/sys/devices/system/cpu"),
        powercap_root: Path = Path("/sys/class/powercap"),
    ) -> None:
        self.cpu_root = Path(cpu_root)
        self.powercap_root = Path(powercap_root)
        self._energy_sampler = EnergySampler()

    def _cpu_topology(self) -> tuple[CpuTopologySnapshot, ...]:
        snapshots: list[CpuTopologySnapshot] = []
        cpu_dirs = sorted(
            (
                path
                for path in self.cpu_root.glob("cpu[0-9]*")
                if path.name[3:].isdigit()
            ),
            key=lambda path: int(path.name[3:]),
        )
        for cpu_dir in cpu_dirs:
            cpu_id = int(cpu_dir.name[3:])
            topology = cpu_dir / "topology"
            snapshots.append(
                CpuTopologySnapshot(
                    cpu_id=cpu_id,
                    physical_package_id=_read_int(topology / "physical_package_id"),
                    core_id=_read_int(topology / "core_id"),
                    thread_siblings=_read_cpu_list(topology / "thread_siblings_list"),
                )
            )
        return tuple(snapshots)

    def _cpufreq_policies(self) -> tuple[CpuFreqPolicySnapshot, ...]:
        cpufreq_root = self.cpu_root / "cpufreq"
        policies = sorted(
            cpufreq_root.glob("policy*"),
            key=lambda path: _numeric_suffix(path, "policy"),
        )
        snapshots: list[CpuFreqPolicySnapshot] = []
        for policy_path in policies:
            snapshots.append(
                CpuFreqPolicySnapshot(
                    policy=policy_path.name,
                    related_cpus=_read_cpu_list(policy_path / "related_cpus"),
                    scaling_driver=_read_text(policy_path / "scaling_driver"),
                    scaling_governor=_read_text(policy_path / "scaling_governor"),
                    cpuinfo_min_freq_khz=_read_int(policy_path / "cpuinfo_min_freq"),
                    cpuinfo_max_freq_khz=_read_int(policy_path / "cpuinfo_max_freq"),
                    scaling_min_freq_khz=_read_int(policy_path / "scaling_min_freq"),
                    scaling_max_freq_khz=_read_int(policy_path / "scaling_max_freq"),
                    epp=_read_text(policy_path / "energy_performance_preference"),
                    available_epp=_read_word_list(
                        policy_path / "energy_performance_available_preferences"
                    ),
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _constraint_indexes(zone_path: Path) -> tuple[int, ...]:
        indexes: set[int] = set()
        try:
            for path in zone_path.glob("constraint_*_*"):
                suffix = path.name.removeprefix("constraint_")
                index_text, separator, _attribute = suffix.partition("_")
                if separator and index_text.isdigit():
                    indexes.add(int(index_text))
        except OSError:
            return ()
        return tuple(sorted(indexes))

    @staticmethod
    def _constraints(
        zone_path: Path,
    ) -> tuple[PowercapConstraintSnapshot, ...]:
        snapshots: list[PowercapConstraintSnapshot] = []
        for index in IntelPowerDiscovery._constraint_indexes(zone_path):
            prefix = zone_path / f"constraint_{index}"
            snapshots.append(
                PowercapConstraintSnapshot(
                    index=index,
                    name=_read_text(Path(f"{prefix}_name")),
                    power_limit_uw=_read_int(Path(f"{prefix}_power_limit_uw")),
                    time_window_us=_read_int(Path(f"{prefix}_time_window_us")),
                    min_power_uw=_read_int(Path(f"{prefix}_min_power_uw")),
                    max_power_uw=_read_int(Path(f"{prefix}_max_power_uw")),
                    min_time_window_us=_read_int(
                        Path(f"{prefix}_min_time_window_us")
                    ),
                    max_time_window_us=_read_int(
                        Path(f"{prefix}_max_time_window_us")
                    ),
                )
            )
        return tuple(snapshots)

    @staticmethod
    def _is_powercap_zone(path: Path) -> bool:
        indicators = (
            path / "name",
            path / "energy_uj",
            path / "max_energy_range_uj",
        )
        if any(indicator.exists() for indicator in indicators):
            return True
        try:
            return next(path.glob("constraint_*_name"), None) is not None
        except OSError:
            return False

    @staticmethod
    def _zone_children(path: Path, boundary: Path) -> tuple[Path, ...]:
        try:
            children = sorted(path.iterdir(), key=lambda child: child.name)
        except OSError:
            return ()

        zones: list[Path] = []
        for child in children:
            try:
                if not child.is_dir():
                    continue
                resolved = child.resolve(strict=True)
            except OSError:
                continue
            if not _is_within(resolved, boundary):
                continue
            if IntelPowerDiscovery._is_powercap_zone(child):
                zones.append(child)
        return tuple(zones)

    def powercap_zone_paths(self) -> tuple[tuple[Path, Optional[str]], ...]:
        """Return canonical Powercap zones using existing safety rules."""
        return self._powercap_zone_paths()

    def _powercap_zone_paths(self) -> tuple[tuple[Path, Optional[str]], ...]:
        try:
            root_children = sorted(
                self.powercap_root.iterdir(),
                key=lambda child: child.name,
            )
        except OSError:
            return ()

        control_types: list[tuple[Path, Path]] = []
        root_aliases: list[Path] = []

        for child in root_children:
            try:
                if not child.is_dir():
                    continue
                resolved = child.resolve(strict=True)
            except OSError:
                continue

            if self._is_powercap_zone(child):
                root_aliases.append(child)
                continue

            zone_children = self._zone_children(child, resolved)
            if zone_children:
                control_types.append((child, resolved))

        seen_resolved: set[Path] = set()
        zones: list[tuple[Path, Optional[str]]] = []

        def visit_zone(
            zone_path: Path,
            control_type: Optional[str],
            boundary: Path,
        ) -> None:
            try:
                resolved = zone_path.resolve(strict=True)
            except OSError:
                return
            if not _is_within(resolved, boundary) or resolved in seen_resolved:
                return
            if not self._is_powercap_zone(zone_path):
                return

            seen_resolved.add(resolved)
            zones.append((zone_path, control_type))
            for child in self._zone_children(zone_path, boundary):
                visit_zone(child, control_type, boundary)

        for control_path, boundary in control_types:
            for zone_path in self._zone_children(control_path, boundary):
                visit_zone(zone_path, control_path.name, boundary)

        # Some kernels expose convenient top-level zone aliases in the class
        # directory. Use them only as a fallback: canonical control-type paths
        # above win through resolved-path deduplication.
        for alias in root_aliases:
            try:
                boundary = alias.resolve(strict=True)
            except OSError:
                continue
            visit_zone(alias, None, boundary)

        return tuple(zones)

    def _powercap_zones(
        self,
        sample_energy: bool,
    ) -> tuple[PowercapZoneSnapshot, ...]:
        snapshots: list[PowercapZoneSnapshot] = []
        for zone_path, control_type in self._powercap_zone_paths():
            try:
                zone_id = str(zone_path.relative_to(self.powercap_root))
            except ValueError:
                zone_id = zone_path.name

            energy = _read_int(zone_path / "energy_uj")
            max_range = _read_int(zone_path / "max_energy_range_uj")
            power_sample = None
            if sample_energy and energy.status is ReadStatus.AVAILABLE:
                max_range_value = (
                    max_range.value
                    if max_range.status is ReadStatus.AVAILABLE
                    else None
                )
                power_sample = self._energy_sampler.sample(
                    zone_id,
                    energy.value or 0,
                    max_range_value,
                )

            snapshots.append(
                PowercapZoneSnapshot(
                    zone_id=zone_id,
                    control_type=control_type,
                    sysfs_name=zone_path.name,
                    name=_read_text(zone_path / "name"),
                    energy_uj=energy,
                    max_energy_range_uj=max_range,
                    constraints=self._constraints(zone_path),
                    power_sample=power_sample,
                )
            )
        return tuple(snapshots)

    def _thermal_packages(
        self,
        topology: tuple[CpuTopologySnapshot, ...],
    ) -> tuple[ThermalThrottleSnapshot, ...]:
        package_cpus: dict[int, list[int]] = {}
        for cpu in topology:
            package = cpu.physical_package_id
            if package.status is not ReadStatus.AVAILABLE or package.value is None:
                continue
            package_cpus.setdefault(package.value, []).append(cpu.cpu_id)

        snapshots: list[ThermalThrottleSnapshot] = []
        for package_id in sorted(package_cpus):
            for cpu_id in sorted(package_cpus[package_id]):
                thermal = self.cpu_root / f"cpu{cpu_id}" / "thermal_throttle"
                count = _read_int(thermal / "package_throttle_count")
                total = _read_int(thermal / "package_throttle_total_time_ms")
                maximum = _read_int(thermal / "package_throttle_max_time_ms")

                if all(
                    result.status is ReadStatus.MISSING
                    for result in (count, total, maximum)
                ):
                    continue

                snapshots.append(
                    ThermalThrottleSnapshot(
                        package_id=package_id,
                        representative_cpu=cpu_id,
                        throttle_count=count,
                        total_time_ms=total,
                        max_time_ms=maximum,
                    )
                )
                break

        return tuple(snapshots)

    def snapshot(self, sample_energy: bool = False) -> IntelPowerSnapshot:
        intel_pstate = self.cpu_root / "intel_pstate"
        topology = self._cpu_topology()
        return IntelPowerSnapshot(
            intel_pstate_status=_read_text(intel_pstate / "status"),
            turbo_allowed=_read_bool01(intel_pstate / "no_turbo", invert=True),
            hwp_dynamic_boost=_read_bool01(intel_pstate / "hwp_dynamic_boost"),
            cpu_topology=topology,
            cpufreq_policies=self._cpufreq_policies(),
            powercap_zones=self._powercap_zones(sample_energy),
            thermal_packages=self._thermal_packages(topology),
        )


intel_power = IntelPowerDiscovery()
