# Debug Observability Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `auto-cpufreq --debug` a structured, one-shot, read-only diagnostic path on top of the current PR #975 code while preserving useful support information.

**Architecture:** Keep `SystemReport` unchanged as the fast shared telemetry snapshot. Add a dependency-light `auto_cpufreq.modules.diagnostics` module containing immutable diagnostic value objects, read-only collectors, one `DiagnosticsReport` aggregate, and formatting that consumes the already-collected `SystemReport`; then make only the CLI `--debug` branch use that boundary. The Stage 1 implementation must not call lifecycle mutators, deploy `cpufreqctl`, or invoke legacy print-oriented battery diagnostics.

**Tech Stack:** Python 3.12, stdlib `dataclasses`/`pathlib`, existing `SystemReport`, existing read-only `power_state.capture_service_state()`, Click CLI, temporary stdlib `unittest` validation, GitHub Actions Linux/Nix workflows.

**Spec:** `docs/superpowers/specs/2026-09-12-debug-observability-architecture-design.md`

## Global Constraints

- Base implementation on `installer-resilience-final` at `84669bcca702753ac50ccda87ca7fd93142d4e8e` or a verified descendant containing the same PR #975 lifecycle changes.
- `SystemReport` remains the fast shared telemetry source for GTK, `--stats`, and `--debug`.
- `--debug` must not create, remove, enable, disable, lock, deploy, or rewrite system/runtime configuration.
- Do not change source installation, daemon lifecycle, updater behavior, power policy, governor selection, or turbo policy.
- Do not add CPUFreq policy diagnostics, AMD P-State, TuneD-PPD, Snap-aware service reporting, or PPD D-Bus details in Stage 1.
- Reuse only observational lower-level primitives; do not call `auto_cpufreq.lifecycle` mutators from diagnostics.
- Preserve useful legacy battery-threshold information through structured reads rather than `battery_get_thresholds()` printing.
- Temporary validation files/workflows must not remain in the final candidate branch.
- Production changes follow TDD: observe the focused check fail before writing the code that satisfies it.

---

### Task 1: Add a temporary Stage 1 validation harness

**Files:**
- Create temporarily: `tmp_tests/test_debug_observability_stage1.py`
- Create temporarily: `.github/workflows/tmp-debug-observability-stage1.yml`

**Interfaces:**
- Consumes: current PR #975 tree with no `auto_cpufreq.modules.diagnostics` module.
- Produces: an executable failing contract for the Stage 1 diagnostic API and CLI read-only invariant.

- [ ] **Step 1: Add the failing diagnostic contract tests**

Create `tmp_tests/test_debug_observability_stage1.py` with stdlib-only tests so the RED phase does not depend on installing PyGObject or the whole application:

```python
import ast
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.diagnostics import (
    BatteryThresholdDiagnostics,
    DiagnosticsReport,
    IntelPstateInfo,
    PowerServicesInfo,
    collect_diagnostics,
    format_diagnostics_report,
    read_battery_threshold_diagnostics,
    read_debug_override,
    read_intel_pstate_info,
    read_power_services_info,
)


class DiagnosticsCollectorTests(unittest.TestCase):
    def test_override_reader_rejects_corrupt_or_non_string_values(self):
        self.assertEqual(
            read_debug_override(lambda: "performance", {"default", "performance"}),
            "performance",
        )
        self.assertIsNone(read_debug_override(lambda: 1, {"default", "performance"}))

        def broken():
            raise ValueError("corrupt pickle")

        self.assertIsNone(read_debug_override(broken, {"default", "performance"}))

    def test_intel_pstate_reader_is_fail_soft(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").write_text("active\n")
            (root / "min_perf_pct").write_text("not-an-int\n")
            (root / "max_perf_pct").write_text("100\n")

            self.assertEqual(
                read_intel_pstate_info(root),
                IntelPstateInfo(mode="active", min_perf_pct=None, max_perf_pct=100),
            )

    def test_battery_diagnostics_prefer_kernel_abi_and_keep_legacy_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "power_supply"
            root.mkdir()

            bat0 = root / "BAT0"
            bat0.mkdir()
            (bat0 / "type").write_text("Battery\n")
            (bat0 / "charge_control_start_threshold").write_text("40\n")
            (bat0 / "charge_control_end_threshold").write_text("80\n")
            (bat0 / "charge_start_threshold").write_text("1\n")
            (bat0 / "charge_stop_threshold").write_text("2\n")

            bat1 = root / "CMB1"
            bat1.mkdir()
            (bat1 / "type").write_text("Battery\n")
            (bat1 / "charge_start_threshold").write_text("55\n")
            (bat1 / "charge_stop_threshold").write_text("90\n")

            ac = root / "AC"
            ac.mkdir()
            (ac / "type").write_text("Mains\n")

            result = read_battery_threshold_diagnostics(
                power_supply_root=root,
                ideapad_roots=(),
            )

            self.assertEqual([item.name for item in result.batteries], ["BAT0", "CMB1"])
            self.assertEqual((result.batteries[0].start_threshold, result.batteries[0].stop_threshold), (40, 80))
            self.assertEqual((result.batteries[1].start_threshold, result.batteries[1].stop_threshold), (55, 90))

    def test_battery_diagnostics_read_conservation_mode_without_writes(self):
        with TemporaryDirectory() as tmp:
            power = Path(tmp) / "power_supply"
            power.mkdir()
            bat = power / "BAT0"
            bat.mkdir()
            (bat / "type").write_text("Battery\n")

            ideapad = Path(tmp) / "ideapad_acpi"
            ideapad.mkdir()
            (ideapad / "conservation_mode").write_text("1\n")

            result = read_battery_threshold_diagnostics(
                power_supply_root=power,
                ideapad_roots=(ideapad,),
            )
            self.assertIs(result.conservation_mode, True)

    def test_non_systemd_hosts_do_not_query_systemctl(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("openrc-init\n")

            calls = []
            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: calls.append(unit),
            )

            self.assertEqual(calls, [])
            self.assertEqual(result.init_system, "openrc-init")
            self.assertEqual(result.services, ())

    def test_service_state_distinguishes_missing_from_query_failure(self):
        with TemporaryDirectory() as tmp:
            init_comm = Path(tmp) / "comm"
            init_comm.write_text("systemd\n")

            states = {
                "auto-cpufreq.service": {
                    "load_state": "not-found",
                    "active_state": "inactive",
                    "unit_file_state": "",
                },
                "power-profiles-daemon.service": None,
                "tuned.service": {
                    "load_state": "loaded",
                    "active_state": "active",
                    "unit_file_state": "enabled",
                },
                "tlp.service": {
                    "load_state": "loaded",
                    "active_state": "inactive",
                    "unit_file_state": "disabled",
                },
            }

            result = read_power_services_info(
                init_comm=init_comm,
                capture_state=lambda unit: states[unit],
            )
            by_name = {item.name: item for item in result.services}

            self.assertIs(by_name["auto-cpufreq"].installed, False)
            self.assertIsNone(by_name["power-profiles-daemon"].installed)
            self.assertIs(by_name["tuned"].installed, True)
            self.assertEqual(by_name["tuned"].active_state, "active")

    def test_collect_diagnostics_returns_one_structured_report(self):
        fake_system_report = SimpleNamespace()
        result = collect_diagnostics(
            fake_system_report,
            config_path="/tmp/auto-cpufreq.conf",
            governor_override_getter=lambda: "default",
            turbo_override_getter=lambda: "auto",
            intel_pstate_root=Path("/nonexistent"),
            power_supply_root=Path("/nonexistent"),
            ideapad_roots=(),
            init_comm=Path("/nonexistent"),
            capture_state=lambda unit: None,
        )

        self.assertIsInstance(result, DiagnosticsReport)
        self.assertEqual(result.config_path, "/tmp/auto-cpufreq.conf")
        self.assertEqual(result.governor_override, "default")
        self.assertEqual(result.turbo_override, "auto")
        self.assertIsInstance(result.intel_pstate, IntelPstateInfo)
        self.assertIsInstance(result.power_services, PowerServicesInfo)
        self.assertIsInstance(result.battery_thresholds, BatteryThresholdDiagnostics)


class DebugCliInvariantTests(unittest.TestCase):
    def test_debug_branch_does_not_call_legacy_mutating_or_duplicate_collectors(self):
        source = Path("auto_cpufreq/bin/auto_cpufreq.py").read_text()
        tree = ast.parse(source)

        debug_body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "debug":
                debug_body = node.body
                break

        self.assertIsNotNone(debug_body)
        calls = {
            node.func.id
            for statement in debug_body
            for node in ast.walk(statement)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        self.assertIn("collect_diagnostics", calls)
        self.assertNotIn("cpufreqctl", calls)
        self.assertNotIn("battery_get_thresholds", calls)
        self.assertNotIn("charging", calls)
        self.assertNotIn("get_current_gov", calls)
        self.assertNotIn("get_turbo", calls)
        self.assertNotIn("get_load", calls)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add a temporary workflow that runs only this contract**

Create `.github/workflows/tmp-debug-observability-stage1.yml`:

```yaml
name: Temporary Debug Observability Stage 1

