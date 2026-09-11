# Modern Intel Optional RAPL Envelopes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, package-level Intel RAPL power envelopes for the Modern Intel HWP backend with conservative acquisition, verified sysfs writes, runtime ownership tracking, and failsafe restoration.

**Architecture:** Keep `intel_power.py` as discovery/telemetry and add a focused `intel_rapl.py` control module. RAPL is enabled only when the user opts in and a failsafe-capable daemon environment is present; the initial production integration is systemd-only because the repository's other init backends do not yet provide an equivalent post-crash restore hook. The controller never raises an externally observed limit on first acquisition, may change an already-owned value only up to its recorded original baseline, verifies every write by readback, and restores only values whose current value still equals `last_written_by_us`.

**Tech Stack:** Python 3.9+, stdlib `configparser`, `decimal`, `json`, `pathlib`, `tempfile`, Linux Powercap sysfs, systemd service directives. No new runtime dependency. Development verification uses temporary standalone Python harnesses rather than introducing an upstream test framework.

**Spec:** `docs/superpowers/specs/2026-09-10-modern-intel-power-policy-design.md`

## Global Constraints

- Active RAPL control is opt-in; existing installations must not receive package power-limit writes merely by upgrading.
- Use Linux Powercap sysfs only; never write MSRs directly.
- Control package zones only; do not change core, uncore, psys, PL3/PL4/peak, or time windows.
- Select constraints by ABI-provided names (`long_term`, `short_term`), never by numeric index alone. Unnamed constraints remain telemetry-only.
- Multiple control types (`intel-rapl`, `intel-rapl-mmio`) remain independent; never sum, merge, blindly synchronize, or raise one merely because another exposes a higher value.
- A first acquisition may lower an external limit but must never raise it.
- Once owned, a limit may move up or down only at or below the recorded `original_value`.
- Every write is followed by readback. A successful write syscall without readback does not establish ownership.
- Unexpected drift (`current != last_written_by_us`) invalidates ownership and must not trigger a write war.
- Kernel-reported min/max attributes are validation evidence, not permission to raise a limit. The Intel PL1 `constraint_X_max_power_uw` value is not treated as an unconditional hard ceiling when the currently accepted limit is already above it; conservative lowering still relies on write+readback.
- No automatic SKU/ARK-derived PL1/PL2 values and no automatic time-window tuning.
- Runtime ownership state lives under `/run/auto-cpufreq/`, is root-only, versioned, and atomically replaced.
- The restore helper must avoid importing `core.py` or starting config/notifier/service side effects.
- Stage 5 does not add suspend/resume D-Bus integration.
- LegacyPolicy behavior remains unchanged.
- By user preference, execution is inline in this conversation; do not dispatch subagents.

---

### Task 1: Define opt-in configuration and exact watt-to-microwatt parsing

**Files:**
- Create: `auto_cpufreq/modules/intel_rapl.py`
- Modify: `auto-cpufreq.conf-example`
- Development test: `tmp/test_intel_rapl_config.py` (temporary verification branch only)

**Interfaces:**
- Produces: `RaplEnvelopeTargets(long_term_uw: Optional[int], short_term_uw: Optional[int])`
- Produces: `RaplPolicyConfig(enabled: bool, targets: RaplEnvelopeTargets)`
- Produces: `RaplConfigError(ValueError)`
- Produces: `parse_rapl_policy_config(conf: ConfigParser, profile: str) -> RaplPolicyConfig`
- Configuration schema:

```ini
[intel_power]
enable_rapl_envelopes = true

[charger]
rapl_package_long_term_w = 15
rapl_package_short_term_w = 30

[battery]
rapl_package_long_term_w = 10
rapl_package_short_term_w = 20
```

The example values above are syntax examples only and must be commented out in the shipped example file so they are never active defaults.

- [ ] **Step 1: Write the failing config parser test**

