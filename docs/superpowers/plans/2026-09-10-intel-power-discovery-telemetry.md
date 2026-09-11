# Intel Power Discovery and Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Intel power discovery and telemetry layer that reports modern `intel_pstate`/HWP, CPUFreq policy, Powercap/RAPL, energy, and thermal-throttle state without changing any hardware policy.

**Architecture:** Introduce one dependency-light module, `auto_cpufreq/modules/intel_power.py`, that owns safe sysfs discovery and stateful energy-delta sampling. Existing reporting code consumes a normalized snapshot only when Intel diagnostics are explicitly requested by `--debug` or `--stats`; normal daemon reporting must not gain extra RAPL/thermal polling in this stage. All hardware writes, policy selection, event-driven control, watchdog changes, and RAPL envelopes remain out of scope.

**Tech Stack:** Python standard library (`dataclasses`, `enum`, `pathlib`, `time`, `typing`), Linux sysfs (`/sys/devices/system/cpu`, `/sys/class/powercap`), existing `SystemInfo`/`SystemMonitor` reporting infrastructure, existing GitHub Actions installer build only.

**Spec:** `docs/superpowers/specs/2026-09-10-modern-intel-power-policy-design.md`

## Global Constraints

- Stage 1 is **100% read-only**. It must not write governor, EPP, EPB, HWP Dynamic Boost, turbo, CPU frequency limits, Platform Profile, Powercap constraints, or service state.
- Development base remains the current design branch based on commit `070693c94170f6ac4b1a01e3bcc41d3d798825d2`.
- Do not add a new runtime dependency.
- Do not add pytest, a new CI job, or a committed upstream test framework in this stage.
- Local verification may use temporary standard-library Python scripts/fixtures, but those files must not remain in the final diff.
- Discover by kernel ABI contents (`name`, `constraint_X_name`, CPUFreq policy directories), never by assuming numeric indexes have fixed meanings.
- Missing, unreadable, and invalid data must remain distinguishable where that distinction is user-visible; unknown data must never be fabricated.
- Energy telemetry uses `time.monotonic_ns()` and each zone's actual `max_energy_range_uj`.
- Differential power calculation supports at most one energy-counter wrap between samples and must document that assumption.
- Thermal package counters must not be summed once per logical CPU.
- New Intel telemetry is collected only when explicitly requested by diagnostics/stats in this stage; the existing daemon policy loop must not become more expensive.
- Keep the existing legacy behavior and all active power policy untouched.

---

## File Structure

### Create

- `auto_cpufreq/modules/intel_power.py`
  - Safe read-only sysfs primitives.
  - CPUFreq policy and Intel P-state/HWP discovery.
  - Powercap zone/constraint discovery.
  - Stateful energy-delta sampling.
  - Package-level thermal-throttle discovery.
  - Normalized dataclasses used by reporting code.
  - No imports from `auto_cpufreq.core`, `system_info`, config, or UI modules.

### Modify

- `auto_cpufreq/modules/system_info.py`
  - Add an optional Intel power snapshot to `SystemReport`.
  - Add an explicit `include_intel_power=False` collection flag.
  - Format compact Intel diagnostics for text output.
  - Preserve current default collection cost when the flag is false.

- `auto_cpufreq/modules/system_monitor.py`
  - Request Intel telemetry only for `ViewType.STATS`.
  - Render a compact Intel Power section without changing `MONITOR` or `LIVE` suggestions.

- `auto_cpufreq/bin/auto_cpufreq.py`
  - Make `--debug` request the enhanced read-only snapshot.
  - Do not change daemon/live/monitor policy behavior.

### Explicitly unchanged

- `auto_cpufreq/core.py`
- `scripts/cpufreqctl.sh`
- `scripts/auto-cpufreq.service`
- `auto-cpufreq.conf-example`
- `pyproject.toml`
- GitHub Actions workflows

If implementation pressure suggests modifying any explicitly unchanged file, stop and reassess the Stage 1 boundary before proceeding.

---

### Task 1: Add the Read-Only Sysfs Model and CPUFreq/Intel Discovery

**Files:**
- Create: `auto_cpufreq/modules/intel_power.py`
- Temporary local verification only: no committed test file