on:
  push:
    branches:
      - tmp/debug-observability-on-975-current

jobs:
  diagnostics-contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Run Stage 1 contract
        run: python -m unittest -v tmp_tests.test_debug_observability_stage1
```

- [ ] **Step 3: Commit and verify RED**

```bash
git add tmp_tests/test_debug_observability_stage1.py .github/workflows/tmp-debug-observability-stage1.yml
git commit -m "test: define debug observability stage 1 contract"
```

Expected workflow result: **FAIL**, with import failure for
`auto_cpufreq.modules.diagnostics` and/or the missing Stage 1 API. A passing
workflow at this point means the test is not proving the new boundary and must
be corrected before production code is written.

---

### Task 2: Introduce structured read-only diagnostic collectors

**Files:**
- Create: `auto_cpufreq/modules/diagnostics.py`
- Test: `tmp_tests/test_debug_observability_stage1.py`

**Interfaces:**
- Consumes: `power_state.capture_service_state()` as an observational systemd primitive and injected getters/paths.
- Produces:
  - `IntelPstateInfo`
  - `BatteryThresholdInfo`
  - `BatteryThresholdDiagnostics`
  - `ServiceStatus`
  - `PowerServicesInfo`
  - `DiagnosticsReport`
  - `read_debug_override()`
  - `read_intel_pstate_info()`
  - `read_battery_threshold_diagnostics()`
  - `read_power_services_info()`
  - `collect_diagnostics()`

- [ ] **Step 1: Implement immutable value objects and safe primitive readers**

Create `auto_cpufreq/modules/diagnostics.py` with dependency-light imports and these public shapes:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from auto_cpufreq.power_state import capture_service_state

INTEL_PSTATE_ROOT = Path("/sys/devices/system/cpu/intel_pstate")
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
SYSTEMD_INIT_COMM = Path("/proc/1/comm")
IDEAPAD_ROOTS = (
    Path("/sys/bus/platform/drivers/ideapad_acpi"),
    Path("/sys/devices/platform/ideapad_acpi"),
)
POWER_SERVICE_UNITS = (
    ("auto-cpufreq", "auto-cpufreq.service"),
    ("power-profiles-daemon", "power-profiles-daemon.service"),
    ("tuned", "tuned.service"),
    ("TLP", "tlp.service"),
)


@dataclass(frozen=True)
class IntelPstateInfo:
    mode: str | None = None
    min_perf_pct: int | None = None
    max_perf_pct: int | None = None


@dataclass(frozen=True)
class BatteryThresholdInfo:
    name: str
    start_threshold: int | None = None
    stop_threshold: int | None = None


@dataclass(frozen=True)
class BatteryThresholdDiagnostics:
    batteries: tuple[BatteryThresholdInfo, ...] = ()
    conservation_mode: bool | None = None


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    installed: bool | None
    active_state: str | None = None
    unit_file_state: str | None = None


@dataclass(frozen=True)
class PowerServicesInfo:
    init_system: str | None
    services: tuple[ServiceStatus, ...] = ()


@dataclass(frozen=True)
class DiagnosticsReport:
    config_path: str | None
    governor_override: str | None
    turbo_override: str | None
    intel_pstate: IntelPstateInfo
    battery_thresholds: BatteryThresholdDiagnostics
    power_services: PowerServicesInfo
```

Use `_read_text()`, `_read_int()`, and `_read_first_int()` helpers that catch only read/parse failures relevant to diagnostics and return `None` rather than printing.

- [ ] **Step 2: Implement Intel P-State and override readers**

`read_intel_pstate_info(root=INTEL_PSTATE_ROOT)` reads only `status`, `min_perf_pct`, and `max_perf_pct`. `read_debug_override(getter, allowed_values)` must catch getter failures, reject non-string deserialized values, and return `None` for unavailable/invalid values rather than a presentation string.

- [ ] **Step 3: Implement battery threshold diagnostics from sysfs**

`read_battery_threshold_diagnostics()` must:

```python
start = _read_first_int(
    battery,
    ("charge_control_start_threshold", "charge_start_threshold"),
)
stop = _read_first_int(
    battery,
    ("charge_control_end_threshold", "charge_stop_threshold"),
)
```

