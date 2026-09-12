# Debug observability Stage 2 implementation plan

## Baseline

Stage 2 starts from `tmp/debug-observability-stage1-candidate` at
`b00d7ae865d9211a52baffa4887387f5b35b1ce7`.

The Stage 1 boundary is preserved:

- `SystemReport` remains the one fast telemetry snapshot;
- `DiagnosticsReport` owns one-shot deep diagnostics;
- collectors are read-only and return structured values;
- formatting performs no I/O;
- GTK and `--stats` do not gain deep service/sysfs polling;
- lifecycle/install/update/live/daemon behavior is out of scope.

## Authoritative interfaces

Implementation follows the current kernel and Snap interfaces rather than the
older experimental branch.

### CPUFreq

Use `/sys/devices/system/cpu/cpufreq/policy*` as the observation unit. Each
policy represents the CPUs sharing one hardware performance-scaling interface.
Read generic CPUFreq attributes when present:

- `related_cpus`;
- `scaling_driver`;
- `scaling_governor`;
- `scaling_available_governors`;
- `scaling_min_freq` / `scaling_max_freq`;
- `cpuinfo_min_freq` / `cpuinfo_max_freq`;
- `energy_performance_preference`;
- `energy_performance_available_preferences`.

All attributes are optional for diagnostics. Missing or malformed values become
`None` rather than aborting the report.

### AMD P-State

Read global AMD P-State state from `/sys/devices/system/cpu/amd_pstate/`:

- `status`: `active`, `passive`, `guided`, or `disable` when exposed;
- `prefcore`: preferred-core state when exposed.

Do not mutate either attribute.

### Snap service state

When `IS_INSTALLED_WITH_SNAP` is true, query the snap's own daemon with
`snapctl services auto-cpufreq.service`. `snapctl` can query only services of
the calling snap, so host PPD/TuneD/TLP/tuned-ppd state must be represented as
unavailable due to Snap confinement rather than guessed from host unit names.

Outside Snap, continue using `capture_service_state()` on systemd hosts and add
`tuned-ppd.service` to the observed service set.

## Task 1: RED contracts for CPUFreq and AMD P-State

Create temporary Stage 2 tests before production changes.

Required CPUFreq cases:

1. Policies sort numerically (`policy2` before `policy10`).
2. `related_cpus` parses ranges and comma-separated CPU lists without assuming
   `policyX == CPU X`.
3. Current governor/EPP, available governors/EPP, driver, scaling limits, and
   hardware limits are collected independently.
4. Missing attributes remain `None` while the rest of the policy survives.
5. Malformed integer limits remain `None`.
6. Multiple policies preserve differing state rather than collapsing to CPU0.

Required AMD P-State cases:

1. `active`, `passive`, `guided`, and `disable` are preserved verbatim.
2. Missing `status` or `prefcore` is fail-soft.
3. Unknown future strings are reported rather than rejected, because debug
   output should preserve kernel-observed state.

Observe RED before implementation.

## Task 2: Implement structured policy and AMD collectors

Extend `auto_cpufreq/modules/diagnostics.py` with immutable value objects and
read-only collectors.

Suggested model:

```python
@dataclass(frozen=True)
class CpuFreqPolicyInfo:
    name: str
    related_cpus: tuple[int, ...] | None = None
    scaling_driver: str | None = None
    scaling_governor: str | None = None
    available_governors: tuple[str, ...] | None = None
    energy_performance_preference: str | None = None
    available_epp_preferences: tuple[str, ...] | None = None
    scaling_min_freq_khz: int | None = None
    scaling_max_freq_khz: int | None = None
    cpuinfo_min_freq_khz: int | None = None
    cpuinfo_max_freq_khz: int | None = None

@dataclass(frozen=True)
class AmdPstateInfo:
    mode: str | None = None
    preferred_core: str | None = None
```

Add both to `DiagnosticsReport` and collect them from the existing one-shot
entry point. Do not recollect governor/EPP through another fast telemetry path;
per-policy values are diagnostics specifically for divergence/capability.

Run the focused contracts and reach GREEN before formatting work.

## Task 3: RED contracts for compact formatting

Add formatter tests before production formatting changes.

Formatting requirements:

1. When all policies agree, show one compact value for governor, driver,
   available governors/EPP, and limits.
2. When policies differ, say they are mixed and identify the policy groups.
3. Include related CPUs where expansion is needed so heterogeneous systems are
   diagnosable.
4. Show scaling limits separately from hardware (`cpuinfo`) limits.
5. AMD P-State appears only when at least one AMD field is available.
6. Existing Stage 1 sections remain present and no formatter performs I/O.

Observe RED, implement the smallest formatting helpers, then reach GREEN.

## Task 4: RED contracts for Snap and tuned-ppd services

Add service tests before production changes.

Non-Snap/systemd:

- query `auto-cpufreq.service`, `power-profiles-daemon.service`,
  `tuned.service`, `tuned-ppd.service`, and `tlp.service`;
- preserve missing-unit vs query-failure semantics from
  `capture_service_state()`.

Snap:

- invoke injected `snapctl services auto-cpufreq.service` exactly once;
- parse the service's startup/current fields;
- never query host service units through `capture_service_state()`;
- report PPD/TuneD/tuned-ppd/TLP as unavailable due to Snap confinement;
- malformed/non-zero/missing `snapctl` results are fail-soft and do not abort
  the report.

Observe RED before implementation.

## Task 5: Integrate Stage 2 into `--debug`

The CLI should need no new deep collection calls. `collect_diagnostics()`
continues to return one complete one-shot report and the existing
`format_diagnostics_report()` consumes it.

Do not add direct sysfs, subprocess, `systemctl`, or `snapctl` calls to
`auto_cpufreq/bin/auto_cpufreq.py`.

## Task 6: Verification and clean candidate

On the investigation branch run temporary contracts plus:

```bash
python -m py_compile auto_cpufreq/modules/diagnostics.py \
  auto_cpufreq/bin/auto_cpufreq.py
git diff --check b00d7ae865d9211a52baffa4887387f5b35b1ce7...HEAD
```

Require Linux Build and Nix Flake to pass on the exact validated SHA.

Then create a clean Stage 2 candidate from `b00d7ae...` containing only the
production changes needed for Stage 2. Keep temporary tests/workflows and this
plan on the investigation branch.

Before declaring Stage 2 complete, compare the clean candidate against
`b00d7ae...` and verify that no lifecycle, installer, updater, GTK, stats, or
policy-control code changed.