**Interfaces:**
- Produces: `ReadStatus`, `ReadResult`, `CpuFreqPolicySnapshot`, `IntelPowerSnapshot`, `IntelPowerDiscovery.snapshot()`.
- Consumes: only standard-library filesystem APIs.
- Later tasks extend `IntelPowerSnapshot`; reporting code must consume the dataclasses rather than reread sysfs directly.

- [ ] **Step 1: Create a temporary synthetic-sysfs verification script before implementation**

Run a standard-library-only script from the repository root that creates a `tempfile.TemporaryDirectory`, then builds these paths under a temporary CPU root:

```text
intel_pstate/status                         -> active
intel_pstate/hwp_dynamic_boost              -> 1
cpufreq/policy0/scaling_driver              -> intel_pstate
cpufreq/policy0/scaling_governor            -> powersave
cpufreq/policy0/related_cpus                 -> 0 2
cpufreq/policy0/cpuinfo_min_freq            -> 400000
cpufreq/policy0/cpuinfo_max_freq            -> 4500000
cpufreq/policy0/scaling_min_freq             -> 400000
cpufreq/policy0/scaling_max_freq             -> 4500000
cpufreq/policy0/energy_performance_preference -> balance_performance
cpufreq/policy0/energy_performance_available_preferences
                                              -> default performance balance_performance balance_power power
cpufreq/policy1/...                          -> second policy with CPUs 1 3
```

The script should import the not-yet-created API and assert that two policies are returned, `intel_pstate/status` is `active`, Dynamic Boost is `True`, and policy CPU lists remain distinct. The first run must fail because `auto_cpufreq.modules.intel_power` does not exist.

- [ ] **Step 2: Define explicit read-state primitives**

Implement these public types in `intel_power.py`:

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
```

Add private helpers with these signatures:

```python
def _read_text(path: Path) -> ReadResult[str]: ...
def _read_int(path: Path) -> ReadResult[int]: ...
def _parse_cpu_list(value: str) -> tuple[int, ...]: ...
```

Required behavior:

- nonexistent path -> `MISSING`;
- `OSError` on an existing path -> `UNREADABLE`;
- integer parse failure -> `INVALID`;
- CPU lists accept both whitespace-separated CPUs (`"0 2 4"`) and kernel range/list syntax (`"0-3,8"`) because different topology attributes use different formats;
- invalid ranges raise internally and become `INVALID`, never an invented empty/CPU0 topology.

- [ ] **Step 3: Define the CPUFreq and Intel snapshot dataclasses**

Add:

```python
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
class IntelPowerSnapshot:
    intel_pstate_status: ReadResult[str]
    hwp_dynamic_boost: ReadResult[bool]
    cpufreq_policies: tuple[CpuFreqPolicySnapshot, ...]
    powercap_zones: tuple = ()
    thermal_packages: tuple = ()
```

Do **not** add a definitive `modern_hwp_eligible` decision in Stage 1. Stage 1 reports the evidence; Stage 2 owns policy selection so discovery cannot silently become control policy.

- [ ] **Step 4: Implement CPUFreq policy discovery**

Create:

```python
class IntelPowerDiscovery:
    def __init__(
        self,
        cpu_root: Path = Path("/sys/devices/system/cpu"),
        powercap_root: Path = Path("/sys/class/powercap"),
    ) -> None: ...

    def snapshot(self, sample_energy: bool = False) -> IntelPowerSnapshot: ...
```

CPU policy enumeration must use:

```text
<cpu_root>/cpufreq/policy*
```

and sort numeric policy suffixes numerically when possible. Do not derive policy membership from `cpu0/cpufreq` symlinks.

Dynamic Boost handling:

- missing `intel_pstate/hwp_dynamic_boost` -> `MISSING`;
- contents `0`/`1` -> `False`/`True`;
- other contents -> `INVALID`.

`intel_pstate/status` remains a raw observed value (`active`, `passive`, `off`, or another kernel-provided string); do not normalize an unknown string into a guessed mode.

- [ ] **Step 5: Re-run the synthetic CPUFreq fixture and add negative cases**

Using the same temporary script, add assertions for:

- no `intel_pstate` directory;
- no `cpufreq` directory;
- malformed `related_cpus`;
- missing EPP file;
- unreadable data simulated by pointing one attribute path at a directory so `read_text()` raises `OSError`/`IsADirectoryError`.

Expected: snapshot creation never raises for these ordinary absence/read failures; each field reports the appropriate state.

- [ ] **Step 6: Verify Python syntax/imports and commit**

Run:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
python -c 'from auto_cpufreq.modules.intel_power import IntelPowerDiscovery; print(IntelPowerDiscovery)'
```