```python
from configparser import ConfigParser
from auto_cpufreq.modules.intel_rapl import (
    RaplConfigError,
    parse_rapl_policy_config,
)

conf = ConfigParser()
conf.read_dict({
    "intel_power": {"enable_rapl_envelopes": "true"},
    "charger": {
        "rapl_package_long_term_w": "12.5",
        "rapl_package_short_term_w": "20.000001",
    },
})

parsed = parse_rapl_policy_config(conf, "charger")
assert parsed.enabled is True
assert parsed.targets.long_term_uw == 12_500_000
assert parsed.targets.short_term_uw == 20_000_001

for invalid in ("0", "-1", "nan", "inf", "12.0000001", "abc"):
    bad = ConfigParser()
    bad.read_dict({
        "intel_power": {"enable_rapl_envelopes": "true"},
        "charger": {"rapl_package_long_term_w": invalid},
    })
    try:
        parse_rapl_policy_config(bad, "charger")
    except RaplConfigError:
        pass
    else:
        raise AssertionError(invalid)
```

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=. python tmp/test_intel_rapl_config.py`

Expected: import failure because `auto_cpufreq.modules.intel_rapl` does not exist.

- [ ] **Step 3: Implement exact parsing with `Decimal`**

```python
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

MICROWATTS_PER_WATT = Decimal("1000000")

class RaplConfigError(ValueError):
    pass

@dataclass(frozen=True)
class RaplEnvelopeTargets:
    long_term_uw: Optional[int] = None
    short_term_uw: Optional[int] = None

@dataclass(frozen=True)
class RaplPolicyConfig:
    enabled: bool
    targets: RaplEnvelopeTargets


def _watts_to_microwatts(raw: str, option: str) -> int:
    try:
        watts = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise RaplConfigError(f"Invalid {option}: {raw}") from exc
    if not watts.is_finite() or watts <= 0:
        raise RaplConfigError(f"Invalid {option}: {raw}")
    microwatts = watts * MICROWATTS_PER_WATT
    if microwatts != microwatts.to_integral_value():
        raise RaplConfigError(f"{option} has precision below one microwatt: {raw}")
    return int(microwatts)
```

`parse_rapl_policy_config()` must default `enabled=False`; missing profile targets become `None`; values are parsed only when enabled so an old unused commented/example field cannot affect existing users.

- [ ] **Step 4: Run GREEN and compile**

Run:

```bash
PYTHONPATH=. python tmp/test_intel_rapl_config.py
python -m compileall -q auto_cpufreq/modules/intel_rapl.py
```

Expected: PASS, exit 0.

- [ ] **Step 5: Update `auto-cpufreq.conf-example`**

Add a commented `[intel_power]` block explaining that RAPL writes are disabled by default, plus commented per-profile `rapl_package_long_term_w` / `rapl_package_short_term_w` examples with a warning that values are platform-specific and must not be copied blindly.

- [ ] **Step 6: Commit**

```bash
git add auto_cpufreq/modules/intel_rapl.py auto-cpufreq.conf-example
git commit -m "feat: parse optional Intel RAPL envelopes"
```

---

### Task 2: Expose safe canonical Powercap zone references and discover named package constraints

**Files:**
- Modify: `auto_cpufreq/modules/intel_power.py`
- Modify: `auto_cpufreq/modules/intel_rapl.py`
- Development test: `tmp/test_intel_rapl_discovery.py`

**Interfaces:**
- Produces from `intel_power.py`: `IntelPowerDiscovery.powercap_zone_paths() -> tuple[tuple[Path, Optional[str]], ...]`, preserving the existing boundary checks and resolved-path deduplication currently implemented by `_powercap_zone_paths()`.
- Produces from `intel_rapl.py`:

```python
@dataclass(frozen=True)
class RaplConstraintRef:
    control_type: str
    zone_id: str
    zone_name: str
    constraint_index: int
    constraint_name: str
    power_limit_path: Path
    min_power_path: Path
    max_power_path: Path
