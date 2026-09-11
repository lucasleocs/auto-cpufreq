# USB-C and USB-PD Power Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, factual `PowerContextSnapshot` for Linux `power_supply`, USB Type-C, and USB-PD data, expose it in `--debug` and `--stats`, and leave all daemon/policy/hardware-write behavior unchanged.

**Architecture:** Extract the existing generic sysfs read result model into `auto_cpufreq/modules/sysfs.py`, then add one focused `power_context.py` discovery module with injectable roots. `SystemInfo` collects one optional snapshot per report; the CLI/debug formatter shows detail and the stats UI shows a compact summary. No code path from the new snapshot reaches policy, daemon scheduling, RAPL, EPP, governor, turbo, Dynamic Boost, platform profile, or Type-C/PD writes.

**Tech Stack:** Python >=3.9, standard library (`dataclasses`, `enum`, `pathlib`, `re`, `tempfile`, `unittest`, `unittest.mock`), existing `urwid` reporting UI, Linux sysfs ABIs.

**Spec:** `docs/superpowers/specs/2026-09-11-usb-c-power-context-design.md`

## Global Constraints

- Development base is `usb-c-power-context-design`, itself based on `modern-intel-rapl-envelopes` at `d47b783d91ab616a863027069e1511e5d557bf22`.
- Create the implementation branch `usb-c-power-context` from the approved design/plan tip; do not rebase this work directly onto upstream `master`.
- First delivery is strictly read-only and observational.
- `PowerSource.CHARGER/BATTERY`, `charging()`, `SystemInfo.external_power_state()`, Modern Intel policy, Legacy policy, daemon triggers, watchdog behavior, RAPL ownership/failsafe, and platform-profile control remain unchanged.
- Do not introduce a permanent upstream test framework solely for this feature. TDD fixtures live under `/tmp/auto-cpufreq-power-context-tests/` and are never committed to the production branch.
- Do not add dependencies.
- Do not infer charger sufficiency, an active charger, negotiated wattage, or cross-subsystem identity.
- Do not calculate missing `power_now` from voltage/current.
- Do not correlate `power_supply`, Type-C, and USB-PD objects by name, index, or resolved-path similarity.
- Preserve multiple supplies, system batteries, Type-C ports, and USB-PD objects independently.
- `external_power` is `ONLINE | OFFLINE | UNKNOWN`; there is no `MIXED` external-power state.
- `battery_flow` is `CHARGING | DISCHARGING | NOT_CHARGING | FULL | MIXED | UNKNOWN`.
- A `scope=Device` battery may be observed in `power_supplies` but does not participate in `batteries` or the aggregate battery flow.
- Root missing => `MISSING`; root enumeration failure => `UNREADABLE`; readable empty root => `AVAILABLE` plus empty collection.
- The smallest meaningful unit carries `MISSING`, `UNREADABLE`, or `INVALID`; one bad attribute/PDO must not invalidate unrelated data.
- Production roots are injectable. Snap/hostfs resolution belongs to the integration layer, not the discovery readers.
- One report gets one collection pass; no long-lived PowerContext cache in v1.
- `--debug` gets detailed PowerContext diagnostics. `--stats` gets a compact factual summary. `--monitor` and `--live` remain unchanged.
- Linux Power Supply Class ABI is authoritative for `online`, `status`, units, and `input_*_limit` semantics.
- Linux Type-C ABI is authoritative for `power_role`, `power_operation_mode`, partner relationship, and `supports_usb_power_delivery` (`yes`/`no`).
- Linux USB-PD class ABI is optional. PDO voltage/current/power values are textual `mV`/`mA`/`mW` values and must be parsed with their documented suffixes.
- Current USB-PD capability kinds to support: `fixed_supply`, `variable_supply`, `battery`, `programmable_supply`, `spr_adjustable_voltage_supply`; unknown future kinds remain identifiable but uninterpreted.
- Existing Linux Build and Nix Flake workflows must remain green.

## File Structure

Production files:

- Create `auto_cpufreq/modules/sysfs.py` — shared `ReadStatus`, `ReadResult`, and truly generic sysfs scalar readers.
- Modify `auto_cpufreq/modules/intel_power.py` — import the shared primitives while preserving Intel behavior and existing public imports.
- Create `auto_cpufreq/modules/power_context.py` — all new power-supply, Type-C, USB-PD discovery, snapshots, parsing, aggregation, and root-status logic.
- Modify `auto_cpufreq/modules/system_info.py` — opt-in report collection, root/hostfs resolution, detailed/summary formatters.
- Modify `auto_cpufreq/modules/system_monitor.py` — request PowerContext only in STATS and render only the compact summary.
- Modify `auto_cpufreq/bin/auto_cpufreq.py` — request PowerContext in `--debug`; monitor/live/daemon paths remain unchanged.

Temporary verification files, never committed to production:

- `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`
- `/tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`

Optional validation documentation after all gates pass:

- `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md`

---

### Task 1: Extract Shared Sysfs Read Primitives Without Changing Intel Behavior

**Files:**
- Create: `auto_cpufreq/modules/sysfs.py`
- Modify: `auto_cpufreq/modules/intel_power.py` (current top-level read types/helpers, approximately lines 1-126)
- Temporary test: `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`

**Interfaces:**
- Produces: `ReadStatus`, `ReadResult[T]`, `read_text(Path) -> ReadResult[str]`, `read_int(Path) -> ReadResult[int]`, `read_bool01(Path, invert=False) -> ReadResult[bool]` from `auto_cpufreq.modules.sysfs`.
- Compatibility: `auto_cpufreq.modules.intel_power` must continue exposing `ReadStatus` and `ReadResult` through normal module imports because existing code imports them there.
- Keeps Intel-only `_parse_cpu_list`, `_read_cpu_list`, `_read_word_list`, `_numeric_suffix`, `_is_within`, and all discovery behavior in `intel_power.py`.

- [ ] **Step 1: Capture the pre-refactor Intel reader baseline**

Create `/tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py`:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
import json
from auto_cpufreq.modules import intel_power

