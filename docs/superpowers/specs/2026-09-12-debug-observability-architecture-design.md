# Debug observability architecture

## Context

The current reporting stack already has an important shared foundation: GTK,
`--stats`, and `--debug` all have access to `SystemInfo.generate_system_report()`
for fast system telemetry. That should remain the source of truth for frequently
refreshed state such as CPU usage, frequencies, temperatures, fan speed,
battery state, governor/EPP/EPB, turbo, HWP Dynamic Boost, and
platform-profile state.

The weakness is the diagnostic layer around that snapshot. On the current
`installer-resilience-final` baseline, `--debug` still performs one-off
collection directly in the CLI and still invokes legacy helpers such as
`cpufreqctl()` and `battery_get_thresholds()`. `cpufreqctl()` is a deployment
helper and can create `/usr/local/bin/cpufreqctl.auto-cpufreq`; therefore a
command intended to observe the system can change the system it is trying to
diagnose. `battery_get_thresholds()` is primarily read-only, but it prints
through vendor/device classes and bypasses the structured reporting model.

This makes diagnostics easy to forget when new power-management features are
added and allows different frontends to disagree about the same underlying
state.

## Integration baseline

This design is rebased conceptually onto `installer-resilience-final` at
`84669bcca702753ac50ccda87ca7fd93142d4e8e`, the current head of PR #975 when
this revision was written.

That baseline introduced a dedicated `auto_cpufreq.lifecycle` orchestration
layer for source install, daemon install/remove, and source updates. Diagnostic
collection must not depend on lifecycle orchestration or call lifecycle
mutators. It may reuse narrowly scoped read-only primitives when they are
already the canonical interpretation of host state; in particular,
`power_state.capture_service_state()` is suitable for systemd observation and
avoids duplicating the project's missing-unit compatibility logic.

The previous experimental diagnostics branches are reference material only.
They must not be replayed wholesale onto the new baseline. Some of those
experiments already contain work that belongs to later stages of this design,
while the current PR has changed the CLI, daemon lifecycle, and power-state
boundaries underneath them.

## Goals

1. Make `auto-cpufreq --debug` observational: collecting a debug report must not
   install, copy, enable, disable, or otherwise mutate system state.
2. Keep `SystemReport` as the shared, lightweight telemetry snapshot used by
   GTK, `--stats`, and `--debug`.
3. Add a structured one-shot `DiagnosticsReport` for deeper information that is
   useful in bug reports but should not be polled every two seconds.
4. Keep collectors independent from presentation. Collectors return structured
   values; CLI/GTK/Urwid format them.
5. Reduce duplicated semantic interpretation so GTK, `--stats`, and `--debug`
   cannot silently disagree about the same state.
6. Make future power-management features naturally expose diagnostic state
   through a shared collector/report layer rather than ad-hoc CLI code.
7. Improve issue reports with relevant kernel/service state while avoiding an
   encyclopedic dump of unrelated system information.

## Non-goals

- Do not change auto-cpufreq power policy, governor selection, turbo policy,
  service lifecycle, installer behavior, updater behavior, or daemon behavior.
- Do not make GTK or `--stats` perform expensive D-Bus/service-manager
  diagnostics on each refresh.
- Do not add complete service-manager diagnostics for every init system in the
  first implementation stage.
- Do not add broad hardware inventory unrelated to power-management decisions.
- Do not use frontend output as an input source; GTK, Urwid, and CLI remain
  presentation layers only.
- Do not refactor PR #975 lifecycle code merely to make diagnostic collection
  look cleaner.

## Architecture

### Fast telemetry: `SystemReport`

`SystemReport` remains the point-in-time snapshot for values that can change
continuously and are useful to more than one frontend.

Examples:

- distro/kernel/architecture/CPU model;
- CPUFreq driver;
- CPU/core frequency, usage, temperature, and online/offline state;
- current governor, EPP, EPB;
- HWP Dynamic Boost and turbo state;
- fan state;
- battery level, AC state, charging state, power, primary battery thresholds;
- platform-profile snapshot;
- load averages and aggregate CPU statistics.