```

- Produces: `IntelRaplController.discover_package_constraints() -> tuple[RaplConstraintRef, ...]`

- [ ] **Step 1: Write the failing synthetic discovery test**

Build a temporary Powercap tree containing canonical `intel-rapl` and `intel-rapl-mmio` trees, duplicate class aliases, one `package-0`, one `core` subzone, named `long_term`/`short_term`, one unnamed constraint, and an external symlink escape. Assert that discovery returns only four named package refs (two names × two canonical control types), does not return the alias twice, does not return `core`, does not return the unnamed constraint, and does not follow the escape.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=. python tmp/test_intel_rapl_discovery.py`

Expected: fail because the public canonical zone-path API / controller discovery does not exist.

- [ ] **Step 3: Promote the existing safe zone enumerator without changing Stage 1 behavior**

Rename `_powercap_zone_paths()` to `powercap_zone_paths()` and have `_powercap_zones()` call the public method. Do not alter its symlink-boundary or resolved-path dedup semantics.

- [ ] **Step 4: Implement package constraint discovery**

Only accept:

```python
SUPPORTED_CONTROL_TYPES = frozenset({"intel-rapl", "intel-rapl-mmio"})
SUPPORTED_PACKAGE_CONSTRAINTS = frozenset({"long_term", "short_term"})
```

A zone qualifies only when its `name` is readable and starts with `package-`. A constraint qualifies only when `_name` is readable and exactly matches a supported name. The index is retained solely to address the matching `constraint_X_power_limit_uw`; semantics come from the name.

- [ ] **Step 5: Run GREEN plus Stage 1 Powercap regression**

Run the new discovery harness and the existing Stage 1 synthetic Powercap harness used for `intel-power-discovery-telemetry`, confirming aliases, external symlink escapes, MSR/MMIO separation, unnamed constraint telemetry, and energy sampling remain unchanged.

- [ ] **Step 6: Commit**

```bash
git add auto_cpufreq/modules/intel_power.py auto_cpufreq/modules/intel_rapl.py
git commit -m "feat: discover controllable Intel RAPL constraints"
```

---

### Task 3: Add atomic root-only ownership persistence

**Files:**
- Modify: `auto_cpufreq/modules/intel_rapl.py`
- Development test: `tmp/test_intel_rapl_state.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class RaplConstraintIdentity:
    control_type: str
    zone_id: str
    zone_name: str
    constraint_index: int
    constraint_name: str

@dataclass(frozen=True)
class RaplOwnershipRecord:
    identity: RaplConstraintIdentity
    original_power_limit_uw: int
    last_written_power_limit_uw: int

class RaplStateStore:
    def __init__(self, path: Path = Path("/run/auto-cpufreq/power-state.json")) -> None: ...
    def load(self) -> tuple[RaplOwnershipRecord, ...]: ...
    def save(self, records: tuple[RaplOwnershipRecord, ...]) -> None: ...
    def clear(self) -> None: ...
```

State JSON version is exactly `1`.

- [ ] **Step 1: Write failing persistence tests**

Cover round-trip, malformed JSON, unsupported version, duplicate identities, file mode `0600`, parent mode `0700` when the module must create it outside systemd tests, atomic replacement, and clearing the last record.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=. python tmp/test_intel_rapl_state.py`

Expected: missing state classes/methods.

- [ ] **Step 3: Implement strict JSON serialization and atomic write**

Use a temporary file in the same directory, `os.fchmod(fd, 0o600)`, `flush()`, `os.fsync()`, and `os.replace()`. Reject malformed/unknown-version state without using it to write sysfs. A malformed state file must cause active acquisition to fail closed until the state is removed or repaired; never silently overwrite unknown ownership history.

- [ ] **Step 4: Run GREEN**

Run the persistence harness and compile the module.

- [ ] **Step 5: Commit**

```bash
git add auto_cpufreq/modules/intel_rapl.py
git commit -m "feat: persist Intel RAPL ownership state"
```

---

### Task 4: Implement conservative acquisition, verified writes, drift handling, and restoration

**Files:**
- Modify: `auto_cpufreq/modules/intel_rapl.py`
- Development test: `tmp/test_intel_rapl_control.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class RaplApplyResult:
    identity: RaplConstraintIdentity
    action: str
    before_uw: Optional[int]
    requested_uw: Optional[int]
    effective_uw: Optional[int]
    message: str