with TemporaryDirectory() as td:
    root = Path(td)
    (root / "text").write_text("value\n")
    (root / "integer").write_text("42\n")
    (root / "invalid").write_text("not-an-int\n")
    (root / "true").write_text("1\n")
    (root / "false").write_text("0\n")
    results = {
        "text": (intel_power._read_text(root / "text").status.value, intel_power._read_text(root / "text").value),
        "integer": (intel_power._read_int(root / "integer").status.value, intel_power._read_int(root / "integer").value),
        "invalid": (intel_power._read_int(root / "invalid").status.value, intel_power._read_int(root / "invalid").value),
        "missing": (intel_power._read_text(root / "missing").status.value, intel_power._read_text(root / "missing").value),
        "true": (intel_power._read_bool01(root / "true").status.value, intel_power._read_bool01(root / "true").value),
        "false": (intel_power._read_bool01(root / "false").status.value, intel_power._read_bool01(root / "false").value),
        "inverted": (intel_power._read_bool01(root / "true", invert=True).status.value, intel_power._read_bool01(root / "true", invert=True).value),
    }
    print(json.dumps(results, sort_keys=True))
```

Run:

```bash
mkdir -p /tmp/auto-cpufreq-power-context-tests
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py \
  > /tmp/auto-cpufreq-power-context-tests/intel-reader-baseline.json
cat /tmp/auto-cpufreq-power-context-tests/intel-reader-baseline.json
```

Expected baseline contains `available`, `missing`, and `invalid` with the current values.

- [ ] **Step 2: Write the failing shared-reader test**

Create `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from auto_cpufreq.modules.sysfs import (
    ReadResult,
    ReadStatus,
    read_bool01,
    read_int,
    read_text,
)


class SysfsReaderTests(unittest.TestCase):
    def test_available_missing_invalid_and_bool(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "text").write_text("value\n")
            (root / "integer").write_text("42\n")
            (root / "invalid").write_text("bad\n")
            (root / "true").write_text("1\n")
            (root / "false").write_text("0\n")

            self.assertEqual(read_text(root / "text"), ReadResult(ReadStatus.AVAILABLE, "value"))
            self.assertEqual(read_text(root / "missing").status, ReadStatus.MISSING)
            self.assertEqual(read_int(root / "integer"), ReadResult(ReadStatus.AVAILABLE, 42))
            self.assertEqual(read_int(root / "invalid").status, ReadStatus.INVALID)
            self.assertEqual(read_bool01(root / "true").value, True)
            self.assertEqual(read_bool01(root / "false").value, False)
            self.assertEqual(read_bool01(root / "true", invert=True).value, False)

    def test_oserror_is_unreadable(self):
        target = Path("/synthetic/unreadable")
        with patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertEqual(read_text(target).status, ReadStatus.UNREADABLE)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run:

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py
```

Expected: FAIL at import because `auto_cpufreq.modules.sysfs` does not yet exist.

- [ ] **Step 4: Implement the minimal shared module and alias it into Intel discovery**

Create `auto_cpufreq/modules/sysfs.py` with exactly the current generic semantics:

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
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


def read_text(path: Path) -> ReadResult[str]:
    try:
        return ReadResult(ReadStatus.AVAILABLE, Path(path).read_text().strip())
    except FileNotFoundError:
        return ReadResult(ReadStatus.MISSING)
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)


def read_int(path: Path) -> ReadResult[int]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(result.value))
    except (TypeError, ValueError):
        return ReadResult(ReadStatus.INVALID)


def read_bool01(path: Path, invert: bool = False) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value not in ("0", "1"):
        return ReadResult(ReadStatus.INVALID)
    value = result.value == "1"
    return ReadResult(ReadStatus.AVAILABLE, not value if invert else value)
```

In `intel_power.py`, remove the duplicated `Enum`, `Generic`, `TypeVar`, `ReadStatus`, `ReadResult`, `_read_text`, `_read_int`, and `_read_bool01` definitions. Import the shared types and alias the functions so the rest of the Intel file stays mechanically unchanged:

```python
from auto_cpufreq.modules.sysfs import (
    ReadResult,
    ReadStatus,
    read_bool01 as _read_bool01,
    read_int as _read_int,
    read_text as _read_text,
)
```

Do not change any Intel discovery call sites in this commit.

- [ ] **Step 5: Run the temporary test and baseline comparison**

Run:

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py \
  > /tmp/auto-cpufreq-power-context-tests/intel-reader-after.json
diff -u \
  /tmp/auto-cpufreq-power-context-tests/intel-reader-baseline.json \
  /tmp/auto-cpufreq-power-context-tests/intel-reader-after.json
python -m compileall -q auto_cpufreq/modules/sysfs.py auto_cpufreq/modules/intel_power.py
```

Expected: unit test PASS, `diff` has no output, compileall exits 0.

- [ ] **Step 6: Review the diff for behavior neutrality**

Run:

```bash
git diff -- auto_cpufreq/modules/sysfs.py auto_cpufreq/modules/intel_power.py
```

Reject any unrelated Intel topology, CPUFreq, powercap, energy, thermal, formatting, or policy change.

- [ ] **Step 7: Commit**

```bash
git add auto_cpufreq/modules/sysfs.py auto_cpufreq/modules/intel_power.py
git commit -m "refactor: share generic sysfs readers"
```

---

### Task 2: Discover Power-Supply Objects and Aggregate External/Battery State

**Files:**
- Create: `auto_cpufreq/modules/power_context.py`
- Temporary test: `/tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py`

**Interfaces:**
- Produces enums `ExternalPower` and `BatteryFlow`.
- Produces immutable `PowerSupplySnapshot`.
- Produces internal helpers:
  - `_discover_power_supplies(root: Path, ignored_supply_substrings: tuple[str, ...] = ()) -> tuple[ReadStatus, tuple[PowerSupplySnapshot, ...]]`
  - `_system_batteries(supplies: tuple[PowerSupplySnapshot, ...]) -> tuple[PowerSupplySnapshot, ...]`
  - `_aggregate_external_power(supplies: tuple[PowerSupplySnapshot, ...]) -> ExternalPower`
  - `_aggregate_battery_flow(batteries: tuple[PowerSupplySnapshot, ...]) -> BatteryFlow`
- `power_supplies` includes every non-ignored observed object, including `scope=Device` batteries. `_system_batteries` includes only objects whose type is known `Battery` and whose scope is not explicitly `Device`.

- [ ] **Step 1: Write the failing power-supply tests**

Create `/tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from auto_cpufreq.modules.power_context import (
    BatteryFlow,
    ExternalPower,
    _aggregate_battery_flow,
    _aggregate_external_power,
    _discover_power_supplies,
    _system_batteries,
)
from auto_cpufreq.modules.sysfs import ReadStatus