`SystemInfo.generate_system_report()` remains read-only and is the only normal
collection path for these values. GTK and `--stats` continue to consume this
report directly. `--debug` also consumes exactly one `SystemReport` rather than
recollecting the same telemetry through legacy helper commands.

### Deep diagnostics: `DiagnosticsReport`

Add a one-shot structured report in `auto_cpufreq/modules/diagnostics.py`. It
contains information that is valuable in a support report but does not need
continuous polling.

The Stage 1 report should contain:

- effective configuration path/state;
- validated governor and turbo override state;
- Intel P-State implementation details;
- power-management service state on systemd hosts;
- structured battery-device threshold diagnostics that preserve useful
  information currently printed by `battery_get_thresholds()` but not present
  in the primary `BatteryInfo` snapshot.

Later stages extend the same report with CPUFreq policy state, AMD P-State,
Snap/TuneD-PPD service details, and selected external PPD state.

The report is built by one collection entry point, conceptually:

```python
collect_diagnostics(system_report: SystemReport, ...) -> DiagnosticsReport
```

Passing the already-collected `SystemReport` gives diagnostics a consistent
view of AC state and CPU driver without rereading fast telemetry
unnecessarily. Dependencies that otherwise couple diagnostics to mutable or
hard-to-test global state should be injectable at the collector boundary.

### Collector boundaries

Collectors read kernel/sysfs, persisted auto-cpufreq state, service state,
package/runtime information, or stable IPC APIs. They do not print and they do
not write.

Stage 1 examples:

- `read_intel_pstate_info()` -> structured Intel P-State state;
- `read_debug_override()` -> validated persisted override state;
- `read_power_services_info()` -> structured service states;
- `read_battery_threshold_diagnostics()` -> structured per-battery threshold or
  conservation-mode state.

Later examples:

- `read_cpufreq_policies()` -> per-policy CPUFreq state;
- `read_amd_pstate_info()` -> structured AMD P-State state.

Formatting functions accept report objects and return strings or
frontend-specific values. They never recollect state.

### Lifecycle boundary

`auto_cpufreq.lifecycle` owns mutation-oriented source/daemon/update workflows.
`auto_cpufreq.modules.diagnostics` owns observation for support output. The
latter must not call `install_daemon`, `remove_daemon`, `update_source_install`,
Bluetooth setters, competing-service mutators, daemon deployment helpers, or
operation locks merely to collect state.

A lower-level helper can be reused only when its contract is observational. A
representative example is `power_state.capture_service_state()`: it reads
systemd unit properties and already handles historical missing-unit behavior,
so reusing it keeps one canonical interpretation without crossing into
lifecycle mutation.

## Read-only debug invariant

The debug path must not invoke helpers whose purpose is deployment or mutation.

The current call to `cpufreqctl()` in the `--debug` path must be removed.
`cpufreqctl()` can copy `cpufreqctl.auto-cpufreq` into `/usr/local/bin`; that is
not acceptable for a diagnostic command. Shared telemetry already reads the
relevant governor/power state from kernel interfaces.

`battery_get_thresholds()` must also leave the final debug path. It should not
simply be dropped, however: the legacy output can contain per-battery threshold
information and vendor-specific conservation-mode information beyond the
single primary-battery fields in `SystemReport.BatteryInfo`. Stage 1 therefore
preserves useful parity through a structured, read-only battery diagnostic
collector instead of invoking methods that print directly.

The intended invariant is:

> Running `auto-cpufreq --debug` may read system and auto-cpufreq state, but it
> must not create, remove, enable, disable, lock, deploy, or rewrite
> system/runtime configuration as a side effect of producing the report.

Root may remain required temporarily where current readable state or
confinement requires it, but mutation must not be used merely to make
diagnostics possible. Removing the root requirement is a separate change to be
evaluated only after behavior parity and permissions are demonstrated.

## Shared semantics

Presentation strings do not need to be globally identical, but semantic
classification should be centralized.

The current code already demonstrates why: battery and turbo state have been
interpreted separately by GTK, `--stats`, and diagnostics. The same raw state
should first become a canonical semantic value, then each frontend can choose a
label.

Initial targets:

