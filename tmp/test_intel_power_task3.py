import tempfile
from pathlib import Path

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery, ReadStatus


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

        write(cpu_root, "cpu0/topology/physical_package_id", "0\n")
        write(cpu_root, "cpu0/thermal_throttle/package_throttle_count", "12\n")
        write(
            cpu_root,
            "cpu0/thermal_throttle/package_throttle_total_time_ms",
            "300\n",
        )
        write(
            cpu_root,
            "cpu0/thermal_throttle/package_throttle_max_time_ms",
            "40\n",
        )

        # Same package-level hardware event is visible from another logical CPU.
        # Discovery must not add it a second time.
        write(cpu_root, "cpu1/topology/physical_package_id", "0\n")
        write(cpu_root, "cpu1/thermal_throttle/package_throttle_count", "12\n")
        write(
            cpu_root,
            "cpu1/thermal_throttle/package_throttle_total_time_ms",
            "300\n",
        )

        write(cpu_root, "cpu2/topology/physical_package_id", "1\n")
        write(cpu_root, "cpu2/thermal_throttle/package_throttle_count", "4\n")

        # Thermal files without known package topology must never become package 0.
        write(cpu_root, "cpu3/thermal_throttle/package_throttle_count", "999\n")

        # Known package with no package thermal-throttle ABI should be omitted.
        write(cpu_root, "cpu4/topology/physical_package_id", "2\n")

        snapshot = IntelPowerDiscovery(
            cpu_root=cpu_root,
            powercap_root=powercap_root,
        ).snapshot()

        assert len(snapshot.thermal_packages) == 2
        package0 = next(item for item in snapshot.thermal_packages if item.package_id == 0)
        package1 = next(item for item in snapshot.thermal_packages if item.package_id == 1)

        assert package0.representative_cpu == 0
        assert package0.throttle_count.value == 12
        assert package0.total_time_ms.value == 300
        assert package0.max_time_ms.value == 40

        assert package1.representative_cpu == 2
        assert package1.throttle_count.value == 4
        assert package1.total_time_ms.status is ReadStatus.MISSING
        assert package1.max_time_ms.status is ReadStatus.MISSING

        assert all(item.package_id != 2 for item in snapshot.thermal_packages)
        assert all(item.throttle_count.value != 999 for item in snapshot.thermal_packages)

        # If the lowest-numbered CPU in a known package has no thermal ABI,
        # discovery must select the lowest CPU that actually exposes it.
        write(cpu_root, "cpu5/topology/physical_package_id", "3\n")
        write(cpu_root, "cpu6/topology/physical_package_id", "3\n")
        write(cpu_root, "cpu6/thermal_throttle/package_throttle_count", "7\n")
        second = IntelPowerDiscovery(
            cpu_root=cpu_root,
            powercap_root=powercap_root,
        ).snapshot()
        package3 = next(item for item in second.thermal_packages if item.package_id == 3)
        assert package3.representative_cpu == 6
        assert package3.throttle_count.value == 7

    print("task3 thermal throttle checks passed")


if __name__ == "__main__":
    main()
