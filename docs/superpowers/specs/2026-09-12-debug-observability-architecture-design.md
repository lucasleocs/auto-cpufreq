# Debug observability architecture

## Context

The current reporting stack already has an important shared foundation: GTK, `--stats`, and `--debug` all consume `SystemInfo.generate_system_report()` for fast system telemetry. That should remain the source of truth for frequently refreshed state such as CPU usage, frequencies, temperatures, fan speed, battery state, governor/EPP/EPB, turbo, HWP Dynamic Boost, and platform-profile state.

The weakness is the diagnostic layer around that snapshot. `--debug` still performs one-off collection directly in the CLI, duplicates some interpretation logic used by GTK or `--stats`, and retains legacy calls such as `cpufreqctl()` and `battery_get_thresholds()` that bypass the structured-report model. This makes diagnostics easier to forget when new power-management features are added and allows different frontends to disagree about the same underlying state.

## Goals

1. Make `auto-cpufreq --debug` observational: collecting a debug report must not install, copy, enable, disable, or otherwise mutate system state.
2. Keep `SystemReport` as the shared, lightweight telemetry snapshot used by GTK, `--stats`, and `--debug`.
3. Add a structured one-shot `DiagnosticsReport` for deeper information that is useful in bug reports but should not be polled every two seconds.
4. Keep collectors independent from presentation. Collectors return structured values; CLI/GTK/Urwid format them.
5. Reduce duplicated semantic interpretation so GTK, `--stats`, and `--debug` cannot silently disagree about the same state.
6. Make future power-management features naturally expose diagnostic state through a shared collector/report layer rather than ad-hoc CLI code.
7. Improve issue reports with relevant kernel/service state while avoiding an encyclopedic dump of unrelated system information.

## Non-goals

- Do not change auto-cpufreq power policy, governor selection, turbo policy, service lifecycle, or daemon behavior.
- Do not make GTK or `--stats` perform expensive D-Bus/service-manager diagnostics on each refresh.
- Do not add complete service-manager diagnostics for every init system in the first implementation stage.
- Do not add broad hardware inventory unrelated to power-management decisions.
- Do not use frontend output as an input source; GTK, Urwid, and CLI remain presentation layers only.

## Architecture

### Fast telemetry: `SystemReport`

`SystemReport` remains the point-in-time snapshot for values that can change continuously and are useful to more than one frontend.

Examples:

- distro/kernel/architecture/CPU model;
- CPUFreq driver;
- CPU/core frequency, usage, temperature, and online/offline state;
- current governor, EPP, EPB;
- HWP Dynamic Boost and turbo state;
- fan state;
- battery level, AC state, charging state, power, charge thresholds;
- platform-profile snapshot;
- load averages and aggregate CPU statistics.

`SystemInfo.generate_system_report()` remains read-only and is the only normal collection path for these values. GTK and `--stats` continue to consume this report directly. `--debug` also consumes exactly one `SystemReport` rather than recollecting the same telemetry through legacy helper commands.

### Deep diagnostics: `DiagnosticsReport`

Add a one-shot structured report in `auto_cpufreq/modules/diagnostics.py`. It contains information that is valuable in a support report but does not need continuous polling.

Initial fields should include:

- effective configuration path/state;
- governor and turbo override state;
- P-state implementation details (`intel_pstate`, later AMD P-State through the same model);
- power-management service state;
- structured battery-threshold/device diagnostics where the existing `battery_get_thresholds()` path provides additional information beyond `BatteryInfo`;
- CPUFreq policy diagnostics as they are introduced.

The report should be built by a single collector function, conceptually:

```python
collect_diagnostics(system_report: SystemReport) -> DiagnosticsReport
```

Passing the already-collected `SystemReport` lets diagnostics derive context such as AC state and CPU driver without rereading fast telemetry unnecessarily.

### Collector boundaries

Collectors read kernel/sysfs, persisted auto-cpufreq state, service state, package/runtime information, or stable IPC APIs. They do not print.

