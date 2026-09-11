# USB-C and USB-PD Power Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only, factual `PowerContextSnapshot` for Linux `power_supply`, USB Type-C, and USB-PD data, expose it in `--debug` and `--stats`, and leave all daemon/policy/hardware-write behavior unchanged.

**Architecture:** Extract the existing generic sysfs read-result primitives into `auto_cpufreq/modules/sysfs.py`, then add a focused `power_context.py` discovery module with injectable roots. `SystemInfo` collects one optional snapshot per report; `--debug` renders details and STATS renders only a compact summary. There is no path from `PowerContextSnapshot` to policy, daemon scheduling, RAPL, EPP, governor, turbo, Dynamic Boost, platform profile, charging control, or Type-C/PD writes.

**Tech Stack:** Python as declared by the repository metadata (`>=3.9,<4.0`), standard library only for the new discovery code (`dataclasses`, `enum`, `pathlib`, `re`, `typing`, `unittest`, `unittest.mock`, `tempfile`), existing `urwid` UI, Linux sysfs ABIs. New code in `power_context.py` uses `Optional`/`Union` rather than adding new Python-3.10-only union syntax.

**Spec:** `docs/superpowers/specs/2026-09-11-usb-c-power-context-design.md`

## Global Constraints

- Implementation branch: `usb-c-power-context`, created from the approved `usb-c-power-context-design` tip. Do not rebase this work directly onto upstream `master`.
- The design branch itself is based on `modern-intel-rapl-envelopes` at `d47b783d91ab616a863027069e1511e5d557bf22`.
- First delivery is strictly read-only and observational.
- `PowerSource.CHARGER/BATTERY`, `charging()`, `SystemInfo.external_power_state()`, Modern Intel policy, Legacy policy, daemon triggers, watchdog behavior, RAPL ownership/failsafe, and platform-profile control remain unchanged.
- No new dependency and no permanent upstream test framework are introduced.
- TDD fixtures live only under `/tmp/auto-cpufreq-power-context-tests/` and are never committed to the production branch.
- No charger sufficiency classification, active-charger election, negotiated-wattage inference, or cross-subsystem identity inference.
- Do not calculate missing `power_now` from voltage/current.
- Keep `power_supply`, Type-C, and USB-PD objects independent; only the documented Type-C port→partner relationship is used.
- `external_power`: `ONLINE | OFFLINE | UNKNOWN`; no `MIXED` state.
- `battery_flow`: `CHARGING | DISCHARGING | NOT_CHARGING | FULL | MIXED | UNKNOWN`.
- A battery explicitly `scope=Device` stays observable in `power_supplies` but is excluded from system `batteries` and aggregate flow. Missing `scope` does not by itself exclude an otherwise known system battery/source.
- Root missing → `MISSING`; root enumeration failure → `UNREADABLE`; readable empty root → `AVAILABLE` plus an empty tuple.
- Attribute errors are localized: missing → `MISSING`, I/O/permission error → `UNREADABLE`, malformed content → `INVALID`.
- Injected roots never fall back to real `/sys`.
- One report gets one discovery pass; there is no long-lived PowerContext cache in v1.
- `--debug`: detailed PowerContext. `--stats`: compact PowerContext. `--monitor`/`--live`: unchanged.
- `power_supply/online`: `0` means offline; `1` and `2` mean online; other values are invalid.
- `power_supply` numeric units remain kernel ABI micro-units (`µV`, `µA`, `µW`) internally.
- Type-C `power_role` may expose supported values with the active value in brackets; `power_operation_mode` remains the raw documented current mode.
- Type-C partner PD support is read only from `<port>-partner/supports_usb_power_delivery` (`yes`/`no`); it is never inferred from operation mode.
- USB-PD capability groups are `source-capabilities` and `sink-capabilities`.
- USB-PD PDO scalar values are textual `mV`, `mA`, or `mW` according to the kernel ABI and are parsed with exact suffix validation.
- Supported PDO kinds: `fixed_supply`, `variable_supply`, `battery`, `programmable_supply`, `spr_adjustable_voltage_supply`; unknown kinds are retained as identity only.
- Existing `Linux Build` and `Nix Flake` workflows must remain green.

## File Structure

Production files:

- Create `auto_cpufreq/modules/sysfs.py` — shared `ReadStatus`, `ReadResult`, `read_text`, `read_int`, `read_bool01`.
- Modify `auto_cpufreq/modules/intel_power.py` — import shared primitives while preserving current Intel behavior and existing public imports.
- Create `auto_cpufreq/modules/power_context.py` — all new snapshots, parsers, discovery, aggregation, ordering, and root-status logic.
- Modify `auto_cpufreq/modules/system_info.py` — optional report collection, Snap hostfs root resolution, detailed/summary formatters.
- Modify `auto_cpufreq/modules/system_monitor.py` — STATS-only collection/rendering.
- Modify `auto_cpufreq/bin/auto_cpufreq.py` — `--debug` requests PowerContext.

Temporary tests, never committed:

- `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`
- `/tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py`
- `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`

Optional validation evidence after all gates pass:

- `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md`

---

### Task 1: Extract Shared Sysfs Readers Without Intel Behavior Change

**Files:**
- Create: `auto_cpufreq/modules/sysfs.py`
- Modify: `auto_cpufreq/modules/intel_power.py` (top-level read types/helpers)
- Test: `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`

**Interfaces:**
- Produces `ReadStatus`, `ReadResult[T]`, `read_text(Path)`, `read_int(Path)`, `read_bool01(Path, invert=False)`.
- `intel_power.py` continues exposing `ReadStatus` and `ReadResult` by importing them at module scope.
- Intel-only CPU-list/list parsing, numeric sorting, powercap logic, energy sampling, thermal telemetry, and discovery remain in `intel_power.py`.

- [ ] **Step 1: Capture the current reader behavior before editing**