def make_supply(root: Path, name: str, **attrs: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    for key, value in attrs.items():
        (path / key).write_text(f"{value}\n")
    return path


class PowerSupplyContextTests(unittest.TestCase):
    def test_multiple_sources_and_system_batteries(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="0")
            make_supply(root, "USBC", type="USB", online="2", usb_type="PD")
            make_supply(root, "BAT0", type="Battery", scope="System", status="Charging", capacity="80")
            make_supply(root, "BAT1", type="Battery", status="Discharging", capacity="60")
            make_supply(root, "hidpp_battery_0", type="Battery", scope="Device", status="Discharging")

            status, supplies = _discover_power_supplies(root)
            batteries = _system_batteries(supplies)

            self.assertEqual(status, ReadStatus.AVAILABLE)
            self.assertEqual([s.supply_id for s in supplies], ["ADP1", "BAT0", "BAT1", "USBC", "hidpp_battery_0"])
            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.ONLINE)
            self.assertEqual([b.supply_id for b in batteries], ["BAT0", "BAT1"])
            self.assertEqual(_aggregate_battery_flow(batteries), BatteryFlow.MIXED)
            self.assertEqual(next(s for s in supplies if s.supply_id == "USBC").online.value, True)

    def test_offline_plus_unreadable_is_unknown(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="0")
            make_supply(root, "USBC", type="USB", online="1")

            original = Path.read_text
            def selective_read(path, *args, **kwargs):
                if str(path).endswith("USBC/online"):
                    raise PermissionError
                return original(path, *args, **kwargs)

            with patch.object(Path, "read_text", selective_read):
                _, supplies = _discover_power_supplies(root)

            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.UNKNOWN)

    def test_missing_root_and_empty_root_are_distinct(self):
        with TemporaryDirectory() as td:
            parent = Path(td)
            missing_status, missing = _discover_power_supplies(parent / "missing")
            empty = parent / "empty"
            empty.mkdir()
            empty_status, empty_items = _discover_power_supplies(empty)
            self.assertEqual(missing_status, ReadStatus.MISSING)
            self.assertEqual(missing, ())
            self.assertEqual(empty_status, ReadStatus.AVAILABLE)
            self.assertEqual(empty_items, ())

    def test_online_value_outside_0_1_2_is_invalid(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="9")
            _, supplies = _discover_power_supplies(root)
            self.assertEqual(supplies[0].online.status, ReadStatus.INVALID)
            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
```

Expected: FAIL at import because `power_context.py` does not exist.

- [ ] **Step 3: Define the immutable power-supply model**

Start `auto_cpufreq/modules/power_context.py` with:

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus, read_int, read_text


class ExternalPower(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class BatteryFlow(str, Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    NOT_CHARGING = "not_charging"
    FULL = "full"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PowerSupplySnapshot:
    supply_id: str
    sysfs_path: Path
    type: ReadResult[str]
    scope: ReadResult[str]
    online: ReadResult[bool]
    status: ReadResult[str]
    usb_type: ReadResult[str]
    voltage_now: ReadResult[int]
    current_now: ReadResult[int]
    power_now: ReadResult[int]
    input_current_limit: ReadResult[int]
    input_voltage_limit: ReadResult[int]
    input_power_limit: ReadResult[int]
    capacity: ReadResult[int]
    capacity_level: ReadResult[str]
```

Use local helpers, not changes to shared `sysfs.py`, for ABI-specific formats:

```python
def _read_online(path: Path) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "0":
        return ReadResult(ReadStatus.AVAILABLE, False)
    if result.value in ("1", "2"):
        return ReadResult(ReadStatus.AVAILABLE, True)
    return ReadResult(ReadStatus.INVALID)


def _active_enum(result: ReadResult[str]) -> ReadResult[str]:
    if result.status is not ReadStatus.AVAILABLE:
        return result
    value = result.value or ""
    if "[" not in value and "]" not in value:
        return result
    starts = value.count("[")
    ends = value.count("]")
    if starts != 1 or ends != 1:
        return ReadResult(ReadStatus.INVALID)
    start = value.index("[") + 1
    end = value.index("]")
    if end <= start:
        return ReadResult(ReadStatus.INVALID)
    return ReadResult(ReadStatus.AVAILABLE, value[start:end])


def _read_capacity(path: Path) -> ReadResult[int]:
    result = read_int(path)
    if result.status is not ReadStatus.AVAILABLE:
        return result
    if result.value is None or not 0 <= result.value <= 100:
        return ReadResult(ReadStatus.INVALID)
    return result
```

Use `_active_enum(read_text(... / "usb_type"))` because the kernel may expose supported enum values with the current value in brackets.

- [ ] **Step 4: Implement discovery and conservative aggregation**

Implement `_discover_power_supplies` so that it:

```python
try:
    entries = sorted(root.iterdir(), key=lambda path: path.name)
except FileNotFoundError:
    return ReadStatus.MISSING, ()
except OSError:
    return ReadStatus.UNREADABLE, ()
```

For each directory/symlink-to-directory not matching any configured ignore substring, build one `PowerSupplySnapshot`; do not fall back to `/sys` if the injected root is incomplete.

Implement `_aggregate_external_power` with this decision order:

```text
known relevant source online -> ONLINE
else any relevant/possibly-relevant source has unresolved type/scope/online -> UNKNOWN
else one or more known relevant sources and all are offline -> OFFLINE
else -> UNKNOWN
```

A known `Battery` is not an external source. An object explicitly `scope=Device` is not a system external source. An unknown type or unresolved scope can keep an otherwise-offline aggregate at `UNKNOWN`, because it may represent a relevant source.

Implement `_system_batteries` and `_aggregate_battery_flow` so that any unresolved status among system batteries produces `UNKNOWN`; differing known statuses produce `MIXED`; no system battery produces `UNKNOWN`.

