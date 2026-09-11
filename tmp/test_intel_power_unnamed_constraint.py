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

        zone = powercap_root / "intel-rapl" / "intel-rapl:0"
        write(powercap_root, "intel-rapl/intel-rapl:0/name", "package-0\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/energy_uj", "1000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/max_energy_range_uj", "100000000\n")
        # The kernel ABI makes constraint_X_name optional. Mandatory constraint
        # attributes must still make the constraint discoverable.
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_0_power_limit_uw", "15000000\n")
        write(powercap_root, "intel-rapl/intel-rapl:0/constraint_0_time_window_us", "28000000\n")

        snapshot = IntelPowerDiscovery(
            cpu_root=cpu_root,
            powercap_root=powercap_root,
        ).snapshot()

        assert len(snapshot.powercap_zones) == 1
        constraint = snapshot.powercap_zones[0].constraints[0]
        assert constraint.index == 0
        assert constraint.name.status is ReadStatus.MISSING
        assert constraint.power_limit_uw.value == 15_000_000
        assert constraint.time_window_us.value == 28_000_000

    print("unnamed Powercap constraint check passed")


if __name__ == "__main__":
    main()