Create `/tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py`:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from auto_cpufreq.modules import intel_power

with TemporaryDirectory() as td:
    root = Path(td)
    (root / "text").write_text("value\n")
    (root / "integer").write_text("42\n")
    (root / "invalid").write_text("bad\n")
    (root / "true").write_text("1\n")
    (root / "false").write_text("0\n")
    results = {
        "text": (intel_power._read_text(root / "text").status.value, intel_power._read_text(root / "text").value),
        "integer": (intel_power._read_int(root / "integer").status.value, intel_power._read_int(root / "integer").value),
        "invalid": (intel_power._read_int(root / "invalid").status.value, intel_power._read_int(root / "invalid").value),
        "missing": (intel_power._read_text(root / "missing").status.value, intel_power._read_text(root / "missing").value),
        "true": (intel_power._read_bool01(root / "true").status.value, intel_power._read_bool01(root / "true").value),
        "false": (intel_power._read_bool01(root / "false").status.value, intel_power._read_bool01(root / "false").value),
        "invert": (intel_power._read_bool01(root / "true", invert=True).status.value, intel_power._read_bool01(root / "true", invert=True).value),
    }
    print(json.dumps(results, sort_keys=True))
```

Run:

```bash
mkdir -p /tmp/auto-cpufreq-power-context-tests
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py > /tmp/auto-cpufreq-power-context-tests/intel-reader-before.json
```

Expected: command exits 0 and records the current `available`/`missing`/`invalid` behavior.

- [ ] **Step 2: Write the failing shared-reader test**

Create `/tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus, read_bool01, read_int, read_text


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
        with patch.object(Path, "read_text", side_effect=PermissionError):
            self.assertEqual(read_text(Path("/synthetic/unreadable")).status, ReadStatus.UNREADABLE)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run it and confirm RED**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py
```

Expected: import failure for `auto_cpufreq.modules.sysfs`.

- [ ] **Step 4: Add the minimal shared module and preserve Intel call sites**

Create `auto_cpufreq/modules/sysfs.py`:

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

In `intel_power.py`, delete the duplicated shared definitions and import aliases:

```python
from auto_cpufreq.modules.sysfs import (
    ReadResult,
    ReadStatus,
    read_bool01 as _read_bool01,
    read_int as _read_int,
    read_text as _read_text,
)
```

Do not alter Intel discovery call sites.

- [ ] **Step 5: Run GREEN and compare the pre/post baseline**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_sysfs_refactor.py
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/capture_intel_reader_baseline.py > /tmp/auto-cpufreq-power-context-tests/intel-reader-after.json
diff -u /tmp/auto-cpufreq-power-context-tests/intel-reader-before.json /tmp/auto-cpufreq-power-context-tests/intel-reader-after.json
python -m compileall -q auto_cpufreq/modules/sysfs.py auto_cpufreq/modules/intel_power.py
```

Expected: test PASS, `diff` has no output, compileall exits 0.

- [ ] **Step 6: Commit the behavior-neutral refactor**

```bash
git add auto_cpufreq/modules/sysfs.py auto_cpufreq/modules/intel_power.py
git commit -m "refactor: share generic sysfs readers"
```

---

### Task 2: Discover `power_supply` Objects and Aggregate Factual State

**Files:**
- Create: `auto_cpufreq/modules/power_context.py`
- Test: `/tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py`

**Interfaces:**
- Produces `ExternalPower`, `BatteryFlow`, immutable `PowerSupplySnapshot`.
- Produces `_discover_power_supplies(root, ignored_supply_substrings=())`, `_system_batteries`, `_aggregate_external_power`, `_aggregate_battery_flow`.
- A known non-battery supply is system-relevant unless `scope` is explicitly `Device`; missing `scope` alone does not make it ambiguous.
- A known battery participates in system battery flow unless `scope` is explicitly `Device`.

- [ ] **Step 1: Write failing power-supply tests**

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


def make_supply(root, name, **attrs):
    path = root / name
    path.mkdir(parents=True)
    for key, value in attrs.items():
        (path / key).write_text(str(value) + "\n")