Discover batteries by reading each power-supply entry's `type` and accepting
case-insensitive `Battery`, not by assuming a `BAT*` name. Sort device names for
deterministic output. Ignore non-battery supplies.

For Ideapad conservation mode, check each configured root first for a direct
`conservation_mode` file, then one level of `*/conservation_mode`, matching the
existing project compatibility paths. Read `0` as `False`, `1` as `True`, and
anything else as unavailable. Do not instantiate a battery device class and do
not check write permission.

- [ ] **Step 4: Implement structured systemd service observation**

`read_power_services_info()` first reads `/proc/1/comm`. If PID 1 is not
`systemd`, return the init name and an empty service tuple without calling the
injected `capture_state` function.

For systemd, convert `capture_service_state()` results as follows:

```python
if state is None:
    ServiceStatus(name, installed=None)
elif state.get("load_state") == "not-found":
    ServiceStatus(name, installed=False)
else:
    ServiceStatus(
        name,
        installed=True,
        active_state=state.get("active_state") or None,
        unit_file_state=state.get("unit_file_state") or None,
    )
```

Do not add OpenRC/dinit/runit/s6 queries in this stage.

- [ ] **Step 5: Implement the single collection entry point**

Use this API:

```python
def collect_diagnostics(
    system_report,
    *,
    config_path: str | None,
    governor_override_getter: Callable[[], object],
    turbo_override_getter: Callable[[], object],
    intel_pstate_root: Path = INTEL_PSTATE_ROOT,
    power_supply_root: Path = POWER_SUPPLY_ROOT,
    ideapad_roots: Iterable[Path] = IDEAPAD_ROOTS,
    init_comm: Path = SYSTEMD_INIT_COMM,
    capture_state=capture_service_state,
) -> DiagnosticsReport:
    # system_report is intentionally the already-collected fast snapshot and
    # establishes the one-snapshot boundary for this report family. Stage 1
    # collectors do not need to recollect any of its fields.
    return DiagnosticsReport(...)
```

The implementation must call each one-shot collector exactly once and validate
governor values against `{"default", "powersave", "performance"}` and turbo
values against `{"auto", "always", "never"}`.

- [ ] **Step 6: Run the focused tests**

```bash
python -m unittest -v tmp_tests.test_debug_observability_stage1.DiagnosticsCollectorTests
```

Expected: collector tests **PASS**; the CLI invariant test still **FAILS**
because `--debug` has not yet been migrated.

- [ ] **Step 7: Commit the collector boundary**

```bash
git add auto_cpufreq/modules/diagnostics.py
git commit -m "feat: add structured read-only debug diagnostics"
```

---

### Task 3: Add formatting over the two report objects without recollection

**Files:**
- Modify: `auto_cpufreq/modules/diagnostics.py`
- Modify: `tmp_tests/test_debug_observability_stage1.py`

**Interfaces:**
- Consumes: one existing `SystemReport` plus one `DiagnosticsReport`.
- Produces: `format_diagnostics_report(system_report, diagnostics) -> str`.

- [ ] **Step 1: Add failing formatter tests**

Extend the temporary test file with a fake report built from `SimpleNamespace`
and assert the formatter includes observed semantics without invoking any
collector:

```python
def test_formatter_uses_existing_reports_for_power_and_load_state(self):
    system_report = SimpleNamespace(
        battery_info=SimpleNamespace(
            is_charging=False,
            is_ac_plugged=False,
            battery_level=73,
            power_consumption=7.5,
        ),
        cpu_driver="intel_pstate",
        current_gov="powersave",
        current_epp="balance_power",
        current_epb="balance_power",
        current_hwp_dynamic_boost=False,
        is_turbo_on=(True, False),
        cpu_usage=12.5,
        load=0.42,
        cpu_avg_temp=51.25,
    )
    diagnostics = DiagnosticsReport(
        config_path=None,
        governor_override="default",
        turbo_override="auto",
        intel_pstate=IntelPstateInfo("active", 20, 100),
        battery_thresholds=BatteryThresholdDiagnostics(),
        power_services=PowerServicesInfo("systemd", ()),
    )

    output = format_diagnostics_report(system_report, diagnostics)

    self.assertIn("AC power: Disconnected", output)
    self.assertIn("Auto-cpufreq profile selection: battery", output)
    self.assertIn("Governor: powersave", output)
    self.assertIn("Turbo Boost: Enabled", output)
    self.assertIn("Total CPU usage: 12.5%", output)
    self.assertIn("Total system load: 0.42", output)
    self.assertIn("Average temp. of all cores: 51.25 °C", output)
```