- [ ] **Step 5: Run the focused tests**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
python -m compileall -q auto_cpufreq/modules/power_context.py
```

Expected: PASS and compileall 0.

- [ ] **Step 6: Add explicit ignored-supply coverage**

Append:

```python
def test_ignore_substrings_remove_objects_from_discovery(self):
    with TemporaryDirectory() as td:
        root = Path(td)
        make_supply(root, "hidpp_battery_0", type="Battery", scope="Device", status="Discharging")
        make_supply(root, "BAT0", type="Battery", status="Full")
        _, supplies = _discover_power_supplies(root, ("hidpp_battery",))
        self.assertEqual([s.supply_id for s in supplies], ["BAT0"])
```

Run the file again and require PASS.

- [ ] **Step 7: Commit**

```bash
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover system power context"
```

---

### Task 3: Discover USB Type-C Ports and Partner State

**Files:**
- Modify: `auto_cpufreq/modules/power_context.py`
- Temporary test: `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`

**Interfaces:**
- Produces immutable `TypeCPortSnapshot`.
- Produces `_discover_typec_ports(root: Path) -> tuple[ReadStatus, tuple[TypeCPortSnapshot, ...]]`.
- `power_role` stores the current role, extracting the bracketed current enum value when the sysfs attribute presents multiple supported roles.
- `power_operation_mode` preserves the kernel value (`default`, `1.5A`, `3.0A`, `usb_power_delivery`, or a future raw value).
- Partner relationship is local to Type-C only. No power-supply/USB-PD object is linked here.

- [ ] **Step 1: Write the failing Type-C test**

Create `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from auto_cpufreq.modules.power_context import _discover_typec_ports
from auto_cpufreq.modules.sysfs import ReadStatus


