#!/usr/bin/env python3
from pathlib import Path
from tempfile import TemporaryDirectory

import auto_cpufreq.modules.intel_rapl as rapl
from auto_cpufreq.modules.intel_power import IntelPowerDiscovery
from auto_cpufreq.modules.intel_rapl import (
    IntelRaplController,
    RaplConstraintIdentity,
    RaplEnvelopeTargets,
    RaplOwnershipRecord,
    RaplStateStore,
)


def write(path: Path, value) -> None:
    path.write_text(f"{value}\n", encoding="utf-8")


def package_zone(
    root: Path,
    control_type: str,
    *,
    long_term: int,
    short_term: int = 41_000_000,
    long_min: int = 1_000_000,
    long_max: int = 15_000_000,
) -> Path:
    control = root / control_type
    control.mkdir(parents=True, exist_ok=True)
    zone = control / f"{control_type}:0"
    zone.mkdir()
    write(zone / "name", "package-0")

    write(zone / "constraint_0_name", "long_term")
    write(zone / "constraint_0_power_limit_uw", long_term)
    write(zone / "constraint_0_min_power_uw", long_min)
    write(zone / "constraint_0_max_power_uw", long_max)

    write(zone / "constraint_1_name", "short_term")
    write(zone / "constraint_1_power_limit_uw", short_term)
    write(zone / "constraint_1_min_power_uw", 1_000_000)
    write(zone / "constraint_1_max_power_uw", 60_000_000)
    return zone


def identity(control_type="intel-rapl", index=0, name="long_term"):
    return RaplConstraintIdentity(
        control_type=control_type,
        zone_id=f"{control_type}/{control_type}:0",
        zone_name="package-0",
        constraint_index=index,
        constraint_name=name,
    )


def ownership(
    *,
    original=28_000_000,
    last=20_000_000,
    control_type="intel-rapl",
    index=0,
    name="long_term",
):
    return RaplOwnershipRecord(
        identity=identity(control_type, index, name),
        original_power_limit_uw=original,
        last_written_power_limit_uw=last,
    )


def controller(root: Path, state_path: Path) -> IntelRaplController:
    return IntelRaplController(
        discovery=IntelPowerDiscovery(powercap_root=root),
        state_store=RaplStateStore(state_path),
    )


def long_path(zone: Path) -> Path:
    return zone / "constraint_0_power_limit_uw"


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def test_first_acquisition_lowers_and_owns():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=28_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = IntelRaplController(
            discovery=IntelPowerDiscovery(powercap_root=root),
            state_store=store,
        )

        results = ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        assert read_int(long_path(zone)) == 20_000_000
        records = store.load()
        assert records == (ownership(),)
        assert any(result.action == "acquired" for result in results)


def test_first_acquisition_never_raises_or_claims_equal_external_limit():
    for current, target in ((15_000_000, 20_000_000), (15_000_000, 15_000_000)):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "powercap"
            root.mkdir()
            zone = package_zone(root, "intel-rapl", long_term=current)
            store = RaplStateStore(base / "state" / "power-state.json")
            ctl = controller(root, store.path)
            ctl.apply(RaplEnvelopeTargets(long_term_uw=target))
            assert read_int(long_path(zone)) == current
            assert store.load() == ()


def test_owned_value_may_move_within_original_but_not_above_it():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=20_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        store.save((ownership(),))
        ctl = controller(root, store.path)

        ctl.apply(RaplEnvelopeTargets(long_term_uw=24_000_000))
        assert read_int(long_path(zone)) == 24_000_000
        assert store.load()[0].last_written_power_limit_uw == 24_000_000

        ctl.apply(RaplEnvelopeTargets(long_term_uw=30_000_000))
        assert read_int(long_path(zone)) == 24_000_000
        assert store.load()[0].last_written_power_limit_uw == 24_000_000


def test_drift_drops_ownership_without_write_war():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=18_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        store.save((ownership(),))
        ctl = controller(root, store.path)

        results = ctl.apply(RaplEnvelopeTargets(long_term_uw=15_000_000))
        assert read_int(long_path(zone)) == 18_000_000
        assert store.load() == ()
        assert any(result.action == "drift" for result in results)


def test_omitted_owned_target_restores_original():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=20_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        store.save((ownership(),))
        ctl = controller(root, store.path)

        results = ctl.apply(RaplEnvelopeTargets())
        assert read_int(long_path(zone)) == 28_000_000
        assert store.load() == ()
        assert any(result.action == "restored" for result in results)