- battery state: charging / discharging / not-charging / unknown;
- AC state: connected / disconnected / unknown;
- turbo state: on / off / driver-managed / unavailable;
- profile selection: charger / battery / charger-with-unknown-AC;
- service state: installed/active/unit-file state/unavailable rather than
  frontend-specific guesses.

Prefer small enums or immutable value objects where they remove repeated
branching. Do not refactor unrelated legacy display code solely for stylistic
uniformity.

## Battery threshold diagnostics

The fast `BatteryInfo` model intentionally describes the primary system
battery. Debug output has a different requirement: when troubleshooting charge
threshold support, multiple batteries or a vendor-specific control may matter.

Stage 1 should therefore collect battery diagnostics independently of the
legacy printing API. For each detected system battery, it should report the
readable threshold interface using the kernel-preferred
`charge_control_start_threshold` / `charge_control_end_threshold` attributes,
while retaining the project's existing compatibility fallback for
`charge_start_threshold` / `charge_stop_threshold` when that is all the kernel
or platform exposes.

Where the existing supported Ideapad path exposes `conservation_mode`, the
collector may report its current read-only value without invoking the setter or
printing device method. Unsupported or unreadable attributes are represented as
unavailable; they do not abort the rest of the debug report.

This collector is diagnostic only. It must not reuse helpers that select files
based on writability, because write permission is not a requirement for
observation and would make the report less accurate on a read-only path.

## CPUFreq policy diagnostics

A later stage of this same architecture should stop assuming CPU0 is sufficient
for debug output.

Linux CPUFreq exposes policy objects under
`/sys/devices/system/cpu/cpufreq/policy*`. Diagnostic collection should be able
to report, per policy:

- related CPUs;
- scaling driver;
- current governor;
- available governors;
- current/scaling minimum and maximum limits where available;
- hardware minimum and maximum limits where available;
- EPP/current available EPP values where exposed by that policy.

The formatter should remain compact when all policies agree and expand only
when policies differ. This makes heterogeneous or multi-policy systems
debuggable without turning every normal report into a large dump.

This is Stage 2 work. The existing experimental branch implementation can be
consulted for lessons learned but should not be copied into Stage 1.

## P-state diagnostics

P-state information should evolve toward a common model rather than adding
unrelated frontend conditionals.

Stage 1 introduces structured Intel P-State observation from the authoritative
`/sys/devices/system/cpu/intel_pstate` interface.

A later stage adds AMD P-State through the common P-state model. AMD exposes a
global `amd_pstate` status with active/passive/guided/disable modes.
Implementation-specific fields may remain optional, but frontends should
ultimately consume one structured P-state diagnostic object rather than infer
implementation solely from a CPUFreq driver label.

## Power-management service diagnostics

Stage 1 should use the project's current structured systemd state reader and
remain strictly observational.

Initial behavior:

- determine whether systemd is PID 1 before querying systemd service state;
- report auto-cpufreq, power-profiles-daemon, TuneD, and TLP where applicable;
- represent an absent unit as not installed;
- represent a genuine service-manager query failure as unavailable without
  aborting the rest of the report.

Later extensions:

- include `tuned-ppd.service` alongside TuneD where applicable;
- make auto-cpufreq daemon status installation-aware for Snap, where the daemon
  is a Snap service rather than `auto-cpufreq.service`;
- on non-systemd PID 1, continue reporting service state as unavailable rather
  than invoking `systemctl` opportunistically;
- do not add complete OpenRC/dinit/runit/s6 support until a small, reliable
  shared abstraction exists.

For Snap, prefer the Snap service-management interface available inside the
snap rather than assuming a host systemd unit name.

## Optional PPD details

Power Profiles Daemon exposes useful diagnostic state beyond whether its
service is running, including active profile, degraded-performance reason, and
profile holds. These are appropriate for `DiagnosticsReport` because they can
explain external constraints or conflicts, but they should be added only after
the report/collector boundary is established.

Do not add D-Bus polling to `SystemReport` or to the GTK refresh loop merely to
support debug output.

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

No arrow points from diagnostics into `lifecycle.py`; diagnostics consumes
observed state, not lifecycle actions.

## Error handling

Diagnostics must fail soft whenever a missing kernel interface, service,
optional package, corrupt override file, or unavailable IPC source is not
itself fatal to auto-cpufreq.