Expected: both commands succeed without root privileges and without touching hardware state.

Delete the temporary verification script, confirm it is untracked/absent, then commit only the module:

```bash
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: discover Intel power capabilities"
```

---

### Task 2: Add Generic Powercap Discovery and Wrap-Safe Energy Sampling

**Files:**
- Modify: `auto_cpufreq/modules/intel_power.py`
- Temporary local verification only: no committed test file

**Interfaces:**
- Consumes: `ReadResult`, `IntelPowerDiscovery` from Task 1.
- Produces: `PowercapConstraintSnapshot`, `PowercapZoneSnapshot`, `PowerSample`, `EnergySampler.sample()`; populates `IntelPowerSnapshot.powercap_zones`.

- [ ] **Step 1: Build a synthetic Powercap tree that initially fails discovery**

Create a temporary Powercap root with independent trees such as:

```text
intel-rapl:0/
  name                         -> package-0
  energy_uj                    -> 90000000
  max_energy_range_uj          -> 100000000
  constraint_0_name            -> long_term
  constraint_0_power_limit_uw  -> 15000000
  constraint_0_time_window_us  -> 28000000
  constraint_1_name            -> short_term
  constraint_1_power_limit_uw  -> 35000000

intel-rapl:0:0/
  name                         -> core
  energy_uj                    -> 45000000
  max_energy_range_uj          -> 100000000

intel-rapl-mmio:0/
  name                         -> package-0
  constraint_0_name            -> long_term
  constraint_0_power_limit_uw  -> 12000000
```

Assertions must require three distinct zones and must **not merge** the two `package-0` zones merely because their `name` contents match.

- [ ] **Step 2: Add normalized Powercap dataclasses**

Implement:

```python
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
```

`zone_id` must be stable within one boot and distinguish control trees. Use a path relative to `powercap_root` (for example `intel-rapl:0` versus `intel-rapl-mmio:0`) rather than the human-readable `name` alone.

- [ ] **Step 3: Enumerate zones and named constraints without index semantics**

Rules:

- consider an entry a zone only if it exposes zone-like ABI files such as `name`, `energy_uj`, or `constraint_*_name`;
- follow the hierarchy exposed under the configured `powercap_root`, including nested zone directories when present;
- enumerate indexes by scanning actual `constraint_*_name` files;
- sort constraint indexes numerically;
- associate `constraint_X_power_limit_uw` and time-window/min/max files only with the same `X`;
- preserve the raw constraint name; never translate index `0` into `long_term` or index `1` into `short_term` unless the `constraint_X_name` file says so;
- tolerate a zone with energy telemetry but no writable constraints and vice versa.

The module remains read-only; do not call `os.access(..., os.W_OK)` as proof of future RAPL writability in this stage.

- [ ] **Step 4: Implement the stateful energy sampler independently of filesystem reads**

Add:

```python
class EnergySampler:
    def __init__(self) -> None:
        self._previous: dict[str, tuple[int, int]] = {}

    def sample(
        self,
        zone_id: str,
        energy_uj: int,
        max_range_uj: Optional[int],
        now_ns: Optional[int] = None,
    ) -> Optional[PowerSample]: ...
```

Required algorithm:

```python
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
    average_power_w=(delta_uj / 1_000_000) / (elapsed_ns / 1_000_000_000),
)
```

Document directly on `sample()` that correctness assumes no more than one hardware-counter wrap between consecutive samples.

- [ ] **Step 5: Add deterministic local assertions for normal, wrapped, and invalid samples**

Use explicit `now_ns` values so the test is deterministic:

```python
sampler.sample("pkg0", 90_000_000, 100_000_000, now_ns=0)
sample = sampler.sample("pkg0", 10_000_000, 100_000_000, now_ns=1_000_000_000)
assert sample.delta_energy_uj == 20_000_000
assert sample.elapsed_ns == 1_000_000_000
assert sample.average_power_w == 20.0
```

Also assert:

- first sample returns `None`;
- backwards counter with missing range returns `None` but updates baseline;
- zero/negative elapsed time returns `None`;
- two human-readable zones named `package-0` keep independent sampler state because their `zone_id`s differ.

- [ ] **Step 6: Wire energy sampling into `snapshot(sample_energy=True)` only**

`IntelPowerDiscovery` owns one `EnergySampler` instance so successive stats snapshots can produce deltas. When `sample_energy=False`, discovery reads static capability data but must not alter the sampler baseline.

This distinction prevents one-shot debug/daemon calls from perturbing the stats sampling history.

- [ ] **Step 7: Run fixture checks, compile, and commit**

Run:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
```

Run the temporary synthetic Powercap and energy-sampling assertions, then delete the temporary script and commit:

```bash
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: report Intel RAPL telemetry"
```

---

### Task 3: Add Package-Deduplicated Thermal Throttle Telemetry

**Files:**
- Modify: `auto_cpufreq/modules/intel_power.py`
- Temporary local verification only: no committed test file

**Interfaces:**
- Consumes: `cpu_root`, `ReadResult`.
- Produces: `ThermalThrottleSnapshot`; populates `IntelPowerSnapshot.thermal_packages`.

- [ ] **Step 1: Create a synthetic topology with duplicate package counters**

Build:

```text
cpu0/topology/physical_package_id -> 0
cpu0/thermal_throttle/package_throttle_count -> 12
cpu0/thermal_throttle/package_throttle_total_time_ms -> 300
cpu0/thermal_throttle/package_throttle_max_time_ms -> 40

cpu1/topology/physical_package_id -> 0
cpu1/thermal_throttle/package_throttle_count -> 12
...

cpu2/topology/physical_package_id -> 1
cpu2/thermal_throttle/package_throttle_count -> 4
...
```

The initial assertion should require exactly two package snapshots, not a sum of 28 events.

- [ ] **Step 2: Add the package thermal dataclass**

```python
@dataclass(frozen=True)
class ThermalThrottleSnapshot:
    package_id: int
    representative_cpu: int
    throttle_count: ReadResult[int]
    total_time_ms: ReadResult[int]
    max_time_ms: ReadResult[int]
```

- [ ] **Step 3: Implement topology-aware deduplication**

Algorithm:

1. Enumerate `cpu[0-9]*` directories numerically.
2. Read `topology/physical_package_id`.
3. CPUs with missing/unreadable/invalid package IDs are not assigned to a fabricated package.
4. For each known package, choose the lowest-numbered CPU that actually exposes `thermal_throttle` package metrics as the representative.
5. Read package metrics only from that representative CPU.
6. Keep packages separate; do not return a global sum.

If no representative CPU exposes throttle files, omit that package from `thermal_packages` rather than reporting zero throttles.

- [ ] **Step 4: Verify missing topology and missing thermal ABI**

Temporary assertions must cover:

- duplicate logical CPUs in one package -> one result;
- two physical packages -> two results;
- unreadable `physical_package_id` -> no fabricated package 0;
- package ID known but no thermal files -> package omitted;
- optional `total_time_ms`/`max_time_ms` absent -> corresponding `MISSING`, not zero.

- [ ] **Step 5: Compile and commit**

Run temporary assertions and:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
```

Delete temporary fixtures and commit:

```bash
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: report Intel thermal throttle state"
```

---

### Task 4: Integrate Intel Diagnostics into `SystemReport` and `--debug` Without Increasing Daemon Polling

**Files:**
- Modify: `auto_cpufreq/modules/system_info.py`
- Modify: `auto_cpufreq/bin/auto_cpufreq.py`
- Read-only import from: `auto_cpufreq/modules/intel_power.py`

**Interfaces:**
- Consumes: `IntelPowerDiscovery.snapshot(sample_energy=...)`, `IntelPowerSnapshot`.
- Produces: `SystemReport.intel_power`, `format_intel_power_summary()`.
- Existing callers that do not opt in must see no new discovery work.