class PowerSupplyTests(unittest.TestCase):
    def test_multiple_sources_batteries_and_online_two(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="0")
            make_supply(root, "USBC", type="USB", online="2", usb_type="SDP DCP [PD]")
            make_supply(root, "BAT0", type="Battery", scope="System", status="Charging")
            make_supply(root, "BAT1", type="Battery", status="Discharging")
            make_supply(root, "MOUSE", type="Battery", scope="Device", status="Discharging")
            status, supplies = _discover_power_supplies(root)
            batteries = _system_batteries(supplies)
            self.assertEqual(status, ReadStatus.AVAILABLE)
            self.assertEqual([s.supply_id for s in supplies], ["ADP1", "BAT0", "BAT1", "MOUSE", "USBC"])
            self.assertEqual(next(s for s in supplies if s.supply_id == "USBC").online.value, True)
            self.assertEqual(next(s for s in supplies if s.supply_id == "USBC").usb_type.value, "PD")
            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.ONLINE)
            self.assertEqual([b.supply_id for b in batteries], ["BAT0", "BAT1"])
            self.assertEqual(_aggregate_battery_flow(batteries), BatteryFlow.MIXED)

    def test_known_offline_source_with_missing_scope_is_offline(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="0")
            _, supplies = _discover_power_supplies(root)
            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.OFFLINE)

    def test_offline_plus_unreadable_online_is_unknown(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            make_supply(root, "ADP1", type="Mains", online="0")
            make_supply(root, "USBC", type="USB", online="1")
            original = Path.read_text
            def selective(path, *args, **kwargs):
                if str(path).endswith("USBC/online"):
                    raise PermissionError
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", selective):
                _, supplies = _discover_power_supplies(root)
            self.assertEqual(_aggregate_external_power(supplies), ExternalPower.UNKNOWN)

    def test_missing_empty_invalid_online_capacity_and_ignore(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            missing_status, missing = _discover_power_supplies(base / "missing")
            empty = base / "empty"; empty.mkdir()
            empty_status, empty_items = _discover_power_supplies(empty)
            populated = base / "populated"; populated.mkdir()
            make_supply(populated, "ADP1", type="Mains", online="9")
            make_supply(populated, "BAT0", type="Battery", status="Full", capacity="101")
            make_supply(populated, "hidpp_battery_0", type="Battery", scope="Device", status="Full")
            _, supplies = _discover_power_supplies(populated, ("hidpp_battery",))
            self.assertEqual(missing_status, ReadStatus.MISSING)
            self.assertEqual(missing, ())
            self.assertEqual(empty_status, ReadStatus.AVAILABLE)
            self.assertEqual(empty_items, ())
            self.assertEqual([s.supply_id for s in supplies], ["ADP1", "BAT0"])
            self.assertEqual(supplies[0].online.status, ReadStatus.INVALID)
            self.assertEqual(supplies[1].capacity.status, ReadStatus.INVALID)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
```

Expected: import failure for `power_context` symbols.

- [ ] **Step 3: Add the immutable model and ABI-specific scalar helpers**

Create `auto_cpufreq/modules/power_context.py` beginning with:

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Union

from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus, read_bool01, read_int, read_text


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


def _read_online(path):
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "0":
        return ReadResult(ReadStatus.AVAILABLE, False)
    if result.value in ("1", "2"):
        return ReadResult(ReadStatus.AVAILABLE, True)
    return ReadResult(ReadStatus.INVALID)


def _active_enum(result):
    if result.status is not ReadStatus.AVAILABLE:
        return result
    value = result.value or ""
    if "[" not in value and "]" not in value:
        return result
    if value.count("[") != 1 or value.count("]") != 1:
        return ReadResult(ReadStatus.INVALID)
    start = value.index("[") + 1
    end = value.index("]")
    if end <= start:
        return ReadResult(ReadStatus.INVALID)
    return ReadResult(ReadStatus.AVAILABLE, value[start:end])


def _read_capacity(path):
    result = read_int(path)
    if result.status is not ReadStatus.AVAILABLE:
        return result
    if result.value is None or result.value < 0 or result.value > 100:
        return ReadResult(ReadStatus.INVALID)
    return result
```

- [ ] **Step 4: Implement deterministic discovery and aggregation**

Use exactly these rules in `_discover_power_supplies`:

```python
def _discover_power_supplies(root, ignored_supply_substrings=()):
    root = Path(root)
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except FileNotFoundError:
        return ReadStatus.MISSING, ()
    except OSError:
        return ReadStatus.UNREADABLE, ()

    snapshots = []
    for path in entries:
        if any(token in path.name for token in ignored_supply_substrings):
            continue
        snapshots.append(PowerSupplySnapshot(
            supply_id=path.name,
            sysfs_path=path,
            type=read_text(path / "type"),
            scope=read_text(path / "scope"),
            online=_read_online(path / "online"),
            status=read_text(path / "status"),
            usb_type=_active_enum(read_text(path / "usb_type")),
            voltage_now=read_int(path / "voltage_now"),
            current_now=read_int(path / "current_now"),
            power_now=read_int(path / "power_now"),
            input_current_limit=read_int(path / "input_current_limit"),
            input_voltage_limit=read_int(path / "input_voltage_limit"),
            input_power_limit=read_int(path / "input_power_limit"),
            capacity=_read_capacity(path / "capacity"),
            capacity_level=read_text(path / "capacity_level"),
        ))
    return ReadStatus.AVAILABLE, tuple(snapshots)
```

Implement `_system_batteries` as: include only `type=Battery` with type `AVAILABLE`, excluding only `scope=Device` when scope is `AVAILABLE`. Unknown type is not guessed to be a battery.

Implement `_aggregate_external_power` as:

```text
1. Ignore known Battery objects and objects explicitly scope=Device.
2. For known non-Battery source objects:
   - any online=True => ONLINE immediately;
   - missing/unreadable/invalid online => unresolved;
   - online=False => known offline.
3. Any object with unresolved type that is not explicitly scope=Device is potentially a source and therefore unresolved.
4. If no online source exists and any relevant/potential source is unresolved => UNKNOWN.
5. Else if at least one known relevant source exists and all are offline => OFFLINE.
6. Else => UNKNOWN.
```

Implement `_aggregate_battery_flow` by mapping exact case-insensitive statuses `Charging`, `Discharging`, `Not charging`, `Full`; any unknown/missing/unreadable/invalid status among system batteries → `UNKNOWN`; all same → that state; differing known states → `MIXED`; no system battery → `UNKNOWN`.

- [ ] **Step 5: Run GREEN and commit**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
python -m compileall -q auto_cpufreq/modules/power_context.py
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover system power context"
```

Expected: tests PASS, compileall 0, one focused commit.

---

### Task 3: Discover USB Type-C Port and Partner State

**Files:**
- Modify: `auto_cpufreq/modules/power_context.py`
- Test: `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`

**Interfaces:**
- Produces immutable `TypeCPortSnapshot` and `_discover_typec_ports(root)`.
- Only names matching `port[0-9]+` are ports; `portN-partner` is the documented partner object.
- Port order is numeric by suffix.

- [ ] **Step 1: Write failing Type-C tests**

Create `/tmp/auto-cpufreq-power-context-tests/test_typec_context.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from auto_cpufreq.modules.power_context import _discover_typec_ports
from auto_cpufreq.modules.sysfs import ReadStatus


class TypeCTests(unittest.TestCase):
    def test_numeric_order_roles_partner_and_pd_flag(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for name, role, mode in (
                ("port10", "source [sink]", "usb_power_delivery"),
                ("port2", "[source] sink", "3.0A"),
            ):
                port = root / name; port.mkdir()
                (port / "power_role").write_text(role + "\n")
                (port / "power_operation_mode").write_text(mode + "\n")
            partner = root / "port10-partner"; partner.mkdir()
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

    def test_invalid_partner_flag_is_local(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            port = root / "port0"; port.mkdir()
            (port / "power_role").write_text("sink\n")
            (port / "power_operation_mode").write_text("usb_power_delivery\n")
            partner = root / "port0-partner"; partner.mkdir()
            (partner / "supports_usb_power_delivery").write_text("maybe\n")
            _, ports = _discover_typec_ports(root)
            self.assertEqual(ports[0].partner_usb_pd_capable.status, ReadStatus.INVALID)
            self.assertEqual(ports[0].power_operation_mode.value, "usb_power_delivery")

    def test_missing_and_empty_roots_are_distinct(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            missing_status, _ = _discover_typec_ports(base / "missing")
            empty = base / "empty"; empty.mkdir()
            empty_status, ports = _discover_typec_ports(empty)
            self.assertEqual(missing_status, ReadStatus.MISSING)
            self.assertEqual(empty_status, ReadStatus.AVAILABLE)
            self.assertEqual(ports, ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_typec_context.py
```

Expected: missing `TypeCPortSnapshot`/`_discover_typec_ports`.

- [ ] **Step 3: Add the model, yes/no parser, presence parser, and discovery**

Add to `power_context.py`:

```python
@dataclass(frozen=True)
class TypeCPortSnapshot:
    port_id: str
    sysfs_path: Path
    power_role: ReadResult[str]
    power_operation_mode: ReadResult[str]
    partner_present: ReadResult[bool]
    partner_usb_pd_capable: ReadResult[bool]


def _read_yes_no(path):
    result = read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    if result.value == "yes":
        return ReadResult(ReadStatus.AVAILABLE, True)
    if result.value == "no":
        return ReadResult(ReadStatus.AVAILABLE, False)
    return ReadResult(ReadStatus.INVALID)


def _read_presence(path):
    try:
        return ReadResult(ReadStatus.AVAILABLE, Path(path).exists())
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)


def _port_sort_key(path):
    match = re.fullmatch(r"port([0-9]+)", path.name)
    return (int(match.group(1)), path.name)
```

Import `re`. `_discover_typec_ports` enumerates the root with the same root-status rules as Task 2, filters `re.fullmatch(r"port[0-9]+", name)`, uses `_active_enum(read_text(port / "power_role"))`, reads `power_operation_mode` directly, checks only `root / f"{port.name}-partner"`, and reads `supports_usb_power_delivery` only when the partner is present. If no partner is present, use `partner_usb_pd_capable=ReadResult(ReadStatus.MISSING)` rather than inferring `False`.

- [ ] **Step 4: Run GREEN and commit**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_typec_context.py
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py
python -m compileall -q auto_cpufreq/modules/power_context.py
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover USB Type-C context"
```

Expected: both suites PASS.

---

### Task 4: Discover USB-PD Capabilities and Compose `PowerContextSnapshot`

**Files:**
- Modify: `auto_cpufreq/modules/power_context.py`
- Test: `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py`

**Interfaces:**
- Produces `UsbPdCapabilityKind`, `UsbPdAttributeSnapshot`, `UsbPdCapabilitySnapshot`, `UsbPdSnapshot`, `PowerContextSnapshot`, `PowerContextDiscovery`.
- Produces `_discover_usb_pd_objects(root)`.
- PDO values remain in their documented numeric units (`mV`, `mA`, `mW`) plus an explicit `unit` string.

- [ ] **Step 1: Write failing USB-PD tests, including hotplug and root isolation**

Create `/tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py`:

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from auto_cpufreq.modules.power_context import (
    PowerContextDiscovery,
    UsbPdCapabilityKind,
    _discover_usb_pd_objects,
)
from auto_cpufreq.modules.sysfs import ReadStatus


def make_cap(pd, group, name, **attrs):
    path = pd / group / name
    path.mkdir(parents=True, exist_ok=True)
    for key, value in attrs.items():
        (path / key).write_text(value + "\n")


class UsbPdTests(unittest.TestCase):
    def test_types_units_numeric_order_and_unknown_kind(self):
        with TemporaryDirectory() as td:
            root = Path(td); pd = root / "pd0"; pd.mkdir()
            (pd / "revision").write_text("3.1\n")
            make_cap(pd, "source-capabilities", "10:future_supply", mystery="9")
            make_cap(pd, "source-capabilities", "2:variable_supply", maximum_voltage="20000mV", minimum_voltage="5000mV", maximum_current="3000mA")
            make_cap(pd, "source-capabilities", "1:fixed_supply", voltage="5000mV", maximum_current="3000mA", dual_role_power="1")
            make_cap(pd, "source-capabilities", "3:battery", maximum_voltage="20000mV", minimum_voltage="5000mV", maximum_power="45000mW")
            make_cap(pd, "source-capabilities", "4:programmable_supply", maximum_voltage="11000mV", minimum_voltage="3300mV", maximum_current="3000mA", pps_power_limited="1")
            make_cap(pd, "source-capabilities", "5:spr_adjustable_voltage_supply", maximum_current_9V_to_15V="3000mA", maximum_current_15V_to_20V="2500mA", peak_current="2")
            status, objects = _discover_usb_pd_objects(root)
            self.assertEqual(status, ReadStatus.AVAILABLE)
            caps = objects[0].source_capabilities
            self.assertEqual([c.position for c in caps], [1, 2, 3, 4, 5, 10])
            self.assertEqual(caps[0].kind, UsbPdCapabilityKind.FIXED_SUPPLY)
            fixed = {a.name: a for a in caps[0].attributes}
            self.assertEqual(fixed["voltage"].value.value, 5000)
            self.assertEqual(fixed["voltage"].unit, "mV")
            self.assertEqual(caps[-1].kind, UsbPdCapabilityKind.UNKNOWN)
            self.assertEqual(caps[-1].attributes, ())

    def test_bad_suffix_is_local_and_sink_schema_works(self):
        with TemporaryDirectory() as td:
            root = Path(td); pd = root / "pd0"; pd.mkdir()
            make_cap(pd, "sink-capabilities", "1:fixed_supply", voltage="5000mV", operational_current="3000watts")
            _, objects = _discover_usb_pd_objects(root)
            fields = {a.name: a for a in objects[0].sink_capabilities[0].attributes}
            self.assertEqual(fields["voltage"].value.status, ReadStatus.AVAILABLE)
            self.assertEqual(fields["operational_current"].value.status, ReadStatus.INVALID)

    def test_missing_empty_and_malformed_capability_name(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            missing_status, _ = _discover_usb_pd_objects(base / "missing")
            empty = base / "empty"; empty.mkdir()
            empty_status, empty_items = _discover_usb_pd_objects(empty)
            pd = base / "pdroot"; pd.mkdir(); obj = pd / "pd0"; obj.mkdir()
            make_cap(obj, "source-capabilities", "broken-name", voltage="5000mV")
            _, objects = _discover_usb_pd_objects(pd)
            self.assertEqual(missing_status, ReadStatus.MISSING)
            self.assertEqual(empty_status, ReadStatus.AVAILABLE)
            self.assertEqual(empty_items, ())
            self.assertIsNone(objects[0].source_capabilities[0].position)
            self.assertEqual(objects[0].source_capabilities[0].kind, UsbPdCapabilityKind.UNKNOWN)

    def test_object_disappearing_during_read_does_not_abort_other_objects(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            for name in ("pd0", "pd1"):
                obj = root / name; obj.mkdir(); (obj / "revision").write_text("3.1\n")
            original = Path.read_text
            def selective(path, *args, **kwargs):
                if str(path).endswith("pd0/revision"):
                    raise FileNotFoundError
                return original(path, *args, **kwargs)
            with patch.object(Path, "read_text", selective):
                status, objects = _discover_usb_pd_objects(root)
            self.assertEqual(status, ReadStatus.AVAILABLE)
            self.assertEqual(len(objects), 2)
            self.assertEqual(objects[0].revision.status, ReadStatus.MISSING)
            self.assertEqual(objects[1].revision.value, "3.1")

    def test_injected_roots_never_observe_host_sysfs(self):
        with TemporaryDirectory() as td:
            base = Path(td)
            ps = base / "power_supply"; ps.mkdir()
            tc = base / "typec"; tc.mkdir()
            pd = base / "usb_power_delivery"; pd.mkdir()
            snapshot = PowerContextDiscovery(ps, tc, pd).discover()
            self.assertEqual(snapshot.power_supplies, ())
            self.assertEqual(snapshot.typec_ports, ())
            self.assertEqual(snapshot.usb_pd_objects, ())
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py
```

Expected: missing USB-PD/top-level symbols.

- [ ] **Step 3: Add USB-PD models with Python-3.9-compatible typing**

Add:

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
    value: ReadResult[Union[int, bool]]
    unit: Optional[str] = None


@dataclass(frozen=True)
class UsbPdCapabilitySnapshot:
    position: Optional[int]
    kind: UsbPdCapabilityKind
    sysfs_name: str
    attributes: Tuple[UsbPdAttributeSnapshot, ...]


@dataclass(frozen=True)
class UsbPdSnapshot:
    pd_id: str
    sysfs_path: Path
    revision: ReadResult[str]
    version: ReadResult[str]
    source_capabilities: Tuple[UsbPdCapabilitySnapshot, ...]
    sink_capabilities: Tuple[UsbPdCapabilitySnapshot, ...]


@dataclass(frozen=True)
class PowerContextSnapshot:
    power_supply_status: ReadStatus
    typec_status: ReadStatus
    usb_pd_status: ReadStatus
    external_power: ExternalPower
    battery_flow: BatteryFlow
    power_supplies: Tuple[PowerSupplySnapshot, ...]
    batteries: Tuple[PowerSupplySnapshot, ...]
    typec_ports: Tuple[TypeCPortSnapshot, ...]
    usb_pd_objects: Tuple[UsbPdSnapshot, ...]
```

- [ ] **Step 4: Add exact suffix parsing and explicit per-kind schemas**

Add:

```python
def _read_suffixed_int(path, suffix):
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

Build capabilities from an explicit schema; do not dump arbitrary files. The supported fields are exactly:

```text
fixed source, PDO1 booleans: dual_role_power, usb_suspend_supported, unconstrained_power,
  usb_communication_capable, dual_role_data, unchunked_extended_messages_supported
fixed source, all positions: peak_current(int), voltage(mV), maximum_current(mA)
fixed sink, PDO1 booleans: dual_role_power, higher_capability, unconstrained_power,
  usb_communication_capable, dual_role_data, unchunked_extended_messages_supported
fixed sink, all positions: fast_role_swap_current(int), voltage(mV), operational_current(mA)
variable source: maximum_voltage(mV), minimum_voltage(mV), maximum_current(mA)
variable sink: maximum_voltage(mV), minimum_voltage(mV), operational_current(mA)
battery source: maximum_voltage(mV), minimum_voltage(mV), maximum_power(mW)
battery sink: maximum_voltage(mV), minimum_voltage(mV), operational_power(mW)
programmable source: maximum_voltage(mV), minimum_voltage(mV), maximum_current(mA), pps_power_limited(bool01)
programmable sink: maximum_voltage(mV), minimum_voltage(mV), maximum_current(mA)
SPR adjustable source: maximum_current_9V_to_15V(mA), maximum_current_15V_to_20V(mA), peak_current(int)
SPR adjustable sink: maximum_current_9V_to_15V(mA), maximum_current_15V_to_20V(mA)
```

For an unknown or malformed PDO name, set `kind=UNKNOWN`, preserve `sysfs_name`, set numeric `position` only when the prefix parses, and use `attributes=()`.

- [ ] **Step 5: Implement deterministic USB-PD discovery and top-level composition**

Capability names are parsed with:

```python
match = re.fullmatch(r"([0-9]+):(.+)", path.name)
```

Sort key:

```python
def _pdo_sort_key(path):
    match = re.fullmatch(r"([0-9]+):(.+)", path.name)
    if match:
        return (0, int(match.group(1)), path.name)
    return (1, 0, path.name)
```

`_discover_usb_pd_objects` uses the same root-status semantics as the other discoverers, enumerates PD objects lexically, reads `revision` and optional `version`, and reads only `source-capabilities` and `sink-capabilities` children.

Add:

```python
class PowerContextDiscovery:
    def __init__(
        self,
        power_supply_root=Path("/sys/class/power_supply"),
        typec_root=Path("/sys/class/typec"),
        usb_pd_root=Path("/sys/class/usb_power_delivery"),
        ignored_supply_substrings=(),
    ):
        self.power_supply_root = Path(power_supply_root)
        self.typec_root = Path(typec_root)
        self.usb_pd_root = Path(usb_pd_root)
        self.ignored_supply_substrings = tuple(ignored_supply_substrings)

    def discover(self):
        power_status, supplies = _discover_power_supplies(
            self.power_supply_root,
            self.ignored_supply_substrings,
        )
        typec_status, ports = _discover_typec_ports(self.typec_root)
        usb_pd_status, pd_objects = _discover_usb_pd_objects(self.usb_pd_root)
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

There is intentionally no cross-subsystem matching function.

- [ ] **Step 6: Run GREEN across all discovery suites and commit**

```bash
for test in \
  /tmp/auto-cpufreq-power-context-tests/test_power_supply_context.py \
  /tmp/auto-cpufreq-power-context-tests/test_typec_context.py \
  /tmp/auto-cpufreq-power-context-tests/test_usb_pd_context.py; do
  PYTHONPATH="$PWD" python "$test" || exit 1
done
python -m compileall -q auto_cpufreq/modules/power_context.py
git add auto_cpufreq/modules/power_context.py
git commit -m "feat: discover USB Power Delivery capabilities"
```

Expected: all tests PASS.

---

### Task 5: Integrate PowerContext Into `--debug` and STATS Only

**Files:**
- Modify: `auto_cpufreq/modules/system_info.py`
- Modify: `auto_cpufreq/modules/system_monitor.py`
- Modify: `auto_cpufreq/bin/auto_cpufreq.py`
- Test: `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`

**Interfaces:**
- `SystemReport.power_context: Optional[PowerContextSnapshot] = None`.
- `SystemInfo.generate_system_report(..., include_power_context=False)`.
- `format_power_context_debug(snapshot) -> list[str]` and `format_power_context_summary(snapshot) -> list[str]`.
- Root/hostfs selection occurs in `system_info.py`; `power_context.py` never imports Snap globals.

- [ ] **Step 1: Write failing reporting tests**

Create `/tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py`:

```python
import unittest
from pathlib import Path

from auto_cpufreq.modules.power_context import BatteryFlow, ExternalPower, PowerContextSnapshot, PowerSupplySnapshot
from auto_cpufreq.modules.sysfs import ReadResult, ReadStatus
from auto_cpufreq.modules.system_info import format_power_context_debug, format_power_context_summary

A = ReadStatus.AVAILABLE
M = ReadStatus.MISSING


class ReportingTests(unittest.TestCase):
    def snapshot(self):
        supply = PowerSupplySnapshot(
            supply_id="USBC",
            sysfs_path=Path("/synthetic/USBC"),
            type=ReadResult(A, "USB"), scope=ReadResult(M), online=ReadResult(A, True),
            status=ReadResult(M), usb_type=ReadResult(A, "PD"),
            voltage_now=ReadResult(A, 20_000_000), current_now=ReadResult(A, 2_000_000),
            power_now=ReadResult(M), input_current_limit=ReadResult(A, 3_250_000),
            input_voltage_limit=ReadResult(M), input_power_limit=ReadResult(A, 65_000_000),
            capacity=ReadResult(M), capacity_level=ReadResult(M),
        )
        return PowerContextSnapshot(
            power_supply_status=A, typec_status=M, usb_pd_status=M,
            external_power=ExternalPower.ONLINE, battery_flow=BatteryFlow.UNKNOWN,
            power_supplies=(supply,), batteries=(), typec_ports=(), usb_pd_objects=(),
        )

    def test_summary_is_compact_and_does_not_invent_power(self):
        text = "\n".join(format_power_context_summary(self.snapshot()))
        self.assertIn("External power: Online", text)
        self.assertIn("Battery flow: Unknown", text)
        self.assertIn("USB-PD: Missing", text)
        self.assertNotIn("40.0 W", text)
        self.assertNotIn("charger", text.lower())

    def test_debug_preserves_missing_and_formats_exposed_limit(self):
        text = "\n".join(format_power_context_debug(self.snapshot()))
        self.assertIn("Power Supply subsystem: Available", text)
        self.assertIn("USBC", text)
        self.assertIn("Input power limit: 65.000 W", text)
        self.assertIn("Power now: Unavailable", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH="$PWD" python /tmp/auto-cpufreq-power-context-tests/test_power_context_reporting.py
```

Expected: formatter import failure.

- [ ] **Step 3: Add report collection and hostfs root construction**

In `system_info.py`, import `Optional` if not already present, import `PowerContextDiscovery`/`PowerContextSnapshot`, and import shared `ReadStatus` from `modules.sysfs` instead of relying on the Intel module.

Add:

```python
def power_context_discovery():
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
power_context: Optional[PowerContextSnapshot] = None
```

Extend `generate_system_report`:

```python
def generate_system_report(
    self,
    include_intel_power=False,
    sample_intel_energy=False,
    include_power_context=False,
):
```

Inside it, collect exactly once:

```python
power_context_snapshot = (
    power_context_discovery().discover()
    if include_power_context
    else None
)
```

and pass `power_context=power_context_snapshot` into `SystemReport`.

- [ ] **Step 4: Add detailed and compact formatters**

Use one read-state formatter:

```python
def _format_power_read(result, formatter=str):
    if result.status is ReadStatus.MISSING:
        return "Unavailable"
    if result.status is ReadStatus.UNREADABLE:
        return "Could not be read"
    if result.status is ReadStatus.INVALID:
        return "Invalid"
    if result.value is None:
        return "Unknown"
    return formatter(result.value)
```

`format_power_context_debug` must emit aggregates, subsystem statuses, every supply, every Type-C port, and every USB-PD/PDO object. Power-supply micro-units are formatted as V/A/W by dividing by `1_000_000`; USB-PD values use their recorded `mV`/`mA`/`mW` unit and are not converted into a synthetic contract value.

`format_power_context_summary` must emit exactly these categories:

```text
External power: <Online|Offline|Unknown>
Battery flow: <Charging|Discharging|Not charging|Full|Mixed|Unknown>
Type-C: <Missing|Unreadable|Available (N ports)>
USB-PD: <Missing|Unreadable|Available (N objects)>
```

No active port/source is selected.

In `format_system_report`, when `report.power_context is not None`, append one `Power Context Diagnostics` section and extend with `format_power_context_debug(report.power_context)`.

- [ ] **Step 5: Wire only the approved frontends**

In `auto_cpufreq/bin/auto_cpufreq.py`, change only the debug report call to:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=False,
    include_power_context=True,
)
```

In `system_monitor.py`, STATS only becomes:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=True,
    include_power_context=True,
)
```

MONITOR and LIVE keep the default call. Import `format_power_context_summary`; inside `format_system_info`, when `self.type == ViewType.STATS and report.power_context is not None`, render a `Power Context` header followed by those summary lines.

- [ ] **Step 6: Run GREEN, compile, and audit control-path isolation**

```bash
for test in /tmp/auto-cpufreq-power-context-tests/test_*.py; do
  PYTHONPATH="$PWD" python "$test" || exit 1
done
python -m compileall -q auto_cpufreq
git diff -- auto_cpufreq/modules/policy.py auto_cpufreq/modules/daemon_scheduler.py auto_cpufreq/modules/intel_rapl.py auto_cpufreq/power_helper.py
```

Expected: all tests PASS, compileall 0, final `git diff` prints nothing.

- [ ] **Step 7: Commit reporting integration**

```bash
git add auto_cpufreq/modules/system_info.py auto_cpufreq/modules/system_monitor.py auto_cpufreq/bin/auto_cpufreq.py
git commit -m "feat: report power context diagnostics"
```

---

### Task 6: Final Synthetic, CI, and Read-Only Hardware Verification

**Files:**
- Production files from Tasks 1-5 only
- Optional create after evidence exists: `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md`

**Interfaces:**
- Consumes the final `PowerContextDiscovery`, `PowerContextSnapshot`, debug formatter, and stats summary.
- Adds no new feature behavior.

- [ ] **Step 1: Add the remaining exact synthetic cases before final verification**

Append these concrete tests to the existing temporary suites:

```python
# test_power_supply_context.py

def test_all_known_battery_states_and_unknown_status(self):
    expected = {
        "Charging": BatteryFlow.CHARGING,
        "Discharging": BatteryFlow.DISCHARGING,
        "Not charging": BatteryFlow.NOT_CHARGING,
        "Full": BatteryFlow.FULL,
    }
    for status, flow in expected.items():
        with self.subTest(status=status), TemporaryDirectory() as td:
            root = Path(td); make_supply(root, "BAT0", type="Battery", status=status)
            _, supplies = _discover_power_supplies(root)
            self.assertEqual(_aggregate_battery_flow(_system_batteries(supplies)), flow)
    with TemporaryDirectory() as td:
        root = Path(td); make_supply(root, "BAT0", type="Battery", status="Mystery")
        _, supplies = _discover_power_supplies(root)
        self.assertEqual(_aggregate_battery_flow(_system_batteries(supplies)), BatteryFlow.UNKNOWN)


def test_deterministic_supply_order(self):
    with TemporaryDirectory() as td:
        root = Path(td)
        for name in ("Z", "A", "M"):
            make_supply(root, name, type="Mains", online="0")
        _, supplies = _discover_power_supplies(root)
        self.assertEqual([s.supply_id for s in supplies], ["A", "M", "Z"])
```

```python
# test_typec_context.py

def test_partner_no_is_false_without_inference(self):
    with TemporaryDirectory() as td:
        root = Path(td); port = root / "port0"; port.mkdir()
        (port / "power_role").write_text("sink\n")
        (port / "power_operation_mode").write_text("usb_power_delivery\n")
        partner = root / "port0-partner"; partner.mkdir()
        (partner / "supports_usb_power_delivery").write_text("no\n")
        _, ports = _discover_typec_ports(root)
        self.assertEqual(ports[0].partner_usb_pd_capable.value, False)
        self.assertEqual(ports[0].power_operation_mode.value, "usb_power_delivery")
```

```python
# test_usb_pd_context.py

def test_boolean_and_power_units(self):
    with TemporaryDirectory() as td:
        root = Path(td); pd = root / "pd0"; pd.mkdir()
        make_cap(pd, "source-capabilities", "1:fixed_supply", voltage="5000mV", maximum_current="3000mA", dual_role_power="1")
        make_cap(pd, "source-capabilities", "2:battery", maximum_voltage="20000mV", minimum_voltage="5000mV", maximum_power="45000mW")
        _, objects = _discover_usb_pd_objects(root)
        fixed = {a.name: a for a in objects[0].source_capabilities[0].attributes}
        battery = {a.name: a for a in objects[0].source_capabilities[1].attributes}
        self.assertEqual(fixed["dual_role_power"].value.value, True)
        self.assertEqual(battery["maximum_power"].value.value, 45000)
        self.assertEqual(battery["maximum_power"].unit, "mW")
```

- [ ] **Step 2: Run the entire temporary suite and local compile gate**

```bash
for test in /tmp/auto-cpufreq-power-context-tests/test_*.py; do
  echo "==> $test"
  PYTHONPATH="$PWD" python "$test" || exit 1
done
python -m compileall -q auto_cpufreq
```

Expected: every test PASS; compileall exits 0.

- [ ] **Step 3: Verify final production diff and five-commit topology**

```bash
git log --oneline --decorate --reverse usb-c-power-context-design..HEAD
git diff --stat usb-c-power-context-design..HEAD
git diff --name-only usb-c-power-context-design..HEAD
```

Expected implementation commits:

```text
refactor: share generic sysfs readers
feat: discover system power context
feat: discover USB Type-C context
feat: discover USB Power Delivery capabilities
feat: report power context diagnostics
```

Expected changed production files only:

```text
auto_cpufreq/modules/sysfs.py
auto_cpufreq/modules/intel_power.py
auto_cpufreq/modules/power_context.py
auto_cpufreq/modules/system_info.py
auto_cpufreq/modules/system_monitor.py
auto_cpufreq/bin/auto_cpufreq.py
```

Any policy/scheduler/RAPL/service/config/installer/power-helper change is a blocker.

- [ ] **Step 4: Push only the implementation branch and require existing CI**

```bash
git push -u origin usb-c-power-context
```

Do not open an upstream PR. Require `Linux Build` and `Nix Flake` to pass on the final production commit. If either fails, inspect that exact run and fix only the demonstrated defect.

- [ ] **Step 5: Capture read-only hardware baseline on the Samsung**

Run from the implementation checkout with the charger connected:

```bash
{
  echo '=== no_turbo ==='; cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || true
  echo '=== hwp_dynamic_boost ==='; cat /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 2>/dev/null || true
  echo '=== cpufreq policy controls ==='
  for p in /sys/devices/system/cpu/cpufreq/policy*; do
    printf '%s|' "$p"
    for f in scaling_governor energy_performance_preference scaling_min_freq scaling_max_freq; do
      printf '%s=' "$f"; cat "$p/$f" 2>/dev/null | tr '\n' ' '; printf '|'
    done
    echo
  done
  echo '=== RAPL limits ==='
  while IFS= read -r f; do printf '%s=' "$f"; cat "$f"; done < <(find /sys/class/powercap -name 'constraint_*_power_limit_uw' -readable -print 2>/dev/null | sort)
} > /tmp/power-context-controls-before.txt

find /sys/class/power_supply -maxdepth 2 -print 2>/dev/null | sort > /tmp/power-context-power-supply-tree.txt
find /sys/class/typec -maxdepth 3 -print 2>/dev/null | sort > /tmp/power-context-typec-tree.txt
find /sys/class/usb_power_delivery -maxdepth 4 -print 2>/dev/null | sort > /tmp/power-context-usb-pd-tree.txt
```

Missing Type-C or USB-PD roots are valid platform observations.

- [ ] **Step 6: Compare raw sysfs, snapshot, and debug while connected**

Run:

```bash
PYTHONPATH="$PWD" python - <<'PY'
from pprint import pprint
from auto_cpufreq.modules.power_context import PowerContextDiscovery
pprint(PowerContextDiscovery().discover())
PY
```

Then run the feature checkout's normal `--debug` entry point. Manually compare every exposed `power_supply`/Type-C/USB-PD field against its raw sysfs value. Verify that advertised PDO capability is never labeled current negotiated wattage.

- [ ] **Step 7: Repeat factual observation disconnected, then reconnect**

Physically disconnect external power, rerun the direct snapshot and raw `power_supply` reads, then reconnect. If all relevant external supplies explicitly report offline, require `ExternalPower.OFFLINE`; if material source state is unresolved, require `UNKNOWN`. Do not force either expected result before observing the platform.

- [ ] **Step 8: Prove reporting caused no control-state write**

After reconnecting and allowing the existing daemon/platform to settle, rerun the exact Step-5 control snapshot into `/tmp/power-context-controls-after.txt` and compare:

```bash
diff -u /tmp/power-context-controls-before.txt /tmp/power-context-controls-after.txt
```

Expected: no diff attributable to the new reporting code. Also inspect write primitives:

```bash
git grep -nE 'write_text|open\([^)]*["'"']w|os\.write|subprocess|run\(' -- auto_cpufreq/modules/power_context.py auto_cpufreq/modules/sysfs.py
```

Expected: no hardware-write path in `power_context.py`; `sysfs.py` contains reads only.

- [ ] **Step 9: Record validation evidence only after all gates pass**

Create `docs/superpowers/validation/2026-09-11-usb-c-power-context-validation.md` with exact final production SHA, both CI run IDs/results, Samsung kernel version, observed objects, presence/absence of Type-C and USB-PD roots, connected/disconnected aggregate results, raw↔snapshot↔debug comparison, and no-write comparison. Commit this validation document separately from the validated production-code HEAD.

- [ ] **Step 10: Completion gate**

Before claiming completion, verify all spec criteria explicitly: local error degradation; independent subsystem availability; preserved multiple supplies/batteries; `MIXED`; Device-scope exclusion; advertised-vs-current separation; no heuristic correlation; unknown PDO tolerance; detailed debug; compact stats; unchanged Intel semantics; unchanged daemon/policy; no hardware writes; real-hardware agreement wherever the ABI exists.
