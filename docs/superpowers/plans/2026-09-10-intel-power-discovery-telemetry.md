# Intel Power Discovery and Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only Intel power discovery and telemetry layer that reports modern `intel_pstate`/HWP evidence, CPUFreq policy, documented CPU topology, turbo permission, Powercap/RAPL, energy, and thermal-throttle state without changing hardware policy.

**Architecture:** Create `auto_cpufreq/modules/intel_power.py` as a dependency-light, read-only sysfs boundary. Existing reporting code consumes a normalized Intel snapshot only when `--debug` or `--stats` explicitly requests it; ordinary daemon, Monitor, and Live paths keep their current collection cost and behavior. Differential energy sampling is stateful inside the Intel module so repeated Stats refreshes can calculate average package/subdomain power without making one-shot debug calls block.

**Tech Stack:** Python standard library (`dataclasses`, `enum`, `pathlib`, `time`, `typing`), documented Linux CPU topology/CPUFreq sysfs, Linux Powercap/RAPL ABI, existing `SystemInfo`, `PlatformProfile`, and `SystemMonitor` reporting infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-10-modern-intel-power-policy-design.md`

## Global Constraints

- Stage 1 is 100% read-only.
- No new runtime dependency, pytest suite, CI job, service change, config option, or active power-policy change.
- Temporary standard-library fixtures/scripts may be used locally but must not remain in the final diff.
- Discover CPUFreq policies from policy directories and Powercap meanings from ABI contents such as `name` and `constraint_X_name`; never infer fixed meanings from numeric suffixes.
- Use only documented kernel sysfs topology attributes in this stage. Do not infer P-core/E-core labels from CPU model numbers or undocumented files. If a stable userspace ABI does not expose core type, report that classification as unavailable rather than guessing.
- Existing Platform Profile discovery/reporting remains owned by `auto_cpufreq.modules.platform_profile`; do not duplicate it in `intel_power.py`.
- Missing, unreadable, and malformed values remain distinguishable. Unknown topology or package identity must never become fabricated CPU/package `0`.
- Energy sampling uses `time.monotonic_ns()` and the zone's actual `max_energy_range_uj`.
- Differential power supports at most one energy-counter wrap between consecutive samples.
- Package thermal counters are reported once per known physical package.
- Intel telemetry is collected only when requested by `--debug` or `--stats` in this stage.
- Existing legacy behavior remains unchanged.

---

## File Map

**Create**

- `auto_cpufreq/modules/intel_power.py` — safe sysfs reads, CPUFreq/Intel discovery, documented topology, turbo permission, Powercap discovery, energy sampling, thermal-throttle discovery, normalized dataclasses.

**Modify**

- `auto_cpufreq/modules/system_info.py` — optional Intel snapshot collection and text formatting.
- `auto_cpufreq/modules/system_monitor.py` — Stats-only Intel telemetry collection/rendering.
- `auto_cpufreq/bin/auto_cpufreq.py` — `--debug` requests the enhanced read-only report.

**Do not modify in Stage 1**

- `auto_cpufreq/core.py`
- `scripts/cpufreqctl.sh`
- `scripts/auto-cpufreq.service`
- `auto-cpufreq.conf-example`
- `pyproject.toml`
- `.github/workflows/*`

If implementation appears to require any file in the do-not-modify set, stop and reassess the Stage 1 boundary.

---

### Task 1: Safe Sysfs Reads, Documented Topology, and CPUFreq/Intel Discovery

**Files:**
- Create: `auto_cpufreq/modules/intel_power.py`
- Temporary verification: standard-library script only; do not commit it

**Interfaces:**
- Produces: `ReadStatus`, `ReadResult`, `CpuTopologySnapshot`, `CpuFreqPolicySnapshot`, `IntelPowerSnapshot`, `IntelPowerDiscovery.snapshot()`.
- Consumes: standard-library filesystem APIs only.

- [ ] **Step 1: Write the temporary failing synthetic-sysfs check**

Create a temporary script outside the final diff. It must create a `tempfile.TemporaryDirectory()` with this CPU tree:

```text
intel_pstate/status -> active
intel_pstate/no_turbo -> 0
intel_pstate/hwp_dynamic_boost -> 1
cpu0/topology/physical_package_id -> 0
cpu0/topology/core_id -> 0
cpu0/topology/thread_siblings_list -> 0,4
cpu1/topology/physical_package_id -> 0
cpu1/topology/core_id -> 1
cpu1/topology/thread_siblings_list -> 1
cpufreq/policy0/scaling_driver -> intel_pstate
cpufreq/policy0/scaling_governor -> powersave
cpufreq/policy0/related_cpus -> 0 2
cpufreq/policy0/cpuinfo_min_freq -> 400000
cpufreq/policy0/cpuinfo_max_freq -> 4500000
cpufreq/policy0/scaling_min_freq -> 400000
cpufreq/policy0/scaling_max_freq -> 4500000
cpufreq/policy0/energy_performance_preference -> balance_performance
cpufreq/policy0/energy_performance_available_preferences -> default performance balance_performance balance_power power
cpufreq/policy1/related_cpus -> 1 3
```

Import `IntelPowerDiscovery`, call `snapshot()`, and assert that two policies are returned, status is `active`, turbo is reported as allowed, Dynamic Boost is `True`, CPU0/CPU1 topology remains distinct, and the two policy related-CPU sets remain distinct. The first run must fail because the module does not yet exist.

- [ ] **Step 2: Add the read-state types and exact safe-read helpers**

Implement:

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


def _read_text(path: Path) -> ReadResult[str]:
    try:
        return ReadResult(ReadStatus.AVAILABLE, path.read_text().strip())
    except FileNotFoundError:
        return ReadResult(ReadStatus.MISSING)
    except OSError:
        return ReadResult(ReadStatus.UNREADABLE)


def _read_int(path: Path) -> ReadResult[int]:
    result = _read_text(path)
    if result.status is not ReadStatus.AVAILABLE:
        return ReadResult(result.status)
    try:
        return ReadResult(ReadStatus.AVAILABLE, int(result.value))
    except (TypeError, ValueError):
        return ReadResult(ReadStatus.INVALID)


def _parse_cpu_list(value: str) -> tuple[int, ...]:
    cpus: list[int] = []
    for token in value.replace(",", " ").split():
        if "-" not in token:
            cpus.append(int(token))
            continue
        start_text, end_text = token.split("-", 1)
        start = int(start_text)
        end = int(end_text)
        if end < start:
            raise ValueError("invalid CPU range")
        cpus.extend(range(start, end + 1))
    return tuple(cpus)
```

Add a helper that wraps `_parse_cpu_list()` and returns `ReadStatus.INVALID` instead of propagating malformed kernel/userspace content.

- [ ] **Step 3: Add normalized topology and CPUFreq types**

Implement:

```python
@dataclass(frozen=True)
class CpuTopologySnapshot:
    cpu: int
    physical_package_id: ReadResult[int]
    core_id: ReadResult[int]
    thread_siblings: ReadResult[tuple[int, ...]]


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
    turbo_allowed: ReadResult[bool]
    hwp_dynamic_boost: ReadResult[bool]
    cpu_topology: tuple[CpuTopologySnapshot, ...]
    cpufreq_policies: tuple[CpuFreqPolicySnapshot, ...]
    powercap_zones: tuple = ()
    thermal_packages: tuple = ()
```

Stage 1 deliberately does not expose a behavior-changing `modern_hwp_eligible` decision and does not classify a CPU as P-core/E-core from model knowledge. It reports documented topology plus HWP-related evidence; policy selection belongs to Stage 2.

- [ ] **Step 4: Implement topology, turbo, and CPUFreq discovery**

`IntelPowerDiscovery.__init__` must accept:

```python
cpu_root: Path = Path("/sys/devices/system/cpu")
powercap_root: Path = Path("/sys/class/powercap")
```

`IntelPowerDiscovery.snapshot(sample_energy: bool = False)` must:

1. enumerate numeric `cpu[0-9]*` directories and read only documented `topology/physical_package_id`, `topology/core_id`, and `topology/thread_siblings_list`;
2. enumerate `<cpu_root>/cpufreq/policy*`, sorting numeric policy suffixes numerically;
3. read each CPUFreq field defined above;
4. read `intel_pstate/status` without rewriting unknown strings;
5. read `intel_pstate/no_turbo` and expose **turbo permission** with inverse semantics;
6. read Dynamic Boost separately.

Turbo conversion is exact:

```text
no_turbo "0" -> turbo_allowed available True
no_turbo "1" -> turbo_allowed available False
missing no_turbo -> MISSING
other contents -> INVALID
read error -> UNREADABLE
```

Dynamic Boost conversion is exact:

```text
"0" -> available False
"1" -> available True
missing path -> MISSING
other contents -> INVALID
read error -> UNREADABLE
```

Do not derive processor-wide state from `cpu0/cpufreq` symlinks.

- [ ] **Step 5: Extend the temporary fixture with negative cases**

Assert all of the following without uncaught exceptions:

- no `intel_pstate` directory;
- no CPUFreq policy directory;
- malformed `related_cpus` such as `4-2`;
- malformed `thread_siblings_list`;
- missing EPP file;
- missing topology attributes;
- unreadable attribute simulated by using a directory where a text attribute is expected;
- unknown P/E classification is not fabricated.

Expected results must use the matching `ReadStatus`; they must not silently become zero, CPU0, package0, or a valid-looking core type.

- [ ] **Step 6: Compile and commit**

Run:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
python -c 'from auto_cpufreq.modules.intel_power import IntelPowerDiscovery; print(IntelPowerDiscovery.__name__)'
```

Delete the temporary verification script and commit only the production module:

```bash
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: discover Intel power capabilities"
```

---

### Task 2: Generic Powercap Discovery and Wrap-Safe Energy Sampling

**Files:**
- Modify: `auto_cpufreq/modules/intel_power.py`
- Temporary verification only; no committed test file

**Interfaces:**
- Produces: `PowercapConstraintSnapshot`, `PowercapZoneSnapshot`, `PowerSample`, `EnergySampler`.
- Extends: `IntelPowerSnapshot.powercap_zones`.

- [ ] **Step 1: Build the failing synthetic Powercap fixture**

Create a temporary root containing three distinct zone entries:

```text
intel-rapl:0/name -> package-0
intel-rapl:0/energy_uj -> 90000000
intel-rapl:0/max_energy_range_uj -> 100000000
intel-rapl:0/constraint_0_name -> long_term
intel-rapl:0/constraint_0_power_limit_uw -> 15000000
intel-rapl:0/constraint_0_time_window_us -> 28000000
intel-rapl:0/constraint_1_name -> short_term
intel-rapl:0/constraint_1_power_limit_uw -> 35000000
intel-rapl:0:0/name -> core
intel-rapl:0:0/energy_uj -> 45000000
intel-rapl:0:0/max_energy_range_uj -> 100000000
intel-rapl-mmio:0/name -> package-0
intel-rapl-mmio:0/constraint_0_name -> long_term
intel-rapl-mmio:0/constraint_0_power_limit_uw -> 12000000
```

Require three independent zones. In particular, the two human-readable `package-0` zones must never be merged by name.

- [ ] **Step 2: Add the Powercap dataclasses**

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

Update `IntelPowerSnapshot.powercap_zones` to `tuple[PowercapZoneSnapshot, ...]` after this type exists.

- [ ] **Step 3: Implement Powercap traversal without relying on symlink accidents**

Use an explicit directory queue starting at `powercap_root`. For every directory entry that `is_dir()` reports as a directory, inspect it as a possible zone and enqueue its child directories. Track each resolved directory path in a `seen_dirs` set so a sysfs class symlink and a nested path cannot cause duplicate or cyclic traversal.

A directory is a zone candidate when it exposes at least one zone ABI indicator: `name`, `energy_uj`, `max_energy_range_uj`, or a `constraint_*_name` file. Record `zone_id` as the lexical path relative to `powercap_root`; keep the kernel-visible entry name in `sysfs_name`.

Constraint enumeration must scan actual filenames matching `constraint_*_name`, parse the numeric `X`, sort indexes numerically, and read only matching `constraint_X_*` files. Never map index `0`/`1` to PL1/PL2 unless the corresponding name file says `long_term`/`short_term`.

Do not use `os.access(..., os.W_OK)` as evidence of future writability; Stage 1 only observes.

- [ ] **Step 4: Implement deterministic stateful energy sampling**

Implement:

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
    ) -> Optional[PowerSample]:
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

Document on this method that it assumes no more than one energy-counter wrap between consecutive samples.

- [ ] **Step 5: Verify normal, wrapped, invalid, and independent-zone sampling**

Use explicit timestamps:

```python
sampler = EnergySampler()
assert sampler.sample("pkg-msr", 90_000_000, 100_000_000, now_ns=0) is None
sample = sampler.sample("pkg-msr", 10_000_000, 100_000_000, now_ns=1_000_000_000)
assert sample is not None
assert sample.delta_energy_uj == 20_000_000
assert sample.elapsed_ns == 1_000_000_000
assert sample.average_power_w == 20.0
```

Also assert that a backwards counter with no usable max range returns `None`, non-positive elapsed time returns `None`, and a second zone with the same human-readable name has independent sampler history because its `zone_id` differs.

- [ ] **Step 6: Wire sampling into snapshots only when requested**

`IntelPowerDiscovery` owns one `EnergySampler`. `snapshot(sample_energy=True)` samples zones with valid energy counters. `snapshot(sample_energy=False)` reads static capability/energy values but does not advance sampler baselines.

- [ ] **Step 7: Compile and commit**

Run the synthetic fixture plus:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
```

Delete temporary fixture files and commit:

```bash
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: report Intel RAPL telemetry"
```

---

### Task 3: Package-Deduplicated Thermal Throttle Telemetry

**Files:**
- Modify: `auto_cpufreq/modules/intel_power.py`

**Interfaces:**
- Produces: `ThermalThrottleSnapshot`.
- Extends: `IntelPowerSnapshot.thermal_packages`.
- Reuses: `CpuTopologySnapshot.physical_package_id` rather than performing a contradictory topology fallback.

- [ ] **Step 1: Build a failing synthetic two-package topology**

Create duplicate package data for CPU0/CPU1 and a second package on CPU2:

```text
cpu0/topology/physical_package_id -> 0
cpu0/thermal_throttle/package_throttle_count -> 12
cpu0/thermal_throttle/package_throttle_total_time_ms -> 300
cpu0/thermal_throttle/package_throttle_max_time_ms -> 40
cpu1/topology/physical_package_id -> 0
cpu1/thermal_throttle/package_throttle_count -> 12
cpu2/topology/physical_package_id -> 1
cpu2/thermal_throttle/package_throttle_count -> 4
```

The expected result is two package records, not a sum over logical CPUs.

- [ ] **Step 2: Add the thermal snapshot type**

```python
@dataclass(frozen=True)
class ThermalThrottleSnapshot:
    package_id: int
    representative_cpu: int
    throttle_count: ReadResult[int]
    total_time_ms: ReadResult[int]
    max_time_ms: ReadResult[int]
```

Update `IntelPowerSnapshot.thermal_packages` to `tuple[ThermalThrottleSnapshot, ...]`.

- [ ] **Step 3: Implement known-topology-only deduplication**

Group only CPUs whose `physical_package_id` is `AVAILABLE` and contains an integer. For each known package, choose the lowest-numbered CPU that actually exposes package thermal-throttle attributes and read from that CPU only.

Do not fabricate package 0 when topology is missing. If a package has no representative CPU exposing the thermal ABI, omit it. Missing optional total/max-time files remain `MISSING`; they never become zero.

- [ ] **Step 4: Verify edge cases and commit**

Verify duplicate CPUs, two packages, missing topology, missing thermal ABI, and missing optional time metrics. Then run:

```bash
python -m compileall -q auto_cpufreq/modules/intel_power.py
git add auto_cpufreq/modules/intel_power.py
git commit -m "feat: report Intel thermal throttle state"
```

Delete the temporary fixture before committing.

---

### Task 4: Opt-In `SystemReport` Integration and `--debug`

**Files:**
- Modify: `auto_cpufreq/modules/system_info.py`
- Modify: `auto_cpufreq/bin/auto_cpufreq.py`

**Interfaces:**
- Consumes: `IntelPowerSnapshot`, module singleton `intel_power = IntelPowerDiscovery()`.
- Produces: optional `SystemReport.intel_power` and `format_intel_power_summary()`.
- Reuses: existing `platform_profile` reporting instead of duplicating platform-profile discovery.

- [ ] **Step 1: Write a failing opt-in collection check**

With `unittest.mock.patch`, assert that ordinary `system_info.generate_system_report()` does not call `intel_power.snapshot`, while `generate_system_report(include_intel_power=True)` calls it exactly once.

- [ ] **Step 2: Add optional Intel collection without changing default report cost**

In `intel_power.py`, add:

```python
intel_power = IntelPowerDiscovery()
```

In `system_info.py`, import `IntelPowerSnapshot` and `intel_power`, append this defaulted field to `SystemReport`:

```python
intel_power: IntelPowerSnapshot | None = None
```

Change the report generator signature to:

```python
def generate_system_report(
    self,
    include_intel_power: bool = False,
    sample_intel_energy: bool = False,
) -> SystemReport:
```

When `include_intel_power` is false, do not call the Intel module. When true, set the field from `intel_power.snapshot(sample_energy=sample_intel_energy)`.

- [ ] **Step 3: Add one shared formatter with explicit missing/unknown semantics**

Implement `format_intel_power_summary(snapshot: IntelPowerSnapshot) -> list[str]` in `system_info.py`. It must format:

- `intel_pstate` status, turbo permission, and Dynamic Boost state;
- documented CPU topology summary and CPUFreq policies;
- each distinct Powercap zone, raw energy, optional sampled average power, and named constraints;
- package thermal metrics.

If no stable kernel ABI identifies a P/E core type, print no fabricated P/E labels; policy membership and documented package/core/thread topology are sufficient Stage 1 evidence.

Use these display rules:

```text
optional ABI missing -> Unavailable
read/parse failure -> Unknown or Could not be read
first energy sample -> Awaiting next sample
µJ -> J for display
µW -> W for display
µs -> seconds for display
```

Do not claim a backend is MSR/MMIO/TPMI unless that identity is directly reflected in the observed kernel-visible sysfs identifier.

- [ ] **Step 4: Make `--debug` explicitly request the enhanced report**

Extend the existing import from `auto_cpufreq.modules.system_info` so it imports `system_info` in addition to the existing formatting/printing functions. In the `debug` branch, use:

```python
report = system_info.generate_system_report(
    include_intel_power=True,
    sample_intel_energy=False,
)
print_system_report(report)
```

Do not sleep to manufacture an average-power value. Do not modify daemon, Live, or Monitor branches.

- [ ] **Step 5: Append diagnostics only when the optional snapshot exists**

`format_system_report()` must preserve existing output when `report.intel_power is None`. When the field exists, append a separated Intel Power Diagnostics section using `format_intel_power_summary()`.

- [ ] **Step 6: Verify and commit**

Run the opt-in mock check plus:

```bash
python -m compileall -q auto_cpufreq/modules/system_info.py auto_cpufreq/bin/auto_cpufreq.py
```

If Intel and no-Powercap environments are available, run `sudo auto-cpufreq --debug` in both. Missing optional interfaces must not raise.

Commit:

```bash
git add auto_cpufreq/modules/system_info.py auto_cpufreq/bin/auto_cpufreq.py
git commit -m "feat: expose Intel power diagnostics"
```

---

### Task 5: Stats-Only Differential Power Reporting

**Files:**
- Modify: `auto_cpufreq/modules/system_monitor.py`

**Interfaces:**
- Consumes: optional `SystemReport.intel_power`, persistent module-level Intel sampler, `format_intel_power_summary()`.
- Produces: Intel Power Diagnostics in Stats only.

- [ ] **Step 1: Write the failing view-specific collection assertion**

Patch `system_info.generate_system_report` and verify the intended calls:

```text
STATS -> include_intel_power=True, sample_intel_energy=True
MONITOR -> include_intel_power=False, sample_intel_energy=False
LIVE -> include_intel_power=False, sample_intel_energy=False
```

- [ ] **Step 2: Make `_collect_report()` select telemetry by view type**

Implement:

```python
include_intel_power = self.type == ViewType.STATS
report = system_info.generate_system_report(
    include_intel_power=include_intel_power,
    sample_intel_energy=include_intel_power,
)
```

Keep the current two-second UI refresh cadence. Do not add Intel Powercap/thermal collection to Monitor or Live.

- [ ] **Step 3: Reuse the shared formatter in Stats**

Import `format_intel_power_summary` and, only when `report.intel_power` is not `None`, append an `Intel Power Diagnostics` section to the right column using the returned lines.

The first Stats refresh may say `Awaiting next sample`; later refreshes should show average watts where valid energy counters exist.

- [ ] **Step 4: Verify sampler continuity**

With a deterministic synthetic tree, call two Stats collections through the same module-level `intel_power` instance and confirm the second snapshot has `PowerSample` data. Call Monitor/Live collection between them and confirm those modes do not advance Intel energy baselines.

- [ ] **Step 5: Compile, smoke-test, and commit**

Run:

```bash
python -m compileall -q auto_cpufreq/modules/system_monitor.py
```

On the Intel notebook, run `sudo auto-cpufreq --stats` for at least two refreshes. Confirm no traceback, non-negative sampled power, distinct Powercap zones, and no hardware-setting change on exit.

Commit:

```bash
git add auto_cpufreq/modules/system_monitor.py
git commit -m "feat: show Intel power telemetry in stats"
```

---

### Task 6: Stage 1 Real-Hardware and Scope Validation

**Files:**
- No new production files unless validation exposes a Stage 1 defect.

**Interfaces:**
- Validates the complete Stage 1 contract.

- [ ] **Step 1: Confirm final production-file scope**

Expected implementation diff:

```text
auto_cpufreq/modules/intel_power.py
auto_cpufreq/modules/system_info.py
auto_cpufreq/modules/system_monitor.py
auto_cpufreq/bin/auto_cpufreq.py
```

Any additional production file requires explicit review before proceeding.

- [ ] **Step 2: Run repository-level syntax/build checks**

Run:

```bash
python -m compileall -q auto_cpufreq
```

In a disposable installation/worktree, exercise the same Linux build path the upstream workflow currently uses:

```bash
sudo ./auto-cpufreq-installer --install
```

No new dependency or test framework should be required.

- [ ] **Step 3: Capture before/after control state on the Intel test notebook**

Before `--debug`, capture:

```bash
cat /sys/devices/system/cpu/intel_pstate/status 2>/dev/null || true
cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || true
cat /sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost 2>/dev/null || true
find /sys/devices/system/cpu/cpufreq -maxdepth 2 -type f \
  \( -name scaling_governor -o -name energy_performance_preference \
     -o -name scaling_min_freq -o -name scaling_max_freq \) \
  -print -exec cat {} \; 2>/dev/null
find -L /sys/class/powercap -maxdepth 3 -type f \
  \( -name name -o -name energy_uj -o -name max_energy_range_uj \
     -o -name 'constraint_*' \) \
  -print -exec cat {} \; 2>/dev/null
```

Run:

```bash
sudo auto-cpufreq --debug
```

Capture the same values afterward. Control values must match. Energy counters are expected to advance naturally.

- [ ] **Step 4: Validate live energy deltas qualitatively**

Run `sudo auto-cpufreq --stats` through idle/light activity, a deterministic short CPU workload, and return to idle. Package/subdomain sampled power must remain non-negative and should respond plausibly to load; Stage 1 sets no required wattage or C-state target.

- [ ] **Step 5: Validate graceful no-Intel/no-Powercap behavior**

Use a synthetic root or available VM/non-Intel host. Discovery must return structured missing/unavailable states rather than becoming a startup requirement. Existing ordinary reporting must still work because Intel collection is opt-in.

- [ ] **Step 6: Audit for forbidden writes and scope creep**

Review the changed production code and reject Stage 1 if it contains any sysfs write or calls active policy setters. Specifically verify there is no production use of write-mode file opens, `Path.write_text`, `set_turbo`, governor/EPP setters, HWP Dynamic Boost setters, or Powercap constraint writes.

Also confirm:

- `pyproject.toml` unchanged;
- workflows unchanged;
- no committed fixture/test file;
- no fabricated package 0;
- no fabricated P/E core classification;
- no numeric RAPL index interpreted as a fixed semantic name;
- no policy eligibility decision used to alter behavior.

- [ ] **Step 7: Review the Stage 1 diff before any upstream PR**

The branch must contain only the read-only Stage 1 implementation plus its existing design/spec history. Do not open an upstream PR until the complete diff and notebook output have been reviewed.

---

## Target Commit Shape

```text
feat: discover Intel power capabilities
feat: report Intel RAPL telemetry
feat: report Intel thermal throttle state
feat: expose Intel power diagnostics
feat: show Intel power telemetry in stats
```

Small fixes found during notebook validation should be folded into the commit that introduced the affected behavior before presenting the Stage 1 branch for review. Do not create a catch-all refactor commit.

## Stage 1 Exit Criteria

- Intel P-state/HWP evidence, turbo permission, and documented topology are discoverable without CPU-model allowlists.
- CPUFreq policies are enumerated independently rather than using CPU0 as processor-wide truth.
- Existing Platform Profile reporting remains the single source for platform-profile information.
- Powercap zones remain distinct and constraints are interpreted by ABI names.
- Average power uses monotonic elapsed time, kernel-reported energy range, and one-wrap handling.
- Package thermal counters are deduplicated only when physical package topology is known.
- `--debug` exposes a one-shot Intel snapshot without sleeping for a second energy sample.
- `--stats` can expose differential average power on successive refreshes.
- daemon, Monitor, and Live paths do not gain new Intel RAPL/thermal polling.
- systems without Intel HWP/Powercap degrade gracefully.
- no production sysfs write or active policy behavior change exists.
- no new dependency, test framework, or CI workflow exists.
- real-hardware validation confirms diagnostic commands leave control values unchanged.