- [ ] **Step 1: Add a temporary call-count verification before changing `SystemInfo`**

Use a temporary script with `unittest.mock.patch` around `IntelPowerDiscovery.snapshot` to establish the target behavior:

```python
report = system_info.generate_system_report()
assert snapshot_mock.call_count == 0

report = system_info.generate_system_report(include_intel_power=True)
assert snapshot_mock.call_count == 1
```

The test should fail before the new argument exists.

- [ ] **Step 2: Add optional Intel snapshot collection to `SystemReport`**

Import:

```python
from auto_cpufreq.modules.intel_power import (
    IntelPowerSnapshot,
    intel_power,
)
```

Expose a module singleton in `intel_power.py`:

```python
intel_power = IntelPowerDiscovery()
```

Add to `SystemReport`:

```python
intel_power: IntelPowerSnapshot | None = None
```

Change:

```python
def generate_system_report(
    self,
    include_intel_power: bool = False,
    sample_intel_energy: bool = False,
) -> SystemReport:
```

and set:

```python
intel_power=(
    intel_power.snapshot(sample_energy=sample_intel_energy)
    if include_intel_power
    else None
)
```

Constraint: every existing call that omits these arguments must retain today's behavior and collection cost.

- [ ] **Step 3: Add a compact text formatter with explicit state semantics**

Implement in `system_info.py`:

```python
def format_intel_power_summary(snapshot: IntelPowerSnapshot) -> list[str]: ...
```

Required output shape, adapting only to capabilities actually present:

```text
Intel Power Diagnostics
intel_pstate: active
HWP Dynamic Boost: On
CPUFreq policies: 2
  policy0: CPUs 0,2 · intel_pstate · powersave · EPP balance_performance
  policy1: CPUs 1,3 · intel_pstate · powersave · EPP balance_performance
Powercap zones: 3
  intel-rapl:0 · package-0
    energy: 90.000 J · average: Awaiting next sample
    long_term: 15.00 W · window 28.000 s
    short_term: 35.00 W
  intel-rapl-mmio:0 · package-0
    long_term: 12.00 W
Thermal throttle:
  package 0: 12 events · 300 ms total · 40 ms max
```

Formatting rules:

- `MISSING` -> `Unavailable` only when the ABI is optional;
- `UNREADABLE`/`INVALID` -> `Unknown` or `Could not be read`, never `0`;
- raw energy is converted from µJ to J for display;
- power is converted from µW to W for display;
- time windows are converted from µs to seconds for display;
- a first sample with no delta says `Awaiting next sample` rather than `0 W`;
- do not label a Powercap controller as MSR/MMIO/TPMI unless that identity is directly exposed by the observed sysfs path/name; report the kernel-visible control/zone identifier.

- [ ] **Step 4: Make `--debug` explicitly request Intel diagnostics**