class IntelRaplController:
    def apply(self, targets: RaplEnvelopeTargets) -> tuple[RaplApplyResult, ...]: ...
    def restore_owned(self) -> tuple[RaplApplyResult, ...]: ...
```

- [ ] **Step 1: Write the control-state RED matrix**

Synthetic cases must include:

```text
unowned current=28W target=20W  -> lower, readback, own original=28/effective=20
unowned current=15W target=20W  -> skip; never raise external limit
unowned current=15W target=15W  -> no write and do not claim ownership
owned original=28 current=20 target=24 -> raise to 24 and remain owned
owned original=28 current=20 target=30 -> skip; never exceed original
owned original=28 last=20 current=18 -> drift; drop ownership, do not write
owned current=20 target omitted -> restore 28 only if current still equals last-written
write raises OSError -> no ownership
write succeeds but readback unchanged -> no ownership
write readback is quantized to 20.5W while original=28W -> own effective 20.5W
write readback unexpectedly exceeds original -> best-effort restore original, no ownership
intel-rapl current=28W + mmio current=15W + target=20W -> lower only intel-rapl; never raise mmio
```

Also cover missing/unreadable target files and unnamed constraints.

- [ ] **Step 2: Run RED**

Run: `PYTHONPATH=. python tmp/test_intel_rapl_control.py`

Expected: controller apply/restore behavior not implemented.

- [ ] **Step 3: Implement metadata validation conservatively**

Rules:

```python
# Positive min is a hard lower validation bound when readable.
if min_uw is not None and min_uw > 0 and target_uw < min_uw:
    reject

# A reported max is a hard upper bound only when the currently accepted value
# itself is not already above that metadata value.
if max_uw is not None and max_uw > 0 and current_uw <= max_uw < target_uw:
    reject