def test_failed_or_unverifiable_first_write_never_establishes_ownership():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=28_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        real_write = rapl._write_int
        rapl._write_int = lambda path, value: (_ for _ in ()).throw(
            PermissionError("locked")
        )
        try:
            results = ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        finally:
            rapl._write_int = real_write
        assert read_int(long_path(zone)) == 28_000_000
        assert store.load() == ()
        assert any(result.action == "write-failed" for result in results)

    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=28_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        real_write = rapl._write_int
        rapl._write_int = lambda path, value: None
        try:
            results = ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        finally:
            rapl._write_int = real_write
        assert read_int(long_path(zone)) == 28_000_000
        assert store.load() == ()
        assert any(result.action == "readback-rejected" for result in results)


def test_quantized_readback_becomes_effective_owned_value():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=28_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        real_write = rapl._write_int
        rapl._write_int = lambda path, value: write(path, 20_500_000)
        try:
            ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        finally:
            rapl._write_int = real_write

        assert read_int(long_path(zone)) == 20_500_000
        record = store.load()[0]
        assert record.original_power_limit_uw == 28_000_000
        assert record.last_written_power_limit_uw == 20_500_000


def test_unexpected_readback_above_original_is_restored_and_not_owned():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=28_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        calls = []
        real_write = rapl._write_int

        def unsafe_then_normal(path, value):
            calls.append(value)
            if len(calls) == 1:
                write(path, 30_000_000)
            else:
                write(path, value)

        rapl._write_int = unsafe_then_normal
        try:
            results = ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        finally:
            rapl._write_int = real_write

        assert calls == [20_000_000, 28_000_000]
        assert read_int(long_path(zone)) == 28_000_000
        assert store.load() == ()
        assert any(result.action == "unsafe-readback-restored" for result in results)


def test_msr_and_mmio_are_independent_and_stricter_external_limit_is_not_raised():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        msr = package_zone(root, "intel-rapl", long_term=28_000_000)
        mmio = package_zone(root, "intel-rapl-mmio", long_term=15_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        assert read_int(long_path(msr)) == 20_000_000
        assert read_int(long_path(mmio)) == 15_000_000
        records = store.load()
        assert len(records) == 1
        assert records[0].identity.control_type == "intel-rapl"


def test_positive_min_is_enforced_but_samsung_style_max_metadata_is_not_blind_ceiling():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(
            root,
            "intel-rapl",
            long_term=28_000_000,
            long_min=10_000_000,
            long_max=15_000_000,
        )
        store = RaplStateStore(base / "state" / "power-state.json")
        ctl = controller(root, store.path)

        ctl.apply(RaplEnvelopeTargets(long_term_uw=9_000_000))
        assert read_int(long_path(zone)) == 28_000_000
        assert store.load() == ()

        ctl.apply(RaplEnvelopeTargets(long_term_uw=20_000_000))
        assert read_int(long_path(zone)) == 20_000_000
        assert store.load()[0].original_power_limit_uw == 28_000_000


def test_restore_owned_does_not_overwrite_external_drift():
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "powercap"
        root.mkdir()
        zone = package_zone(root, "intel-rapl", long_term=18_000_000)
        store = RaplStateStore(base / "state" / "power-state.json")
        store.save((ownership(),))
        ctl = controller(root, store.path)

        results = ctl.restore_owned()
        assert read_int(long_path(zone)) == 18_000_000
        assert store.load() == ()
        assert any(result.action == "retired-drift" for result in results)


def main():
    test_first_acquisition_lowers_and_owns()
    test_first_acquisition_never_raises_or_claims_equal_external_limit()
    test_owned_value_may_move_within_original_but_not_above_it()
    test_drift_drops_ownership_without_write_war()
    test_omitted_owned_target_restores_original()
    test_failed_or_unverifiable_first_write_never_establishes_ownership()
    test_quantized_readback_becomes_effective_owned_value()
    test_unexpected_readback_above_original_is_restored_and_not_owned()
    test_msr_and_mmio_are_independent_and_stricter_external_limit_is_not_raised()
    test_positive_min_is_enforced_but_samsung_style_max_metadata_is_not_blind_ceiling()
    test_restore_owned_does_not_overwrite_external_drift()
    print("Intel RAPL conservative control checks passed")


if __name__ == "__main__":
    main()