In `auto_cpufreq/bin/auto_cpufreq.py`, change only the `debug` branch so it creates one enhanced report and passes that report to the existing printer:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=False,
)
print_system_report(report)
```

`--debug` remains one-shot; it must not sleep merely to manufacture an average-power sample.

Do not modify `daemon`, `live`, or `monitor` behavior in this task.

- [ ] **Step 5: Append Intel diagnostics to textual system formatting only when present**

`format_system_report()` should leave existing output unchanged when `report.intel_power is None`. When present, append a separated Intel Power Diagnostics section using `format_intel_power_summary()`.

This preserves all non-debug callers and avoids changing legacy status output unnecessarily.

- [ ] **Step 6: Verify opt-in collection and read-only behavior**

Run the temporary call-count check. Then run:

```bash
python -m compileall -q auto_cpufreq/modules/system_info.py auto_cpufreq/bin/auto_cpufreq.py
```

On a machine without Intel Powercap/HWP, run if available:

```bash
sudo auto-cpufreq --debug
```

Expected: command completes and reports unavailable/unknown optional Intel interfaces instead of raising.

Check the diff for any write operation under `/sys`; Stage 1 must contain none.

- [ ] **Step 7: Commit reporting integration**

```bash
git add auto_cpufreq/modules/system_info.py auto_cpufreq/bin/auto_cpufreq.py
git commit -m "feat: expose Intel power diagnostics"
```

---

### Task 5: Add Live Intel Energy/Power Reporting to `--stats` Only

**Files:**
- Modify: `auto_cpufreq/modules/system_monitor.py`
- Reuse: `SystemInfo.generate_system_report()` and `format_intel_power_summary()`

**Interfaces:**
- Consumes: `SystemReport.intel_power` and the persistent `intel_power` sampler singleton.
- Produces: a Stats-only Intel Power section with average package/subdomain power after the second sample.

- [ ] **Step 1: Add a temporary behavior check for view-specific collection**

Patch `system_info.generate_system_report` in a temporary script and call `_collect_report()` or isolate the argument-selection helper introduced below. Assert:

```text
STATS   -> include_intel_power=True, sample_intel_energy=True
MONITOR -> include_intel_power=False, sample_intel_energy=False
LIVE    -> include_intel_power=False, sample_intel_energy=False
```

This must fail before the view-specific arguments are implemented.

- [ ] **Step 2: Request Intel sampling only in Stats mode**

Inside `_collect_report()`, derive:

```python
include_intel_power = self.type == ViewType.STATS
report = system_info.generate_system_report(
    include_intel_power=include_intel_power,
    sample_intel_energy=include_intel_power,
)
```

Do not change the 2-second Stats UI refresh cadence in Stage 1.

Do not collect Intel Powercap telemetry in `MONITOR` or `LIVE`, because those modes are part of current policy/suggestion workflows and Stage 1 must not increase their hardware polling surface.

- [ ] **Step 3: Render the Intel section only when a snapshot exists**

Import `format_intel_power_summary` from `system_info` and append a right-column section after CPU Power State or System Statistics:

```text
Intel Power Diagnostics
...
```

Use the same formatter as `--debug` so textual semantics do not drift between frontends.

The first Stats refresh may show `Awaiting next sample`; subsequent refreshes should show average watts for zones with valid counters/ranges.

- [ ] **Step 4: Verify sampler continuity across refreshes**

With a temporary fake discovery tree and deterministic `EnergySampler` values, verify that two consecutive Stats collections use the same module-level `intel_power` instance and the second snapshot receives a `PowerSample` rather than resetting the sampler every refresh.

Also verify switching through `MONITOR`/`LIVE` calls does not advance the Intel energy baseline because those calls use `sample_intel_energy=False`.

- [ ] **Step 5: Compile and run a smoke check**

Run:

```bash
python -m compileall -q auto_cpufreq/modules/system_monitor.py
```

On the Intel test notebook, run:

```bash
sudo auto-cpufreq --stats
```

Observe at least two refreshes. Expected:

- no traceback;
- Powercap zones remain distinct;
- first energy sample may be pending;
- later samples report plausible non-negative average watts;
- exiting Stats leaves hardware values unchanged.

- [ ] **Step 6: Commit Stats integration**

```bash
git add auto_cpufreq/modules/system_monitor.py
git commit -m "feat: show Intel power telemetry in stats"
```

---

### Task 6: Validate Stage 1 on Real Hardware and Against the Upstream Build Contract

**Files:**
- No new production files unless validation exposes a Stage 1 bug.
- Final diff must contain no temporary fixture/test files.

**Interfaces:**
- Validates the complete Stage 1 observable behavior.

- [ ] **Step 1: Confirm branch scope**

Compare the implementation branch against its Stage 1 base and confirm only the expected production files changed:

```text
auto_cpufreq/modules/intel_power.py
auto_cpufreq/modules/system_info.py
auto_cpufreq/modules/system_monitor.py
auto_cpufreq/bin/auto_cpufreq.py
```

Any changes to policy, installer, service, config examples, dependencies, or workflows require explicit review before proceeding.

- [ ] **Step 2: Run repository-level syntax/build checks**

Run:

```bash
python -m compileall -q auto_cpufreq
```

Then run the same installer/build path exercised by the existing Linux GitHub Actions workflow in an appropriate disposable environment or worktree:

```bash
sudo ./auto-cpufreq-installer --install
```

Expected: existing installation path still succeeds without a test-framework or dependency change.

- [ ] **Step 3: Capture a read-only diagnostic baseline on the Intel i3-1315U test system**

Before starting `auto-cpufreq --debug`, capture the relevant values:

```bash
cat /sys/devices/system/cpu/intel_pstate/status 2>/dev/null || true
cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || true
cat /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 2>/dev/null || true
find /sys/devices/system/cpu/cpufreq -maxdepth 2 -type f \
  \( -name scaling_governor -o -name energy_performance_preference \
     -o -name scaling_min_freq -o -name scaling_max_freq \) \
  -print -exec cat {} \; 2>/dev/null
