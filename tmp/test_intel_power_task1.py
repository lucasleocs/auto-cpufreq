import tempfile
from pathlib import Path

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery, ReadStatus


def write(root: Path, relative: str, value: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        cpu_root = Path(temp_dir) / "cpu"
        powercap_root = Path(temp_dir) / "powercap"
        powercap_root.mkdir(parents=True)

        write(cpu_root, "intel_pstate/status", "active\n")
        write(cpu_root, "intel_pstate/no_turbo", "0\n")
        write(cpu_root, "intel_pstate/hwp_dynamic_boost", "1\n")

        write(cpu_root, "cpu0/topology/physical_package_id", "0\n")
        write(cpu_root, "cpu0/topology/core_id", "0\n")
        write(cpu_root, "cpu0/topology/thread_siblings_list", "0,4\n")
        write(cpu_root, "cpu1/topology/physical_package_id", "0\n")
        write(cpu_root, "cpu1/topology/core_id", "1\n")
        write(cpu_root, "cpu1/topology/thread_siblings_list", "1\n")

        write(cpu_root, "cpufreq/policy0/scaling_driver", "intel_pstate\n")
        write(cpu_root, "cpufreq/policy0/scaling_governor", "powersave\n")
        write(cpu_root, "cpufreq/policy0/related_cpus", "0 2\n")
        write(cpu_root, "cpufreq/policy0/cpuinfo_min_freq", "400000\n")
        write(cpu_root, "cpufreq/policy0/cpuinfo_max_freq", "4500000\n")
        write(cpu_root, "cpufreq/policy0/scaling_min_freq", "400000\n")
        write(cpu_root, "cpufreq/policy0/scaling_max_freq", "4500000\n")
        write(cpu_root, "cpufreq/policy0/energy_performance_preference", "balance_performance\n")
        write(
            cpu_root,
            "cpufreq/policy0/energy_performance_available_preferences",
            "default performance balance_performance balance_power power\n",
        )

        write(cpu_root, "cpufreq/policy1/scaling_driver", "intel_pstate\n")
        write(cpu_root, "cpufreq/policy1/scaling_governor", "powersave\n")
        write(cpu_root, "cpufreq/policy1/related_cpus", "1 3\n")

        snapshot = IntelPowerDiscovery(
            cpu_root=cpu_root,
            powercap_root=powercap_root,
        ).snapshot()

        assert snapshot.intel_pstate_status.value == "active"
        assert snapshot.turbo_allowed.status is ReadStatus.AVAILABLE
        assert snapshot.turbo_allowed.value is True
        assert snapshot.hwp_dynamic_boost.status is ReadStatus.AVAILABLE
        assert snapshot.hwp_dynamic_boost.value is True
        assert len(snapshot.cpufreq_policies) == 2
        assert snapshot.cpufreq_policies[0].related_cpus.value == (0, 2)
        assert snapshot.cpufreq_policies[1].related_cpus.value == (1, 3)
        assert len(snapshot.cpu_topology) == 2
        assert snapshot.cpu_topology[0].thread_siblings.value == (0, 4)

        malformed = cpu_root / "cpufreq/policy1/related_cpus"
        malformed.write_text("4-2\n")
        (cpu_root / "cpufreq/policy1/energy_performance_preference").mkdir()

        snapshot = IntelPowerDiscovery(
            cpu_root=cpu_root,
            powercap_root=powercap_root,
        ).snapshot()
        assert snapshot.cpufreq_policies[1].related_cpus.status is ReadStatus.INVALID
        assert snapshot.cpufreq_policies[1].epp.status is ReadStatus.UNREADABLE

    print("task1 synthetic sysfs checks passed")


if __name__ == "__main__":
    main()