Examples:

- `read_pstate_info()` -> structured P-state state;
- `read_debug_override()` -> validated override state;
- `read_power_services_info()` -> structured service states;
- `read_battery_threshold_diagnostics()` -> structured battery-device threshold state;
- `read_cpufreq_policies()` -> per-policy CPUFreq state.

Formatting functions accept these objects and return strings or frontend-specific values. They never recollect state.

## Read-only debug invariant

The debug path must not invoke helpers whose purpose is deployment or mutation.

The current call to `cpufreqctl()` in the `--debug` path should be removed. `cpufreqctl()` can copy `cpufreqctl.auto-cpufreq` into `/usr/local/bin`; this was needed by the legacy debug implementation but is no longer necessary because the shared telemetry layer reads governor and related state directly from sysfs.

`battery_get_thresholds()` should also leave the final debug path. Although it is primarily a reader, it prints directly and follows a separate vendor/device path. Any additional threshold information it provides should be exposed through a structured collector instead.

The intended invariant is:

> Running `auto-cpufreq --debug` may read system and auto-cpufreq state, but it must not create, remove, enable, disable, or rewrite system/runtime configuration as a side effect of producing the report.

Root may still be required temporarily where existing readable state or confinement requires it, but mutation must not be used merely to make diagnostics possible. If the final collectors prove readable without root, removing the root requirement can be evaluated separately after behavior parity is demonstrated.

## Shared semantics

Presentation strings do not need to be globally identical, but semantic classification should be centralized.

The current code already demonstrated why: battery and turbo state have been interpreted separately by GTK, `--stats`, and diagnostics. The same raw state should first become a canonical semantic value, then each frontend can choose a label.

Initial targets:

- battery state: charging / discharging / not-charging / unknown;
- AC state: connected / disconnected / unknown;
- turbo state: on / off / driver-managed / unavailable;
- profile selection: charger / battery / charger-with-unknown-AC;
- service state: installed/active/unit-file state/unavailable rather than frontend-specific guesses.

Prefer small enums or immutable value objects where they remove repeated branching. Do not refactor unrelated legacy display code solely for stylistic uniformity.

## CPUFreq policy diagnostics

A later stage of this same architecture should stop assuming CPU0 is sufficient for debug output.

Linux CPUFreq exposes policy objects under `/sys/devices/system/cpu/cpufreq/policy*`. Diagnostic collection should be able to report, per policy:

- related CPUs;
- scaling driver;
- current governor;
- available governors;
- current/scaling minimum and maximum limits where available;
- hardware minimum and maximum limits where available;
- EPP/current available EPP values where exposed by that policy.

The formatter should remain compact when all policies agree and expand only when policies differ. This makes heterogeneous or multi-policy systems debuggable without turning every normal report into a large dump.

## P-state diagnostics

P-state information should evolve toward a common model rather than adding unrelated frontend conditionals.

Intel currently exposes global `intel_pstate` mode and percentage limits. AMD exposes a global `amd_pstate` status with modes such as active/passive/guided/disable. The collector should identify the implementation from the authoritative sysfs interface rather than infer it solely from the CPUFreq driver label.

Implementation-specific fields may remain optional, but frontends should consume one structured P-state diagnostic object.

## Power-management service diagnostics

The current systemd collector is useful and should remain read-only. Extend it carefully:

- include `tuned-ppd.service` alongside TuneD where applicable;
- make auto-cpufreq daemon status installation-aware, especially for Snap, where the daemon is a Snap service rather than `auto-cpufreq.service`;
- on non-systemd PID 1, report that service state is unavailable rather than invoking `systemctl` opportunistically;
- do not add complete OpenRC/dinit/runit/s6 support until a small, reliable shared abstraction exists.

For Snap, prefer the Snap service-management interface available inside the snap rather than assuming a host systemd unit name.

## Optional PPD details