Add separate formatter assertions for:
- unknown AC -> `Unknown` and `charger (AC state unknown)`;
- missing service -> `Not installed`;
- failed service query -> `Unavailable`;
- multiple battery threshold devices appear by name;
- conservation mode appears only when known;
- absent config -> `defaults (no config file)`.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest -v tmp_tests.test_debug_observability_stage1
```

Expected: formatter tests **FAIL** because `format_diagnostics_report()` is
missing or incomplete.

- [ ] **Step 3: Implement pure formatting**

Add small private semantic formatters for battery, AC, profile selection,
turbo, service state, and optional values. `format_diagnostics_report()` may
read fields only from its two arguments; it must not read sysfs, invoke a
getter, call `systemctl`, or collect a second `SystemReport`.

The generated text should contain compact sections for:

```text
Configuration
Power Source
Battery Thresholds (only when battery devices or conservation mode are known)
CPU Power State
System Load
Power Management Services
```

Keep `default` governor override user-facing as `none (profile-controlled)`;
keep `auto` turbo override user-facing as `auto`. Treat `(None, True)` turbo as
`Driver managed`, `(True, ...)` as `Enabled`, `(False, ...)` as `Disabled`, and
otherwise `Unavailable`.

- [ ] **Step 4: Verify GREEN**

```bash
python -m unittest -v tmp_tests.test_debug_observability_stage1
```

Expected: formatter/collector tests **PASS** except the CLI invariant, which
still fails until Task 4.

- [ ] **Step 5: Commit formatting**

```bash
git add auto_cpufreq/modules/diagnostics.py tmp_tests/test_debug_observability_stage1.py
git commit -m "feat: format one-shot debug diagnostics"
```

The test file is temporary and will be removed in Task 6 even though it is
updated here to preserve the red/green history during development.

---

### Task 4: Migrate only `--debug` to the structured read-only path

**Files:**
- Modify: `auto_cpufreq/bin/auto_cpufreq.py`
- Test: `tmp_tests/test_debug_observability_stage1.py`

**Interfaces:**
- Consumes: `system_info.generate_system_report()`, `collect_diagnostics()`, `format_diagnostics_report()`.
- Produces: a debug CLI branch that collects the fast snapshot once and never deploys a helper.

- [ ] **Step 1: Confirm the CLI invariant is still RED before editing**

```bash
python -m unittest -v tmp_tests.test_debug_observability_stage1.DebugCliInvariantTests
```

Expected: **FAIL** because the current branch still calls `cpufreqctl()` and
`battery_get_thresholds()` and does not call `collect_diagnostics()`.

- [ ] **Step 2: Add diagnostic imports**

Import only the public Stage 1 boundary:

```python
from auto_cpufreq.modules.diagnostics import (
    collect_diagnostics,
    format_diagnostics_report,
)
from auto_cpufreq.modules.system_info import (
    format_platform_profile_summary,
    print_system_report,
    system_info,
)
```

Do not import the Stage 1 collector internals into the CLI.

- [ ] **Step 3: Replace the legacy `--debug` collection sequence**

Use this shape inside `elif debug:`:

```python
root_check()
report = system_info.generate_system_report()
diagnostics = collect_diagnostics(
    report,
    config_path=config_path if conf.has_config() else None,
    governor_override_getter=get_override,
    turbo_override_getter=get_turbo_override,
)

footer()
print_system_report(report, include_config=False)
print()
app_version()
print()
print(format_diagnostics_report(report, diagnostics))
print()
python_info()
print()
device_info()
print()
app_res_use()
footer()
```

Remove from the debug branch only:

```python
config_info_dialog()
battery_get_thresholds()
cpufreqctl()
print(f"Battery is: {'' if charging() else 'dis'}charging")
get_load()
get_current_gov()
get_turbo()
```

The information represented by the latter four state readers now comes from
the already-collected `SystemReport` and diagnostic formatter. Keep
`root_check()` in Stage 1; changing privilege requirements is explicitly a
separate follow-up.

- [ ] **Step 4: Verify the full temporary contract is GREEN**

```bash
python -m unittest -v tmp_tests.test_debug_observability_stage1
```

Expected: **PASS**.

- [ ] **Step 5: Run syntax and static mutation checks**

```bash
python -m py_compile auto_cpufreq/modules/diagnostics.py auto_cpufreq/bin/auto_cpufreq.py
python - <<'PY'
from pathlib import Path
source = Path('auto_cpufreq/bin/auto_cpufreq.py').read_text()
assert 'battery_get_thresholds()' not in source[source.index('elif debug:'):source.index('elif version:')]
assert 'cpufreqctl()' not in source[source.index('elif debug:'):source.index('elif version:')]
PY
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the CLI migration**

