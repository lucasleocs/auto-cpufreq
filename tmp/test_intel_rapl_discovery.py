#!/usr/bin/env python3
from pathlib import Path
from tempfile import TemporaryDirectory

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery
from auto_cpufreq.modules.intel_rapl import IntelRaplController


def write(path: Path, value) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")


def add_named_constraint(zone: Path, index: int, name: str, power: int) -> None:
    write(zone / f"constraint_{index}_name", name)
    write(zone / f"constraint_{index}_power_limit_uw", power)
    write(zone / f"constraint_{index}_min_power_uw", 1_000_000)
    write(zone / f"constraint_{index}_max_power_uw", 50_000_000)


def add_control_tree(root: Path, control_type: str) -> Path:
    control = root / control_type
    control.mkdir()
    package = control / f"{control_type}:0"
    package.mkdir()
    write(package / "name", "package-0")
    add_named_constraint(package, 0, "long_term", 15_000_000)
    add_named_constraint(package, 1, "short_term", 30_000_000)

    # Valid but unnamed constraints remain telemetry-only.
    write(package / "constraint_2_power_limit_uw", 40_000_000)

    # Subzones must never be selected for package envelope control.
    core = package / f"{control_type}:0:0"
    core.mkdir()
    write(core / "name", "core")
    add_named_constraint(core, 0, "long_term", 5_000_000)

    # A class-level alias must deduplicate against the canonical path.
    (root / f"alias-{control_type}:0").symlink_to(package, target_is_directory=True)
    return package


def main() -> None:
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()

        package_msr = add_control_tree(root, "intel-rapl")
        package_mmio = add_control_tree(root, "intel-rapl-mmio")

        # A different control type may expose similarly named package zones but
        # is outside the initial Intel RAPL controller scope.
        other = root / "vendor-powercap"
        other.mkdir()
        other_package = other / "vendor:0"
        other_package.mkdir()
        write(other_package / "name", "package-0")
        add_named_constraint(other_package, 0, "long_term", 10_000_000)

        # Symlinks leaving a canonical control-type boundary are rejected.
        outside = base / "outside-package"
        outside.mkdir()
        write(outside / "name", "package-escape")
        add_named_constraint(outside, 0, "long_term", 9_000_000)
        (root / "intel-rapl" / "intel-rapl:99").symlink_to(
            outside,
            target_is_directory=True,
        )

        discovery = IntelPowerDiscovery(powercap_root=root)

        zone_paths = discovery.powercap_zone_paths()
        resolved = [path.resolve() for path, _control_type in zone_paths]
        assert resolved.count(package_msr.resolve()) == 1
        assert resolved.count(package_mmio.resolve()) == 1
        assert outside.resolve() not in resolved

        controller = IntelRaplController(discovery=discovery)
        refs = controller.discover_package_constraints()

        assert len(refs) == 4, refs
        assert {
            (ref.control_type, ref.zone_name, ref.constraint_name)
            for ref in refs
        } == {
            ("intel-rapl", "package-0", "long_term"),
            ("intel-rapl", "package-0", "short_term"),
            ("intel-rapl-mmio", "package-0", "long_term"),
            ("intel-rapl-mmio", "package-0", "short_term"),
        }

        for ref in refs:
            assert ref.zone_id in {
                "intel-rapl/intel-rapl:0",
                "intel-rapl-mmio/intel-rapl-mmio:0",
            }
            assert ref.constraint_index in (0, 1)
            assert ref.power_limit_path.name == (
                f"constraint_{ref.constraint_index}_power_limit_uw"
            )
            assert ref.min_power_path.name == (
                f"constraint_{ref.constraint_index}_min_power_uw"
            )
            assert ref.max_power_path.name == (
                f"constraint_{ref.constraint_index}_max_power_uw"
            )

    print("Intel RAPL controllable discovery checks passed")


if __name__ == "__main__":
    main()