class TypeCDiscoveryTests(unittest.TestCase):
    def test_ports_are_numeric_ordered_and_partner_is_local(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for name, role, mode in (
                ("port10", "source [sink]", "usb_power_delivery"),
                ("port2", "[source] sink", "3.0A"),
            ):
                port = root / name
                port.mkdir()
                (port / "power_role").write_text(role + "\n")
                (port / "power_operation_mode").write_text(mode + "\n")

            partner = root / "port10-partner"
            partner.mkdir()
            (partner / "supports_usb_power_delivery").write_text("yes\n")

            status, ports = _discover_typec_ports(root)
            self.assertEqual(status, ReadStatus.AVAILABLE)
            self.assertEqual([p.port_id for p in ports], ["port2", "port10"])
            self.assertEqual(ports[0].power_role.value, "source")
            self.assertEqual(ports[0].partner_present.value, False)
            self.assertEqual(ports[1].power_role.value, "sink")
            self.assertEqual(ports[1].power_operation_mode.value, "usb_power_delivery")
            self.assertEqual(ports[1].partner_present.value, True)
            self.assertEqual(ports[1].partner_usb_pd_capable.value, True)

    def test_invalid_partner_yes_no_is_localized(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            port = root / "port0"
            port.mkdir()
            (port / "power_role").write_text("sink\n")
            (port / "power_operation_mode").write_text("usb_power_delivery\n")
            partner = root / "port0-partner"
            partner.mkdir()
            (partner / "supports_usb_power_delivery").write_text("maybe\n")
            _, ports = _discover_typec_ports(root)
            self.assertEqual(ports[0].partner_usb_pd_capable.status, ReadStatus.INVALID)
            self.assertEqual(ports[0].power_operation_mode.value, "usb_power_delivery")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_typec_context.py
```

Expected: FAIL because `_discover_typec_ports`/`TypeCPortSnapshot` are not defined.

- [ ] **Step 3: Implement Type-C model and parsers**

Add:

```python
@dataclass(frozen=True)
class TypeCPortSnapshot:
    port_id: str
    sysfs_path: Path
    power_role: ReadResult[str]
    power_operation_mode: ReadResult[str]
    partner_present: ReadResult[bool]
    partner_usb_pd_capable: ReadResult[bool]


def _read_yes_no(path: Path) -> ReadResult[bool]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "yes":
        return ReadResult(ReadStatus.AVAILABLE, True)
    if result.value == "no":
        return ReadResult(ReadStatus.AVAILABLE, False)
    return ReadResult(ReadStatus.INVALID)
```

Use a small `_read_presence(path)` helper that returns `AVAILABLE(True/False)` and converts a stat/listing `OSError` into `UNREADABLE` rather than throwing.

- [ ] **Step 4: Implement deterministic port discovery**

Enumerate only names matching `port` plus digits. Sort by numeric suffix. Treat `/sys/class/typec/<port>-partner` as the documented partner object; if a platform presents the partner through the port child path, allow that second documented local representation, but do not search unrelated topology.

Use `_active_enum(read_text(port / "power_role"))` for the current role and `read_text(port / "power_operation_mode")` for the current operation mode. Do not infer partner PD support from operation mode when `supports_usb_power_delivery` is missing.

- [ ] **Step 5: Run Type-C and previous tests**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_typec_context.py
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
python -m compileall -q auto_cpufreq/modules/power_context.py
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover USB Type-C context"
```

---

### Task 4: Discover USB-PD Objects and Compose the Complete PowerContextSnapshot

**Files:**
- Modify: `auto_cpufreq/modules/power_context.py`
- Temporary test: `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py`

**Interfaces:**
- Produces `UsbPdCapabilityKind`, `UsbPdAttributeSnapshot`, `UsbPdCapabilitySnapshot`, `UsbPdSnapshot`, `PowerContextSnapshot`, and `PowerContextDiscovery`.
- Produces `_discover_usb_pd_objects(root: Path) -> tuple[ReadStatus, tuple[UsbPdSnapshot, ...]]`.
- `PowerContextDiscovery(...).discover() -> PowerContextSnapshot` composes exactly one pass over each injected root.

Use these structures:

```python
class UsbPdCapabilityKind(str, Enum):
    FIXED_SUPPLY = "fixed_supply"
    VARIABLE_SUPPLY = "variable_supply"
    BATTERY = "battery"
    PROGRAMMABLE_SUPPLY = "programmable_supply"
    SPR_ADJUSTABLE_VOLTAGE_SUPPLY = "spr_adjustable_voltage_supply"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UsbPdAttributeSnapshot:
    name: str
    value: ReadResult[int | bool]
    unit: str | None = None


@dataclass(frozen=True)
class UsbPdCapabilitySnapshot:
    position: int | None
    kind: UsbPdCapabilityKind
    sysfs_name: str
    attributes: tuple[UsbPdAttributeSnapshot, ...]


@dataclass(frozen=True)
class UsbPdSnapshot:
    pd_id: str
    sysfs_path: Path
    revision: ReadResult[str]
    version: ReadResult[str]
    source_capabilities: tuple[UsbPdCapabilitySnapshot, ...]
    sink_capabilities: tuple[UsbPdCapabilitySnapshot, ...]


@dataclass(frozen=True)
class PowerContextSnapshot:
    power_supply_status: ReadStatus
    typec_status: ReadStatus
    usb_pd_status: ReadStatus
    external_power: ExternalPower
    battery_flow: BatteryFlow
    power_supplies: tuple[PowerSupplySnapshot, ...]
    batteries: tuple[PowerSupplySnapshot, ...]
    typec_ports: tuple[TypeCPortSnapshot, ...]
    usb_pd_objects: tuple[UsbPdSnapshot, ...]
```

- [ ] **Step 1: Write the failing USB-PD tests**

Create `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py` with fixtures covering all current ABI kinds and one unknown kind:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from auto_cpufreq.modules.power_context import (
    PowerContextDiscovery,
    UsbPdCapabilityKind,
    _discover_usb_pd_objects,
)
from auto_cpufreq.modules.sysfs import ReadStatus


def make_cap(root: Path, group: str, name: str, **attrs: str):
    path = root / group / name
    path.mkdir(parents=True, exist_ok=True)
    for key, value in attrs.items():
        (path / key).write_text(value + "\n")


class UsbPdDiscoveryTests(unittest.TestCase):
    def test_capability_types_units_order_and_unknown(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            pd = root / "pd0"
            pd.mkdir()
            (pd / "revision").write_text("3.1\n")
            make_cap(pd, "source-capabilities", "10:future_supply", mystery="9")
            make_cap(pd, "source-capabilities", "2:variable_supply", maximum_voltage="20000mV", minimum_voltage="5000mV", maximum_current="3000mA")
            make_cap(pd, "source-capabilities", "1:fixed_supply", voltage="5000mV", maximum_current="3000mA", dual_role_power="1")
            make_cap(pd, "source-capabilities", "3:battery", maximum_voltage="20000mV", minimum_voltage="5000mV", maximum_power="45000mW")
            make_cap(pd, "source-capabilities", "4:programmable_supply", maximum_voltage="11000mV", minimum_voltage="3300mV", maximum_current="3000mA", pps_power_limited="1")
            make_cap(pd, "source-capabilities", "5:spr_adjustable_voltage_supply", maximum_current_9V_to_15V="3000mA", maximum_current_15V_to_20V="2500mA", peak_current="2")

            status, objects = _discover_usb_pd_objects(root)
            self.assertEqual(status, ReadStatus.AVAILABLE)
            self.assertEqual(len(objects), 1)
            caps = objects[0].source_capabilities
            self.assertEqual([c.position for c in caps], [1, 2, 3, 4, 5, 10])
            self.assertEqual(caps[0].kind, UsbPdCapabilityKind.FIXED_SUPPLY)
            self.assertEqual(caps[-1].kind, UsbPdCapabilityKind.UNKNOWN)
            fixed = {a.name: a for a in caps[0].attributes}
            self.assertEqual(fixed["voltage"].value.value, 5000)
            self.assertEqual(fixed["voltage"].unit, "mV")
            self.assertEqual(fixed["maximum_current"].unit, "mA")
            self.assertEqual(caps[-1].attributes, ())

    def test_bad_suffix_invalidates_only_that_attribute(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            pd = root / "pd0"
            pd.mkdir()
            make_cap(pd, "sink-capabilities", "1:fixed_supply", voltage="5000mV", operational_current="3000watts")
            _, objects = _discover_usb_pd_objects(root)
            fields = {a.name: a for a in objects[0].sink_capabilities[0].attributes}
            self.assertEqual(fields["voltage"].value.status, ReadStatus.AVAILABLE)
            self.assertEqual(fields["operational_current"].value.status, ReadStatus.INVALID)

    def test_complete_discovery_keeps_subsystems_independent(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            ps = base / "power_supply"; ps.mkdir()
            tc = base / "typec"; tc.mkdir()
            pd = base / "usb_power_delivery"; pd.mkdir()
            (ps / "ADP1").mkdir(); (ps / "ADP1" / "type").write_text("Mains\n"); (ps / "ADP1" / "online").write_text("1\n")
            (tc / "port0").mkdir(); (tc / "port0" / "power_role").write_text("sink\n"); (tc / "port0" / "power_operation_mode").write_text("usb_power_delivery\n")
            (pd / "pd7").mkdir(); (pd / "pd7" / "revision").write_text("3.1\n")

            snapshot = PowerContextDiscovery(ps, tc, pd).discover()
            self.assertEqual([s.supply_id for s in snapshot.power_supplies], ["ADP1"])
            self.assertEqual([p.port_id for p in snapshot.typec_ports], ["port0"])
            self.assertEqual([p.pd_id for p in snapshot.usb_pd_objects], ["pd7"])
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py
```

Expected: FAIL because USB-PD structures/discovery are not implemented.

- [ ] **Step 3: Implement suffix-aware PDO scalar parsing**

Add local helpers:

```python
def _read_suffixed_int(path: Path, suffix: str) -> ReadResult[int]:
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    value = result.value or ""
    if not value.endswith(suffix):
        return ReadResult(ReadStatus.INVALID)
    number = value[:-len(suffix)]
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(number))
    except ValueError:
        return ReadResult(ReadStatus.INVALID)
```

PDO units are retained as documented (`mV`, `mA`, `mW`) instead of being silently converted into power-supply-class micro-units. The two subsystems have different ABIs; keeping their native units prevents accidental semantic merging.

- [ ] **Step 4: Implement explicit schema-driven capability parsing**

Use an explicit mapping, not directory dumping. The parser may construct `UsbPdAttributeSnapshot` entries from these supported fields:

```text
fixed source:
  position 1 booleans: dual_role_power, usb_suspend_supported,
    unconstrained_power, usb_communication_capable, dual_role_data,
    unchunked_extended_messages_supported
  all positions: peak_current (integer enum), voltage (mV), maximum_current (mA)

fixed sink:
  position 1 booleans: dual_role_power, higher_capability,
    unconstrained_power, usb_communication_capable, dual_role_data,
    unchunked_extended_messages_supported
  all positions: fast_role_swap_current (integer enum), voltage (mV), operational_current (mA)

variable source:
  maximum_voltage (mV), minimum_voltage (mV), maximum_current (mA)
variable sink:
  maximum_voltage (mV), minimum_voltage (mV), operational_current (mA)

battery source:
  maximum_voltage (mV), minimum_voltage (mV), maximum_power (mW)
battery sink:
  maximum_voltage (mV), minimum_voltage (mV), operational_power (mW)

programmable/PPS source:
  maximum_voltage (mV), minimum_voltage (mV), maximum_current (mA), pps_power_limited (0/1)
programmable/PPS sink:
  maximum_voltage (mV), minimum_voltage (mV), maximum_current (mA)

SPR adjustable source:
  maximum_current_9V_to_15V (mA), maximum_current_15V_to_20V (mA), peak_current (integer enum)
SPR adjustable sink:
  maximum_current_9V_to_15V (mA), maximum_current_15V_to_20V (mA)
```

Unknown kinds keep `position`, `sysfs_name`, `kind=UNKNOWN`, and `attributes=()` only.

- [ ] **Step 5: Implement deterministic USB-PD discovery and top-level composition**

Parse capability directory names with an anchored pattern equivalent to:

```python
r"^(?P<position>[0-9]+):(?P<kind>.+)$"
```

Sort valid numeric positions numerically and malformed names deterministically after valid positions. Enumerate PD objects lexically. Read `revision` and optional `version` independently.

Implement:

```python
class PowerContextDiscovery:
    def __init__(
        self,
        power_supply_root: Path = Path("/sys/class/power_supply"),
        typec_root: Path = Path("/sys/class/typec"),
        usb_pd_root: Path = Path("/sys/class/usb_power_delivery"),
        ignored_supply_substrings: tuple[str, ...] = (),
    ) -> None:
        ...

    def discover(self) -> PowerContextSnapshot:
        power_status, supplies = _discover_power_supplies(...)
        typec_status, ports = _discover_typec_ports(...)
        usb_pd_status, pd_objects = _discover_usb_pd_objects(...)
        batteries = _system_batteries(supplies)
        return PowerContextSnapshot(
            power_supply_status=power_status,
            typec_status=typec_status,
            usb_pd_status=usb_pd_status,
            external_power=_aggregate_external_power(supplies),
            battery_flow=_aggregate_battery_flow(batteries),
            power_supplies=supplies,
            batteries=batteries,
            typec_ports=ports,
            usb_pd_objects=pd_objects,
        )
```

No cross-subsystem matching step exists.

- [ ] **Step 6: Run the complete discovery tests**

```bash
for test in \
  /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py \
  /tmp/auto-cpufreq-power-context-tests/test_typec_context.py \
  /tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py; do
  PYTHONPATH="$PWD" python "$test" || exit 1
done
python -m compileall -q auto_cpufreq/modules/power_context.py
```

Expected: all PASS.

- [ ] **Step 7: Add hotplug/root-isolation coverage**

Extend the USB-PD/Type-C temporary tests so one enumerated object disappears before a field read and verify discovery returns a partial snapshot rather than raising. Also create an injected root with no real sysfs children and assert no object from `/sys/class/...` appears. Use `unittest.mock.patch` for the race; do not mutate the real host sysfs.

- [ ] **Step 8: Commit**

```bash
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover USB Power Delivery capabilities"
```

---

### Task 5: Integrate PowerContext Into Debug and Stats Reporting Only

**Files:**
- Modify: `auto_cpufreq/modules/system_info.py` (`SystemReport`, `generate_system_report`, reporting helpers/formatter)
- Modify: `auto_cpufreq/modules/system_monitor.py` (`_collect_report`, STATS formatting/imports)
- Modify: `auto_cpufreq/bin/auto_cpufreq.py` (`elif debug:` report flags only)
- Temporary test: `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`

**Interfaces:**
- `SystemReport.power_context: PowerContextSnapshot | None = None`.
- `SystemInfo.generate_system_report(..., include_power_context: bool = False) -> SystemReport`.
- `format_power_context_debug(snapshot: PowerContextSnapshot) -> list[str]`.
- `format_power_context_summary(snapshot: PowerContextSnapshot) -> list[str]`.
- A small integration helper resolves normal `/sys` versus Snap hostfs and constructs `PowerContextDiscovery`; `power_context.py` itself never imports Snap globals.

- [ ] **Step 1: Write failing formatter tests**

Create `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`:

```python
import unittest
from pathlib import Path

from auto_cpufreq.modules.power_context import (
    BatteryFlow,
    ExternalPower,
    PowerContextSnapshot,
    PowerSupplySnapshot,
)
from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus
from auto_cpufreq.modules.system_info import (
    format_power_context_debug,
    format_power_context_summary,
)

A = ReadStatus.AVAILABLE
M = ReadStatus.MISSING


class PowerContextReportingTests(unittest.TestCase):
    def snapshot(self):
        supply = PowerSupplySnapshot(
            supply_id="USBC",
            sysfs_path=Path("/synthetic/power_supply/USBC"),
            type=ReadResult(A, "USB"),
            scope=ReadResult(M),
            online=ReadResult(A, True),
            status=ReadResult(M),
            usb_type=ReadResult(A, "PD"),
            voltage_now=ReadResult(A, 20_000_000),
            current_now=ReadResult(A, 2_000_000),
            power_now=ReadResult(M),
            input_current_limit=ReadResult(A, 3_250_000),
            input_voltage_limit=ReadResult(M),
            input_power_limit=ReadResult(A, 65_000_000),
            capacity=ReadResult(M),
            capacity_level=ReadResult(M),
        )
        return PowerContextSnapshot(
            power_supply_status=A,
            typec_status=ReadStatus.MISSING,
            usb_pd_status=ReadStatus.MISSING,
            external_power=ExternalPower.ONLINE,
            battery_flow=BatteryFlow.UNKNOWN,
            power_supplies=(supply,),
            batteries=(),
            typec_ports=(),
            usb_pd_objects=(),
        )

    def test_summary_is_compact_and_does_not_invent_wattage(self):
        text = "\n".join(format_power_context_summary(self.snapshot()))
        self.assertIn("External power: Online", text)
        self.assertIn("Battery flow: Unknown", text)
        self.assertIn("USB-PD: Missing", text)
        self.assertNotIn("charger", text.lower())
        self.assertNotIn("40.0 W", text)

    def test_debug_preserves_missing_and_formats_exposed_limits(self):
        text = "\n".join(format_power_context_debug(self.snapshot()))
        self.assertIn("Power Supply subsystem: Available", text)
        self.assertIn("USBC", text)
        self.assertIn("Input power limit: 65.000 W", text)
        self.assertIn("Power now: Unavailable", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py
```

Expected: FAIL because reporting functions do not exist.

- [ ] **Step 3: Add root resolution and one-snapshot report collection**

In `system_info.py`, import `PowerContextDiscovery`, `PowerContextSnapshot`, and shared `ReadStatus` as needed.

Add an integration-only constructor helper equivalent to:

```python
def power_context_discovery() -> PowerContextDiscovery:
    sys_root = Path(SNAP_HOST_ROOT) / "sys" if IS_INSTALLED_WITH_SNAP else Path("/sys")
    return PowerContextDiscovery(
        power_supply_root=sys_root / "class/power_supply",
        typec_root=sys_root / "class/typec",
        usb_pd_root=sys_root / "class/usb_power_delivery",
        ignored_supply_substrings=tuple(get_power_supply_ignore_list()),
    )
```

Add to `SystemReport`:

```python
power_context: PowerContextSnapshot | None = None
```

Extend the report signature without changing defaults:

```python
def generate_system_report(
    self,
    include_intel_power: bool = False,
    sample_intel_energy: bool = False,
    include_power_context: bool = False,
) -> SystemReport:
```

Call `power_context_discovery().discover()` exactly once only when `include_power_context` is true, and store that result on the report.

- [ ] **Step 4: Implement detailed and compact formatters**

`format_power_context_debug()` must display:

```text
External power
Battery flow
Power Supply subsystem status + every observed supply
Type-C subsystem status + every port
USB Power Delivery subsystem status + every PD object/PDO
```

Use these user-facing read-state mappings consistently:

```text
MISSING    -> Unavailable
UNREADABLE -> Could not be read
INVALID    -> Invalid
AVAILABLE  -> formatted value
```

Power-supply-class values are stored in micro-units; format `voltage_now`/`input_voltage_limit` as V, current as A, power as W. USB-PD PDO attributes are stored in documented mV/mA/mW; format them using the unit recorded on `UsbPdAttributeSnapshot`.

`format_power_context_summary()` must stay compact and factual, for example:

```text
External power: Online
Battery flow: Charging
Type-C ports: 1
USB-PD: Available (1 object)
```

For heterogeneous ports, report counts/status only; never select one as active.

- [ ] **Step 5: Wire only debug and stats collection**

In `auto_cpufreq/bin/auto_cpufreq.py`, change only the existing `--debug` report construction:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=False,
    include_power_context=True,
)
```

Do not alter daemon, monitor, or live code paths.

In `system_monitor.py`, STATS only becomes:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=True,
    include_power_context=True,
)
```

MONITOR/LIVE continue calling `generate_system_report()` with defaults.

Render `format_power_context_summary(report.power_context)` only when `self.type == ViewType.STATS` and `report.power_context is not None`.

- [ ] **Step 6: Run focused reporting and discovery tests**

```bash
for test in /tmp/auto-cpufreq-power-context-tests/test_*.py; do
  PYTHONPATH="$PWD" python "$test" || exit 1
done
python -m compileall -q auto_cpufreq
```

Expected: all temporary tests PASS and compileall 0.

- [ ] **Step 7: Audit control-path isolation before committing**

Run:

```bash
git diff --name-only HEAD~4..HEAD 2>/dev/null || true
git diff -- auto_cpufreq/modules/policy.py \
  auto_cpufreq/modules/daemon_scheduler.py \
  auto_cpufreq/modules/intel_rapl.py \
  auto_cpufreq/power_helper.py
```

Expected for the explicit control-path diff: no output.

Also inspect:

```bash
git diff -- auto_cpufreq/bin/auto_cpufreq.py auto_cpufreq/modules/system_monitor.py
```

Reject any change outside debug/STATS reporting flags and rendering.

- [ ] **Step 8: Commit**

```bash
git add \
  auto_cpufreq/modules/system_info.py \
  auto_cpufreq/modules/system_monitor.py \
  auto_cpufreq/bin/auto_cpufreq.py
git commit -m "feat: report power context diagnostics"
```

---

### Task 6: Final Synthetic, CI, and Read-Only Hardware Verification

**Files:**
- Production code from Tasks 1-5 only
- Optional create after evidence exists: `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md`
- Temporary tests remain under `/tmp/auto-cpufreq-power-context-tests/`

**Interfaces:**
- Consumes final `PowerContextDiscovery`, `PowerContextSnapshot`, debug formatter, and stats summary.
- Produces verification evidence only; no policy feature is added in this task.

- [ ] **Step 1: Expand the temporary synthetic suite to the complete spec matrix**

Ensure explicit cases exist for all of these before declaring completion:

```text
power_supply:
- normal discovery
- multiple external sources
- multiple system batteries
- scope=Device excluded from battery aggregation
- online + offline => ONLINE
- offline + unreadable => UNKNOWN
- divergent battery states => MIXED
- missing / unreadable / malformed attributes
- online 0 / 1 / 2 and invalid online value
- usb_type active-value extraction
- deterministic ordering
- ignored supply substring

Type-C:
- missing root
- readable empty root
- port without partner
- port with partner
- bracketed power_role current value
- usb_power_delivery operation mode
- supports_usb_power_delivery yes / no / invalid
- deterministic numeric port ordering

USB-PD:
- missing root
- readable empty root
- fixed source and sink PDOs
- variable source and sink PDOs
- battery source and sink PDOs
- programmable/PPS PDOs
- SPR adjustable PDOs
- unknown future PDO
- malformed PDO name deterministic fallback
- missing / unreadable / malformed PDO attribute
- mV / mA / mW suffix validation

robustness:
- object disappears between enumeration and attribute read
- injected roots never fall back to host /sys
- one broken object does not crash unrelated discovery
- no cross-subsystem matching structure exists
```

Run all temporary tests:

```bash
for test in /tmp/auto-cpufreq-power-context-tests/test_*.py; do
  echo "==> $test"
  PYTHONPATH="$PWD" python "$test" || exit 1
done
```

Expected: every script PASS.

- [ ] **Step 2: Run local static/build sanity checks**

```bash
python -m compileall -q auto_cpufreq
python - <<'PY'
from auto_cpufreq.modules.power_context import PowerContextDiscovery
snapshot = PowerContextDiscovery().discover()
print(snapshot.power_supply_status.value)
print(snapshot.typec_status.value)
print(snapshot.usb_pd_status.value)
PY
```

Expected: compileall 0; discovery returns statuses without exception and performs no writes.

- [ ] **Step 3: Verify final production diff and commit topology**

```bash
git log --oneline --decorate --reverse usb-c-power-context-design..HEAD
git diff --stat usb-c-power-context-design..HEAD
git diff --name-only usb-c-power-context-design..HEAD
```

Expected production commit sequence is narrowly equivalent to:

```text
refactor: share generic sysfs readers
feat: discover system power context
feat: discover USB Type-C context
feat: discover USB Power Delivery capabilities
feat: report power context diagnostics
```

Expected production files are limited to the six files listed in File Structure. Any change to policy, scheduler, RAPL, service files, config examples, installer, or power helpers is a blocker requiring review.

- [ ] **Step 4: Push the implementation branch and require existing CI to pass**

Push `usb-c-power-context`; do not open an upstream PR.

Require both existing workflows:

```text
Linux Build
Nix Flake
```

If either fails, inspect the failing job/log and fix only the demonstrated defect before continuing.

- [ ] **Step 5: Perform read-only hardware preflight on the Samsung**

Before running new reporting, capture raw topology without writing:

```bash
printf '%s\n' '=== power_supply ==='
find /sys/class/power_supply -maxdepth 2 -type f -readable -print 2>/dev/null | sort
printf '%s\n' '=== typec ==='
find /sys/class/typec -maxdepth 3 -print 2>/dev/null | sort
printf '%s\n' '=== usb_power_delivery ==='
find /sys/class/usb_power_delivery -maxdepth 4 -print 2>/dev/null | sort
```

A missing Type-C or USB-PD root is a valid result.

Capture control-state baselines before exercising the new reporting:

```bash
cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || true
cat /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 2>/dev/null || true
for p in /sys/devices/system/cpu/cpufreq/policy*; do
  printf '%s ' "$p"
  cat "$p/scaling_governor" "$p/energy_performance_preference" "$p/scaling_min_freq" "$p/scaling_max_freq" 2>/dev/null | tr '\n' ' '
  echo
done
find /sys/class/powercap -name 'constraint_*_power_limit_uw' -readable -print -exec cat {} \; 2>/dev/null | sort
```

Store the output in `/tmp/power-context-before.txt` for later comparison.

- [ ] **Step 6: Compare raw sysfs to the new snapshot with charger connected**

Run the discovery directly from the feature checkout first:

```bash
PYTHONPATH="$PWD" python - <<'PY'
from auto_cpufreq.modules.power_context import PowerContextDiscovery
from pprint import pprint
pprint(PowerContextDiscovery().discover())
PY
```

Then run the feature's `--debug` through the normal installed/development entry point used for prior hardware gates. Confirm:

```text
raw power_supply values <-> snapshot values <-> debug values
Type-C values <-> snapshot/debug when present
USB-PD PDO values <-> snapshot/debug when present
```

No advertised PDO may be described as current negotiated wattage.

- [ ] **Step 7: Repeat observation with external power disconnected**

With the charger disconnected, repeat the raw read and discovery. Expected factual behavior on this laptop is determined by actual sysfs, not by assumptions. At minimum verify that if all relevant external supplies explicitly become offline, `external_power` becomes `OFFLINE`; if the kernel leaves material state unresolved, the snapshot must say `UNKNOWN` rather than fabricate a value.

- [ ] **Step 8: Verify no new hardware state changed**

Repeat the same governor/EPP/turbo/Dynamic Boost/frequency/RAPL snapshots from Step 5 and diff against `/tmp/power-context-before.txt`, accounting only for the deliberate physical charger disconnect/reconnect state itself.

Also verify no new state file was created for PowerContext and no Type-C/USB-PD writable attribute was touched by code inspection (`git grep` should show only read operations in `power_context.py`).

- [ ] **Step 9: Record validation evidence after it exists**

Only after synthetic tests, CI, and hardware checks pass, create `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md` recording:

```text
- exact feature commit SHA
- Linux Build run/result
- Nix Flake run/result
- Samsung kernel version
- observed power_supply objects
- observed Type-C ports/partner data
- whether /sys/class/usb_power_delivery exists
- connected/disconnected aggregate results
- raw-vs-snapshot-vs-debug comparison
- no-write/control-state comparison
- any platform ABI that was absent and therefore covered only synthetically
```

Commit validation documentation separately so the validated production-code HEAD remains easy to identify.

- [ ] **Step 10: Final completion gate**

Do not claim completion until fresh evidence proves all 14 spec completion criteria, especially:

```text
partial data degrades locally
multiple supplies/batteries are preserved
MIXED works for batteries
current state and advertised capabilities remain separate
no heuristic cross-subsystem correlation exists
debug is detailed; stats is compact
Intel reader refactor is behavior-neutral
monitor/live/daemon/policy remain unchanged
no new hardware write occurs
real hardware agrees with raw sysfs where the ABI is exposed
```