Rules:

- missing optional sysfs attribute -> structured unavailable/unknown state;
- malformed integer/string -> unavailable, not an exception from `--debug`;
- stale/corrupt persisted override -> unavailable;
- unreadable individual battery diagnostic -> unavailable for that field while
  retaining other batteries/fields;
- absent systemd unit -> not installed;
- service-manager/IPC query failure -> unavailable while preserving the rest of
  the report;
- one failed optional diagnostic collector must not prevent the main
  `SystemReport` from being printed.

Do not catch exceptions around normal power-policy code merely to hide real
bugs. Fail-soft behavior here applies to the diagnostic collection boundary.

## Implementation stages

### Stage 1: establish the boundary

- introduce `DiagnosticsReport` and a single collection entry point;
- introduce structured Intel P-State, override, systemd service, and battery
  threshold/device diagnostics on top of the current PR #975 baseline;
- remove `cpufreqctl()` from `--debug`;
- replace direct `battery_get_thresholds()` printing with structured read-only
  battery diagnostics while retaining useful multi-battery/vendor parity;
- reuse one `SystemReport` for the shared fast telemetry printed by `--debug`;
- remove duplicate one-off governor/turbo/charging reads from `--debug` when the
  same semantic state is already represented by the two report objects;
- keep output behavior otherwise compatible enough for issue reports;
- do not import Stage 2 collectors merely because they exist in an older
  experimental branch.

### Stage 2: eliminate important diagnostic blind spots

- collect CPUFreq policies;
- add AMD P-State through the common P-state model;
- add `tuned-ppd`;
- make daemon service diagnostics Snap-aware;
- expose available governors/EPP and effective policy limits.

### Stage 3: targeted external-state diagnostics

- add selected PPD D-Bus details such as degraded state and active holds if they
  materially improve issue diagnosis;
- consider non-systemd service status only if it can be implemented through a
  small shared abstraction without duplicating lifecycle logic.

Each stage must preserve the read-only debug invariant and keep GTK/`--stats`
free of expensive one-shot diagnostics.

## Testing strategy

There is no need to add a large permanent test framework to upstream solely for
this work. During development, use focused temporary tests/workflows or local
scripts to exercise collectors with synthetic roots and injected
service/query functions. Production code must be written after the relevant
failing check exists and has been observed to fail.

Minimum verification for Stage 1:

- `--debug` no longer calls or deploys `cpufreqctl.auto-cpufreq`;
- `--debug` no longer invokes the legacy printing `battery_get_thresholds()`
  path;
- collecting diagnostics does not mutate filesystem/service state;
- corrupt, malformed, or non-string override state is fail-safe;
- Intel P-State active/passive/off states and unavailable attributes are
  represented without exceptions;
- battery threshold diagnostics handle multiple batteries, preferred ABI names,
  compatibility aliases, missing attributes, and supported Ideapad
  conservation-mode observation without writes;
- battery state classification remains consistent for connected,
  disconnected, and unknown AC states;
- non-systemd PID 1 does not trigger systemctl queries;
- missing service units and service query failures are represented without
  aborting the report;
- GTK and `--stats` still build from `SystemReport` with no dependency on
  `DiagnosticsReport`;
- `git diff --check`, Python syntax checks, Linux Build, and Nix Flake remain
  green.

For policy/P-state extensions in later stages, collectors should support
injectable roots so active/passive/guided/mixed-policy cases can be reproduced
without specific hardware.

## Success criteria

The architecture is successful when:

1. a maintainer can ask for `auto-cpufreq --debug` and receive the effective
   auto-cpufreq/kernel/service state needed for common power-management issues
   without a long list of follow-up `cat`/`systemctl` commands;
2. running the report does not change the state it is trying to diagnose;
3. GTK, `--stats`, and `--debug` agree on shared telemetry because they consume
   the same structured snapshot/semantic values;
4. adding a new power-management feature has an obvious diagnostic integration
   point instead of requiring ad-hoc edits to each frontend;
5. expensive or one-shot diagnostics remain isolated from the fast GTK/monitor
   refresh path;
6. PR #975 lifecycle orchestration remains behaviorally unchanged by the
   diagnostic work.