find /sys/class/powercap -maxdepth 3 -type f \
  \( -name name -o -name energy_uj -o -name max_energy_range_uj \
     -o -name 'constraint_*' \) \
  -print -exec cat {} \; 2>/dev/null
```

Then run:

```bash
sudo auto-cpufreq --debug
```

Capture the same sysfs values again.

Expected: before/after control values are identical. Energy counters may naturally advance; that is expected and is not a write.

- [ ] **Step 4: Validate Stats energy deltas on the Intel test system**

Run `sudo auto-cpufreq --stats` long enough for multiple refreshes under:

1. idle/light desktop activity;
2. a short deterministic CPU workload;
3. return to idle.

Expected qualitative behavior:

- package average power is non-negative;
- power generally rises under the deterministic workload and falls afterward;
- no requirement is imposed on exact wattage or C-state residency in Stage 1;
- missing subdomains/constraints are reported gracefully rather than treated as failure.

- [ ] **Step 5: Validate no-Intel/no-Powercap behavior**

Using a synthetic root or an available non-Intel/VM environment, confirm discovery returns structured missing/unavailable states and both `--debug` and ordinary reporting remain usable.

Stage 1 must not make Intel Powercap a startup requirement.

- [ ] **Step 6: Run final diff review for forbidden behavior**

Search the changed production code for writes and policy calls. Confirm there is no use of:

```text
write_text(
open(..., "w")
os.write(
set_turbo
set_governor
set_energy_perf
set_hwp_dynamic_boost
constraint_*_power_limit_uw writes
```

Read-only `os.read`/file reads are acceptable; any sysfs write is a Stage 1 blocker.

Also confirm:

- no new dependency in `pyproject.toml`;
- no GitHub Actions change;
- no committed temporary fixtures/tests;
- no fallback that invents package `0` or fixed RAPL constraint meanings;
- no final HWP policy eligibility decision is used to change behavior.

- [ ] **Step 7: Run final branch verification**

Confirm the branch is based on the intended development lineage and that all implementation commits are ahead of the design/spec commits without unrelated history changes.

Do not open an upstream PR yet. The Stage 1 implementation should first be reviewed as a complete read-only diff and tested on the Intel notebook.

---

## Implementation Commit Shape

Target a small reviewable sequence:

```text
feat: discover Intel power capabilities
feat: report Intel RAPL telemetry
feat: report Intel thermal throttle state
feat: expose Intel power diagnostics
feat: show Intel power telemetry in stats
```

Small corrective commits found during real-hardware validation may be folded into the relevant commit before presenting the Stage 1 branch for review. Do not create a catch-all refactor commit.

## Stage 1 Exit Criteria

Stage 1 is complete only when all of the following are true:

- Intel CPUFreq/HWP evidence is discoverable without model-number allowlists.
- CPUFreq policies are enumerated independently rather than treating CPU0 as the processor-wide truth.
- Powercap zones and constraints are discovered by ABI content and remain distinct across control trees.
- Energy telemetry handles the kernel-reported maximum range and one counter wrap using monotonic time.
- Package thermal counters are deduplicated by known physical package topology.
- `--debug` exposes a one-shot Intel snapshot without sleeping for a power sample.
- `--stats` can show differential average power after successive samples.
- normal daemon, Monitor, and Live paths do not gain Intel RAPL/thermal polling in this stage.
- systems without Intel Powercap/HWP degrade gracefully.
- no production sysfs write or policy behavior change is introduced.
- no new dependency, test framework, or CI workflow is introduced.
- real-hardware validation confirms diagnostic commands leave control values unchanged.