```

If `current_uw > max_uw`, do not infer permission to raise. The independent no-raise rule still requires `target_uw <= current_uw` for first acquisition. This preserves the real Samsung case where Intel RAPL PL1 reports current 28 W while `constraint_0_max_power_uw` reports 15 W.

- [ ] **Step 4: Implement acquisition and owned transitions**

Before any first write, persist the original value in memory, perform the write, read back, then atomically persist ownership only if verification establishes an effective value that does not exceed the external original baseline. For existing ownership, verify `current == last_written` before every change. If not, remove the ownership record without restoring or reasserting.

- [ ] **Step 5: Implement restore semantics**

For every owned record:

```text
current == last_written -> write original, read back, retire on verified restore
current != last_written -> retire without writing (ownership lost)
read/write error while still apparently owned -> keep record for another attempt in the same runtime
```

- [ ] **Step 6: Run GREEN and repeat RED/restore proof**

Run full control harness. Then temporarily alter one expected condition to prove the drift/no-raise test fails, restore it, and rerun GREEN.

- [ ] **Step 7: Commit**

```bash
git add auto_cpufreq/modules/intel_rapl.py
git commit -m "feat: control Intel RAPL envelopes safely"
```

---

### Task 5: Integrate RAPL only into the Modern Intel daemon policy path

**Files:**
- Modify: `auto_cpufreq/modules/policy.py`
- Modify: `auto_cpufreq/core.py`
- Modify: `auto_cpufreq/bin/auto_cpufreq.py`
- Development test: `tmp/test_intel_rapl_policy_integration.py`

**Interfaces:**
- Extend `ModernIntelActions` with optional `apply_power_envelope: Optional[Callable[[PowerSource], None]] = None`.
- Add `LegacyPolicy.apply_power_envelope(source)` as a no-op.
- Add `ModernIntelHwpPolicy.apply_power_envelope(source)` which invokes the optional modern envelope callback.
- Add `core.apply_modern_intel_rapl(source: PowerSource)`.
- `_daemon_policy_cycle()` invokes `backend.apply_power_envelope(source)` after normal HWP policy application.

- [ ] **Step 1: Write RED integration tests**

Prove:

1. Legacy daemon cycles never call RAPL.
2. Modern `monitor` never writes RAPL.
3. Existing `--live` loop never writes RAPL in the initial release.
4. Modern daemon cycles call RAPL after applying HWP policy.
5. `enable_rapl_envelopes=false` restores any currently owned records and performs no acquisition.
6. Enabled with no target for the active profile restores currently owned records instead of leaving an old profile cap behind.
7. Invalid RAPL config reports the error and performs no RAPL sysfs writes; the daemon remains alive.

- [ ] **Step 2: Run RED**

Expected: missing backend envelope method/integration.

- [ ] **Step 3: Add the daemon-only failsafe gate**

`core.apply_modern_intel_rapl()` must require both:

```text
[intel_power] enable_rapl_envelopes = true
AUTO_CPUFREQ_RAPL_FAILSAFE = 1
```

If the user opts in but the internal failsafe environment marker is absent, print a clear warning and do not write RAPL. This deliberately keeps the first production RAPL implementation disabled under OpenRC/dinit/runit/s6 and ad-hoc `--live`, where this stage does not yet provide an equivalent crash-recovery hook.

- [ ] **Step 4: Integrate config changes and power transitions**

Because Stage 4 already reapplies the daemon cycle on authoritative power-source change and on config wake, the RAPL callback receives the fresh profile each time. No new polling or event source is added.

- [ ] **Step 5: Run GREEN plus Stage 3/4 regressions**

Run the new integration harness plus the existing Modern/Legacy policy and event-loop harnesses. Verify no `get_load()` call appears in Modern policy and Legacy periodic behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add auto_cpufreq/modules/policy.py auto_cpufreq/core.py auto_cpufreq/bin/auto_cpufreq.py
git commit -m "feat: apply RAPL envelopes in Modern Intel daemon"
```

---

### Task 6: Add minimal restore helper and systemd failsafe lifecycle

**Files:**
- Create: `auto_cpufreq/bin/auto_cpufreq_restore_rapl.py`
- Modify: `pyproject.toml`
- Modify: `scripts/auto-cpufreq.service`
- Development test: `tmp/test_intel_rapl_restore_helper.py`

**Interfaces:**
- New console script: `auto-cpufreq-restore-rapl = "auto_cpufreq.bin.auto_cpufreq_restore_rapl:main"`
- Systemd unit additions:

```ini
RuntimeDirectory=auto-cpufreq
RuntimeDirectoryMode=0700
Environment=AUTO_CPUFREQ_RAPL_FAILSAFE=1
ExecStopPost=/opt/auto-cpufreq/venv/bin/auto-cpufreq-restore-rapl
```

The existing interpreter/runtime remains `/opt/auto-cpufreq/venv`; do not call `/usr/bin/python3`.

- [ ] **Step 1: Write RED helper tests**

The helper must import only the focused RAPL module and stdlib, restore when current equals last-written, skip without writing on drift, keep a still-owned failed record long enough to report failure, and return non-zero on genuine restore I/O failure. It must not import `auto_cpufreq.core`, start config watching, or run service-detection logic.

- [ ] **Step 2: Run RED**

Expected: helper/entry point absent.

- [ ] **Step 3: Implement the minimal helper**

```python
from auto_cpufreq.modules.intel_rapl import IntelRaplController


def main() -> int:
    results = IntelRaplController().restore_owned()
    failed = [result for result in results if result.action == "restore-failed"]
    return 1 if failed else 0
```

Use `raise SystemExit(main())` under `__main__`.

- [ ] **Step 4: Update systemd service and package entry point**

Keep `Restart=on-failure` and `WatchdogSec=30s`. Add only the runtime directory, internal failsafe marker, and ExecStopPost helper. Do not change non-systemd service definitions in this first implementation; their lack of the marker keeps RAPL writes disabled.