```bash
git add auto_cpufreq/bin/auto_cpufreq.py
git commit -m "fix: make debug reporting read-only"
```

---

### Task 5: Verify Stage 1 against current PR #975 behavior

**Files:**
- No production file changes unless a verification exposes a concrete bug.
- Temporary tests/workflow remain available during this task.

**Interfaces:**
- Consumes: completed Stage 1 implementation.
- Produces: evidence that diagnostics did not alter GTK/stats/lifecycle boundaries.

- [ ] **Step 1: Run the temporary contract on GitHub Actions**

Push the Stage 1 branch and confirm `Temporary Debug Observability Stage 1`
finishes **success**.

- [ ] **Step 2: Run existing project CI**

Confirm the current branch's existing workflows complete successfully:

```text
Linux Build
Nix Flake
```

No new permanent workflow is added.

- [ ] **Step 3: Inspect the production diff for scope**

The production diff relative to the branch baseline should be limited to:

```text
auto_cpufreq/modules/diagnostics.py
auto_cpufreq/bin/auto_cpufreq.py
```

plus the approved spec/plan documents. A change to `lifecycle.py`,
`power_state.py`, installer scripts, Nix lifecycle patches, GTK, or
`system_info.py` requires a specific demonstrated need; otherwise revert it.

- [ ] **Step 4: Verify one-snapshot and no-mutation properties manually**

Inspect the final `elif debug:` block and `collect_diagnostics()` call graph.
There must be exactly one `system_info.generate_system_report()` call in the
debug branch and no route from diagnostics into:

```text
cpufreqctl()
cpufreqctl_restore()
install_daemon_lifecycle()
remove_daemon_lifecycle()
update_source_install()
set_bluetooth_boot_enabled()
bluetooth_enable()/bluetooth_disable()
service enable/disable/start/stop helpers
operation locks
```

`capture_service_state()` is allowed because its current contract is
observational.

- [ ] **Step 5: Check the branch against the current PR #975 head again**

Before declaring Stage 1 complete, fetch `installer-resilience-final` again. If
PR #975 advanced after `84669bcca702753ac50ccda87ca7fd93142d4e8e`, compare the
new commits to the Stage 1 production files and lifecycle/service-state
contracts. Rebase/update only after understanding any overlap; do not force a
stale diagnostic tree over a newer #975 tree.

---

### Task 6: Remove temporary validation infrastructure and verify the candidate

**Files:**
- Delete: `tmp_tests/test_debug_observability_stage1.py`
- Delete: `.github/workflows/tmp-debug-observability-stage1.yml`
- Keep: `auto_cpufreq/modules/diagnostics.py`
- Keep: `auto_cpufreq/bin/auto_cpufreq.py`
- Keep: spec and plan documents on the temporary development branch unless the eventual PR scope decides otherwise.

**Interfaces:**
- Consumes: already-green Stage 1 implementation.
- Produces: a candidate tree without temporary test/workflow artifacts.

- [ ] **Step 1: Record the last green temporary validation run**

Capture the successful workflow run URL/SHA before deleting the temporary
files so the evidence remains auditable.

- [ ] **Step 2: Remove temporary validation files**

```bash
git rm tmp_tests/test_debug_observability_stage1.py
git rm .github/workflows/tmp-debug-observability-stage1.yml
git commit -m "test: remove temporary debug observability validation"
```

- [ ] **Step 3: Re-run production-only checks**

```bash
python -m py_compile auto_cpufreq/modules/diagnostics.py auto_cpufreq/bin/auto_cpufreq.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 4: Re-run existing Linux and Nix workflows on the candidate**

Expected: `Linux Build = success` and `Nix Flake = success` on the exact
candidate SHA.

- [ ] **Step 5: Final diff audit**

Confirm:

```text
- no tmp_tests/
- no tmp-debug-observability workflow
- no cpufreqctl() call in --debug
- no battery_get_thresholds() call in --debug
- no Stage 2 AMD/policy/Snap/TuneD-PPD/PPD-D-Bus implementation
- no lifecycle behavior changes
- no generated files or unrelated cleanup
```

The candidate is ready for review only after these checks are attached to the
exact final SHA.
