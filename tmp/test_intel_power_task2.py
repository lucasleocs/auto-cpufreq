import tempfile
from pathlib import Path

from auto_cpufreq.modules.intel_power import (
    EnergySampler,
    IntelPowerDiscovery,
    ReadStatus,
)


def write(root: Path, relative: str, value: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        cpu_root = root / "cpu"
        powercap_root = root / "powercap"
        cpu_root.mkdir()
        powercap_root.mkdir()

        write(powercap_root, "intel-rapl/enabled", "1\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/name", "package-0\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/energy_uj", "90000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/max_energy_range_uj", "100000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_0_name", "long_term\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_0_power_limit_uw", "15000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_0_time_window_us", "28000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_1_name", "short_term\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_1_power_limit_uw", "35000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/intel-rapl:0:0/name", "core\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/intel-rapl:0:0/energy_uj", "45000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/intel-rapl:0:0/max_energy_range_uj", "100000000\n")
        write(powercap_root, "intel-rapl-mmio/enabled", "1\n")
        write(powercap_root, "intel-rapl-mmio/intel-rapl-mmio:0/name", "package-0\n")
        write(powercap_root, "intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_name", "long_term\n")
        write(powercap_root, "intel-rapl-mmio/intel-rapl-mmio:0/constraint_0_power_limit_uw", "12000000\n")

        escape = root / "outside-powercap"
        write(escape, "name", "not-a-powercap-zone\n")
        (powercap_root / "intel-rapl/intel-rapl:0/device").symlink_to(escape, target_is_directory=True)

        snapshot = IntelPowerDiscovery(cpu_root=cpu_root, powercap_root=powercap_root).snapshot()
        assert len(snapshot.powercap_zones) == 3
        zone_ids = {zone.zone_id for zone in snapshot.powercap_zones}
        assert len(zone_ids) == 3
        msr_pkg = next(zone for zone in snapshot.powercap_zones if zone.zone_id == "intel-rapl/intel-rapl:0")
        mmio_pkg = next(zone for zone in snapshot.powercap_zones if zone.zone_id == "intel-rapl-mmio/intel-rapl-mmio:0")
        assert msr_pkg.control_type == "intel-rapl"
        assert mmio_pkg.control_type == "intel-rapl-mmio"
        assert msr_pkg.name.value == "package-0"
        assert mmio_pkg.name.value == "package-0"
        assert msr_pkg.zone_id != mmio_pkg.zone_id
        assert all(zone.name.value != "not-a-powercap-zone" for zone in snapshot.powercap_zones)
        assert [item.name.value for item in msr_pkg.constraints] == ["long_term", "short_term"]
        assert msr_pkg.constraints[0].power_limit_uw.value == 15000000
        assert msr_pkg.constraints[0].time_window_us.value == 28000000
        assert msr_pkg.constraints[0].min_power_uw.status is ReadStatus.MISSING

    sampler = EnergySampler()
    assert sampler.sample("pkg-msr", 90_000_000, 100_000_000, now_ns=0) is None
    sample = sampler.sample("pkg-msr", 10_000_000, 100_000_000, now_ns=1_000_000_000)
    assert sample is not None
    assert sample.delta_energy_uj == 20_000_000
    assert sample.elapsed_ns == 1_000_000_000
    assert sample.average_power_w == 20.0

    no_range = EnergySampler()
    assert no_range.sample("pkg", 10, None, now_ns=1) is None
    assert no_range.sample("pkg", 5, None, now_ns=2) is None
    assert no_range.sample("pkg", 8, None, now_ns=3) is not None

    non_positive = EnergySampler()
    assert non_positive.sample("pkg", 10, 100, now_ns=10) is None
    assert non_positive.sample("pkg", 20, 100, now_ns=10) is None

    separate = EnergySampler()
    assert separate.sample("msr/package-0", 10, 100, now_ns=0) is None
    assert separate.sample("mmio/package-0", 20, 100, now_ns=0) is None
    assert separate.sample("msr/package-0", 20, 100, now_ns=1_000_000_000) is not None
    assert separate.sample("mmio/package-0", 25, 100, now_ns=1_000_000_000) is not None

    print("task2 powercap and energy checks passed")


if __name__ == "__main__":
    main()