- [ ] **Step 5: Run GREEN and packaging/build checks**

Run helper harness, `python -m compileall`, `python -m pip install .` in an isolated environment, Linux Build, and Nix Flake. Confirm the installed venv exposes `auto-cpufreq-restore-rapl`.

- [ ] **Step 6: Commit**

```bash
git add auto_cpufreq/bin/auto_cpufreq_restore_rapl.py pyproject.toml scripts/auto-cpufreq.service
git commit -m "feat: restore owned RAPL state on daemon stop"
```

---

### Task 7: Final safety audit and real-hardware validation gates

**Files:**
- Modify only if a verified defect is found.
- Development verification: temporary workflow/harness files only; do not promote them to the production branch.

**Interfaces:** None new.

- [ ] **Step 1: Static no-write audit with opt-in disabled**

Verify that default configuration and missing `[intel_power]` section make the RAPL controller perform zero sysfs writes. Search production code for `constraint_*_power_limit_uw` write sites and confirm all are centralized in `intel_rapl.py`.

- [ ] **Step 2: Full synthetic matrix**

Run config, discovery, persistence, control, policy integration, restore helper, Stage 1 Powercap regression, Stage 3 Modern policy regression, and Stage 4 event-loop regression in one temporary workflow. Expected: all exit 0.

- [ ] **Step 3: CI on the real production HEAD**

Require Linux Build and Nix Flake success on the exact production HEAD. Do not infer production success from a temporary harness branch with different blobs.

- [ ] **Step 4: Read-only Samsung preflight**

With opt-in absent/false, run the production branch on the Galaxy Book and prove the existing Stage 4 behavior is unchanged. Capture current canonical `intel-rapl` and `intel-rapl-mmio` package `long_term`/`short_term` values and verify no ownership state file is created.

- [ ] **Step 5: Explicitly authorize the first hardware write test**

Do not choose or write a RAPL target automatically. Before the first real sysfs write, present the current package constraints and require an explicit user-selected temporary lower target. The test target must not exceed any current external limit being acquired. This is a safety gate, not an unresolved implementation placeholder.

- [ ] **Step 6: Real hardware apply/readback/restore**

For the explicitly selected target: snapshot both `intel-rapl` and `intel-rapl-mmio`; apply the envelope; verify exact/effective readback and ownership JSON; transition AC↔battery if configured; stop the foreground/systemd test; verify ExecStopPost restores only still-owned values and removes/retire state. Confirm an externally modified value is not overwritten by the helper.

- [ ] **Step 7: Final diff audit**

Compare Stage 4 HEAD `4a4708f24894c746a565b86fed98a495d31b82db` to the Stage 5 HEAD. Confirm no direct MSR access, no time-window writes, no PL3/PL4 writes, no CPU-model allowlist, no new runtime dependency, no RAPL writes in Legacy/monitor/live, and no production test harness files.

- [ ] **Step 8: Completion commit only if documentation needs final correction**

If no production correction is needed, do not create an empty commit. If the shipped config/docs require a verified wording correction, commit only that change with a focused message.

---

## Self-Review Against the Spec

- Opt-in default: covered Tasks 1 and 5.
- User-provided package long/short-term targets: Task 1.
- Named constraints only: Task 2.
- No automatic time-window tuning: global constraint + Tasks 2/7.
- Firmware/write failures and readback: Task 4.
- Original/last-written ownership model: Tasks 3/4.
- No write war after drift: Task 4.
- Atomic `/run/auto-cpufreq` state: Task 3.
- Minimal same-runtime restore helper: Task 6.
- Failsafe service integration: Task 6.
- No automatic raising after external drift: Task 4.
- Legacy unchanged and no new polling: Tasks 5/7.
- Real hardware validation only after explicit write approval: Task 7.

No Stage 5 requirement is intentionally deferred except suspend/resume D-Bus integration, which the approved design explicitly defers.