Power Profiles Daemon exposes useful diagnostic state beyond whether its service is running, including active profile, degraded-performance reason, and profile holds. These are appropriate for `DiagnosticsReport` because they can explain external constraints or conflicts, but they should be added only after the report/collector boundary is established.

Do not add D-Bus polling to `SystemReport` or to the GTK refresh loop merely to support debug output.

## Data flow

```text
kernel/sysfs + psutil + config + stable service/IPC APIs
                         |
                shared read-only collectors
                         |
             +-----------+-----------+
             |                       |
        SystemReport           DiagnosticsReport
        fast snapshot          one-shot deep state
             |                       |
        +----+----+                  |
        |         |                  |
       GTK      --stats            --debug
        \_________|__________________/
          shared semantic values
```

## Error handling

Diagnostics must fail soft whenever a missing kernel interface, service, optional package, corrupt override file, or unavailable IPC source is not itself fatal to auto-cpufreq.

Rules:

- missing optional sysfs attribute -> structured unavailable/unknown state;
- malformed integer/string -> unavailable, not exception from `--debug`;
- stale/corrupt persisted override -> unavailable;
- absent systemd unit -> not installed;
- service-manager/IPC query failure -> unavailable while preserving the rest of the report;
- one failed optional diagnostic collector must not prevent the main `SystemReport` from being printed.

Do not catch exceptions around power-policy code merely to hide real bugs in normal operation; fail-soft behavior here applies to the diagnostic collection boundary.

## Implementation stages

### Stage 1: establish the boundary

- introduce `DiagnosticsReport` and a single collection entry point;
- move existing Intel P-State, override, and service diagnostics into that report;
- remove `cpufreqctl()` from `--debug`;
- replace direct `battery_get_thresholds()` printing with structured threshold diagnostics or, if no additional information is required for parity, rely on `BatteryInfo` and remove the duplicate path;
- keep output behavior otherwise compatible enough for issue reports.

### Stage 2: eliminate important diagnostic blind spots

- collect CPUFreq policies;
- add AMD P-State through the common P-state model;
- add `tuned-ppd`;
- make daemon service diagnostics Snap-aware;
- expose available governors/EPP and effective policy limits.

### Stage 3: targeted external-state diagnostics

- add selected PPD D-Bus details such as degraded state and active holds if they materially improve issue diagnosis;
- consider non-systemd service status only if it can be implemented through a small shared abstraction without duplicating lifecycle logic.

Each stage should preserve the read-only debug invariant and keep GTK/`--stats` free of expensive one-shot diagnostics.

## Testing strategy

There is no need to add a large permanent test suite to upstream solely for this work. During development, use temporary tests/workflows or local scripts to exercise collectors with synthetic roots and injected service/query functions.

Minimum verification for Stage 1:

- `--debug` no longer calls or deploys `cpufreqctl.auto-cpufreq`;
- collecting diagnostics does not mutate filesystem/service state;
- corrupt/non-string override state is fail-safe;
- active/passive Intel P-State remains reported correctly;
- battery state classification remains consistent for connected/disconnected/unknown AC states;
- systemd absent/non-PID1 does not trigger systemctl queries;
- missing service units and service query failures are represented without aborting the report;
- GTK and `--stats` still build from `SystemReport` with no dependency on `DiagnosticsReport`;
- Linux and Nix workflows remain green.

For policy/P-state extensions, collectors should support injectable roots so active/passive/guided/mixed-policy cases can be reproduced without specific hardware.

## Success criteria

The architecture is successful when:

1. a maintainer can ask for `auto-cpufreq --debug` and receive the effective auto-cpufreq/kernel/service state needed for common power-management issues without a long list of follow-up `cat`/`systemctl` commands;
2. running the report does not change the state it is trying to diagnose;
3. GTK, `--stats`, and `--debug` agree on shared telemetry because they consume the same structured snapshot/semantic values;
4. adding a new power-management feature has an obvious diagnostic integration point instead of requiring ad-hoc edits to each frontend;
5. expensive or one-shot diagnostics remain isolated from the fast GTK/monitor refresh path.