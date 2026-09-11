#!/usr/bin/env python3
import json
import stat
from pathlib import Path
from tempfile import TemporaryDirectory

import auto_cpufreq.modules.intel_rapl as rapl
from auto_cpufreq.modules.intel_rapl import (
    RaplConstraintIdentity,
    RaplOwnershipRecord,
    RaplStateError,
    RaplStateStore,
)


def identity(index=0, name="long_term"):
    return RaplConstraintIdentity(
        control_type="intel-rapl",
        zone_id="intel-rapl/intel-rapl:0",
        zone_name="package-0",
        constraint_index=index,
        constraint_name=name,
    )


def record(index=0, name="long_term", original=28_000_000, last=20_000_000):
    return RaplOwnershipRecord(
        identity=identity(index, name),
        original_power_limit_uw=original,
        last_written_power_limit_uw=last,
    )


def expect_state_error(fn):
    try:
        fn()
    except RaplStateError:
        return
    raise AssertionError("expected RaplStateError")


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp) / "runtime"
        path = root / "power-state.json"
        store = RaplStateStore(path)

        assert store.load() == ()

        records = (
            record(),
            RaplOwnershipRecord(
                identity=RaplConstraintIdentity(
                    control_type="intel-rapl-mmio",
                    zone_id="intel-rapl-mmio/intel-rapl-mmio:0",
                    zone_name="package-0",
                    constraint_index=1,
                    constraint_name="short_term",
                ),
                original_power_limit_uw=41_000_000,
                last_written_power_limit_uw=30_000_000,
            ),
        )

        replace_calls = []
        real_replace = rapl.os.replace

        def recording_replace(src, dst):
            replace_calls.append((Path(src), Path(dst)))
            return real_replace(src, dst)

        rapl.os.replace = recording_replace
        try:
            store.save(records)
        finally:
            rapl.os.replace = real_replace

        assert store.load() == records
        assert len(replace_calls) == 1
        src, dst = replace_calls[0]
        assert src.parent == path.parent
        assert dst == path

        file_mode = stat.S_IMODE(path.stat().st_mode)
        dir_mode = stat.S_IMODE(root.stat().st_mode)
        assert file_mode == 0o600, oct(file_mode)
        assert dir_mode == 0o700, oct(dir_mode)
        assert not list(root.glob(f".{path.name}.*"))

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == 1
        assert len(payload["records"]) == 2

        # Saving a new state replaces the old complete document.
        store.save((records[0],))
        assert store.load() == (records[0],)

        # Empty ownership removes the state file entirely.
        store.save(())
        assert not path.exists()
        assert store.load() == ()

        # Malformed state never becomes an empty/overwriteable baseline.
        root.mkdir(mode=0o700, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        expect_state_error(store.load)

        # Unknown schema versions fail closed.
        path.write_text(
            json.dumps({"version": 2, "records": []}),
            encoding="utf-8",
        )
        expect_state_error(store.load)

        # Duplicate identities are rejected on load.
        duplicate = {
            "identity": {
                "control_type": "intel-rapl",
                "zone_id": "intel-rapl/intel-rapl:0",
                "zone_name": "package-0",
                "constraint_index": 0,
                "constraint_name": "long_term",
            },
            "original_power_limit_uw": 28_000_000,
            "last_written_power_limit_uw": 20_000_000,
        }
        path.write_text(
            json.dumps({"version": 1, "records": [duplicate, duplicate]}),
            encoding="utf-8",
        )
        expect_state_error(store.load)

        # Bad integer/string fields also fail closed.
        invalid = dict(duplicate)
        invalid["original_power_limit_uw"] = 0
        path.write_text(
            json.dumps({"version": 1, "records": [invalid]}),
            encoding="utf-8",
        )
        expect_state_error(store.load)

        # Explicit clear handles both present and absent files.
        path.write_text(
            json.dumps({"version": 1, "records": [duplicate]}),
            encoding="utf-8",
        )
        store.clear()
        assert not path.exists()
        store.clear()

    print("Intel RAPL ownership persistence checks passed")


if __name__ == "__main__":
    main()
