from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import time
from typing import Generic, Optional, TypeVar


T = TypeVar("T")


class ReadStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    INVALID = "invalid"


@dataclass(frozen=True)
class ReadResult(Generic[T]):
    status: ReadStatus
    value: Optional[T] = None


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
    sysfs_name: str
    name: ReadResult[str]
    energy_uj: ReadResult[int]
    max_energy_range_uj: ReadResult[int]
    constraints: tuple[PowercapConstraintSnapshot, ...]
    power_sample: Optional[PowerSample] = None


@dataclass(frozen=True)
class IntelPowerSnapshot:
    intel_pstate_status: ReadResult[str]
    turbo_allowed: ReadResult[bool]
    hwp_dynamic_boost: ReadResult[bool]
    cpu_topology: tuple[CpuTopologySnapshot, ...]
    cpufreq_policies: tuple[CpuFreqPolicySnapshot, ...]
    powercap_zones: tuple[PowercapZoneSnapshot, ...] = ()
    thermal_packages: tuple = ()


def _read_text(path: Path) -> ReadResult[str]:
    try:
        return ReadResult(ReadStatus.AVAILABLE, path.read_text().strip())
    except FileNotFoundError:
        return ReadResult(ReadStatus.MISSING)
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)


def _read_int(path: Path) -> ReadResult[int]:
    result = _read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(result.value))
    except (TypeError, ValueError):
        return ReadResult(ReadStatus.INVALID)


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


def _read_bool01(path: Path, invert: bool = False) -> ReadResult[bool]:
    result = _read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value not in ("0", "1"):
        return ReadResult(ReadStatus.INVALID)
    value = result.value == "1"
    return ReadResult(ReadStatus.AVAILABLE, not value if invert else value)


def _numeric_suffix(path: Path, prefix: str) -> tuple[int, str]:
    suffix = path.name.removeprefix(prefix)
    try:
        return int(suffix), path.name
    except ValueError:
        return 2**31 - 1, path.name


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
            candidates = zone_path.glob("constraint_*_name")
            for path in candidates:
                suffix = path.name.removeprefix("constraint_").removesuffix("_name")
                if suffix.isdigit():
                    indexes.add(int(suffix))
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

    def _powercap_zone_paths(self) -> tuple[Path, ...]:
        if not self.powercap_root.exists():
            return ()

        queue: list[Path] = [self.powercap_root]
        seen_dirs: set[Path] = set()
        zones: list[Path] = []

        while queue:
            directory = queue.pop(0)
            try:
                resolved = directory.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen_dirs:
                continue
            seen_dirs.add(resolved)

            try:
                children = sorted(directory.iterdir(), key=lambda path: path.name)
            except OSError:
                continue

            for child in children:
                try:
                    if child.is_dir():
                        queue.append(child)
                except OSError:
                    continue

            if directory != self.powercap_root and self._is_powercap_zone(directory):
                zones.append(directory)

        return tuple(sorted(zones, key=lambda path: str(path)))

    def _powercap_zones(
        self,
        sample_energy: bool,
    ) -> tuple[PowercapZoneSnapshot, ...]:
        snapshots: list[PowercapZoneSnapshot] = []
        for zone_path in self._powercap_zone_paths():
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
                    sysfs_name=zone_path.name,
                    name=_read_text(zone_path / "name"),
                    energy_uj=energy,
                    max_energy_range_uj=max_range,
                    constraints=self._constraints(zone_path),
                    power_sample=power_sample,
                )
            )
        return tuple(snapshots)

    def snapshot(self, sample_energy: bool = False) -> IntelPowerSnapshot:
        intel_pstate = self.cpu_root / "intel_pstate"
        return IntelPowerSnapshot(
            intel_pstate_status=_read_text(intel_pstate / "status"),
            turbo_allowed=_read_bool01(intel_pstate / "no_turbo", invert=True),
            hwp_dynamic_boost=_read_bool01(intel_pstate / "hwp_dynamic_boost"),
            cpu_topology=self._cpu_topology(),
            cpufreq_policies=self._cpufreq_policies(),
            powercap_zones=self._powercap_zones(sample_energy),
        )
