# Modern Intel Power Policy Architecture

**Status:** Design specification  
**Date:** 2026-09-10  
**Repository:** `AdnanHodzic/auto-cpufreq`  
**Design branch:** `modern-intel-power-policy-design`  
**Development base:** `installer-resilience-final` at `070693c94170f6ac4b1a01e3bcc41d3d798825d2`

## 1. Summary

This document defines a staged architecture for modernizing Intel CPU power management in `auto-cpufreq` without breaking the existing legacy path.

The central design decision is to stop using userspace polling and binary Turbo Boost toggling as the primary fast-control mechanism on modern Intel systems where `intel_pstate` is active with Intel Hardware P-states (HWP). On those systems, the processor and kernel already have lower-latency, higher-fidelity mechanisms for selecting instantaneous performance states.

The proposed architecture therefore separates two control domains:

- **Fast control:** Intel HWP, EPP, the Linux scheduler, and the processor choose instantaneous performance states.
- **Slow policy control:** `auto-cpufreq` selects the energy/performance preference and, in a later opt-in stage, a package power envelope through the Linux Powercap/RAPL ABI.

The legacy algorithm remains available for systems that do not meet the modern Intel capability gate.

The implementation is deliberately split into five incremental delivery stages. The first two are behavior-neutral infrastructure, the third changes HWP policy only, the fourth removes CPU-load polling only from the modern path, and the fifth introduces optional RAPL writes together with ownership tracking and failsafe recovery.

## 2. Problem Statement

The current `auto-cpufreq` policy periodically samples CPU utilization, load average, and temperature, then uses those measurements to enable or disable Turbo Boost. This approach has two architectural limitations on modern Intel systems.

First, the sampling interval is orders of magnitude slower than the CPU scheduler and Intel HWP control loop. A Python daemon cannot reproduce the processor's internal knowledge of performance demand, residency, hardware limits, and short-lived workload transitions.

Second, Turbo Boost is a coarse binary permission, not a complete power policy. Disabling turbo to save energy removes short high-performance bursts as well as sustained high-power operation. That conflates two separate questions:

1. May the processor use turbo performance states when they are useful?
2. How much power may the package consume over a sustained or short-term interval?

Modern Intel systems already expose separate mechanisms for these questions:

- `intel_pstate` active mode with HWP and EPP for performance-versus-efficiency preference;
- `no_turbo` for turbo permission;
- HWP Dynamic Boost for short-lived minimum-performance elevation after selected I/O wakeups;
- Linux Powercap/RAPL for package energy telemetry and, where supported and writable, named power constraints such as `long_term` and `short_term`.

The architecture should use these mechanisms according to their intended responsibilities instead of recreating a frequency governor in userspace.

## 3. Goals

### 3.1 Correctly identify modern Intel capabilities

`auto-cpufreq` must be able to discover, report, and reason about:

- `intel_pstate` mode;
- HWP availability;
- EPP availability and accepted values;
- HWP Dynamic Boost availability and current state;
- Turbo control availability and current state;
- CPUFreq policies and their per-policy limits;
- hybrid CPU topology where exposed by the kernel;
- Powercap control types, zones, subzones, energy counters, and named constraints;
- thermal throttling telemetry by physical package where exposed.

Discovery is read-only in the first implementation stage.

### 3.2 Introduce a modern Intel HWP policy without breaking legacy systems

For systems that satisfy the modern capability gate, the default automatic policy should delegate fast frequency selection to Intel HWP instead of toggling turbo according to CPU-load thresholds.

Systems that do not satisfy the capability gate must continue to use the existing legacy algorithm.

### 3.3 Eliminate CPU-load polling from the modern path

Once the modern HWP policy is active, `auto-cpufreq` should no longer wake periodically to inspect CPU utilization or load average for turbo decisions.

This requirement applies only to the modern policy. The legacy backend may retain its existing periodic tick because its policy depends on sampled load and temperature.

### 3.4 Add robust energy telemetry

Power telemetry must use the kernel Powercap ABI, including `energy_uj` and `max_energy_range_uj`, with wraparound-safe differential sampling and a monotonic clock.

### 3.5 Add optional RAPL envelopes only after the policy architecture is stable

RAPL writes are not part of the initial behavior change. They are introduced only in the final stage, behind an explicit opt-in configuration option, together with verification, ownership tracking, runtime state persistence, and crash recovery.

## 4. Non-Goals

The following are explicitly outside the initial project scope:

- AMD power policy changes;
- direct MSR writes;
- undervolting or voltage offsets;
- `ryzenadj` or vendor-specific out-of-kernel power controls;
- adaptive RAPL control based on PSI, CPU utilization, or temperature;
- per-application process scanning;
- automatic PL1/PL2 values inferred from CPU SKU tables;
- automatic acoustic tuning of RAPL time windows;
- automatic GPU-aware power-cap adjustment inferred from package-minus-core energy;
- automatic USB-PD-aware RAPL scaling;
- D-Bus profile leases for applications;
- changing PL3/PL4 or other peak/current-protection limits;
- replacing scheduler or Intel Thread Director decisions;
- assigning different EPP values to P-cores and E-cores by default;
- introducing a mandatory upstream unit-test framework where the project currently has none.

These topics may be revisited independently after the modern Intel path is validated.

## 5. Design Principles

### 5.1 Hardware selects the instant; auto-cpufreq selects the policy

For modern Intel HWP systems:

> `auto-cpufreq` selects the operating policy and optional power envelope; Intel HWP selects the instantaneous performance state.

The daemon must not attempt to reproduce a hardware P-state controller in Python.

### 5.2 Capability-based behavior, not CPU-model allowlists

The modern path is selected by observable kernel capabilities rather than CPU marketing names or model-number lists.

A compatible Intel processor enters the modern path only if the required ABI is actually exposed and internally consistent.

### 5.3 Unknown remains unknown

The implementation must not manufacture topology, ownership, package IDs, domain meanings, or hardware limits when the relevant kernel interface is absent or unreadable.

Examples:

- failure to read `physical_package_id` must not silently become package `0`;
- a numeric Powercap directory suffix must not be interpreted as `core`, `uncore`, or `dram` without reading `name`;
- `constraint_0` must not be assumed to mean PL1 without reading `constraint_0_name`;
- a changed value after resume must not automatically be attributed to firmware.

### 5.4 Events invalidate cached context; sysfs is authoritative

A `power_supply` uevent is a notification that relevant state may have changed. It is not the authoritative state itself.

On a relevant event, the daemon re-reads the canonical sysfs interfaces, rebuilds its power context, compares it with the previous context, and applies a policy only if required.

This makes duplicate, partial, or coalesced events harmless and provides a clean path for future USB-C/PD support.

### 5.5 Explicit user configuration remains authoritative

The modern automatic defaults must not silently override deliberate user settings.

In particular:

- an explicitly configured `governor = performance` remains respected;
- an explicitly configured `scaling_max_freq` remains respected;
- `turbo = always` and `turbo = never` retain their explicit meanings;
- only `turbo = auto` receives modern-backend-specific semantics.

### 5.6 Writes require verification

Any active write introduced by this project must be followed by readback whenever the ABI permits it.

A successful write syscall alone does not prove the requested state became effective.

### 5.7 Ownership must be proven, not assumed

A value is considered controlled by `auto-cpufreq` only while the currently observed value still matches the last value the daemon successfully wrote and verified.

Any unexplained drift invalidates ownership instead of triggering a write war.

## 6. Terminology

### 6.1 Legacy policy

The existing `auto-cpufreq` policy that periodically samples load, CPU use, and temperature and uses those values to make turbo/governor decisions.

### 6.2 Modern Intel HWP policy

The new policy backend used only when the modern capability gate is satisfied. It delegates fast P-state selection to Intel HWP and uses EPP for the energy/performance preference.

### 6.3 Power context

A normalized view of the system's current external-power and battery state used by the policy layer. In the initial project this is intentionally small: external power versus battery operation. More detailed source capability and battery tiers are future extensions.

### 6.4 Powercap zone

A power zone exposed by the Linux Powercap framework and identified by its `name` attribute.

### 6.5 Constraint

A named Powercap limit described by `constraint_X_name`, `constraint_X_power_limit_uw`, and, where applicable, `constraint_X_time_window_us`.

### 6.6 Ownership

A constraint is considered owned by `auto-cpufreq` only when the daemon has recorded the original value, has successfully written and verified a new value, and the currently observed value still equals the last value written by `auto-cpufreq`.

## 7. Capability Model

The first implementation should expose a compact capability object rather than spread path checks throughout `core.py`.

Conceptually, the Intel discovery layer should be able to report:

```text
Intel CPU policy
----------------
intel_pstate status: active | passive | off | unavailable
HWP eligible: yes | no
EPP: available | unavailable
HWP Dynamic Boost: available | unavailable
Turbo control: available | unavailable

CPUFreq policies
----------------
policy0:
  related CPUs: ...
  cpuinfo min/max: ...
  scaling min/max: ...
  governor: ...
  EPP: ...

Powercap
--------
control type: ...
zone: package-0
  energy counter: ...
  max energy range: ...
  constraints:
    long_term: ...
    short_term: ...

Thermal throttle telemetry
--------------------------
package 0:
  count: ...
  total time: ...
  max event time: ...
```

The exact classes are an implementation detail for the later implementation plan. The boundaries are not: discovery, normalized data, formatting/reporting, policy decision, and actuator writes remain separate concerns.

### 7.1 Modern Intel capability gate

The new active policy requires all of the following:

1. CPU scaling is controlled by `intel_pstate` in `active` mode.
2. HWP is demonstrably available through active-mode HWP interfaces.
3. EPP is exposed through the CPUFreq policy interface.
4. The system provides a usable CPUFreq policy view.

The presence of `/sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost` is a strong positive HWP signal, but Dynamic Boost itself is not required for the modern policy. Systems without Dynamic Boost may still use HWP/EPP if HWP availability is otherwise established reliably.

If the gate cannot be established confidently, the system remains on the legacy path.

## 8. Delivery Stage 1: Read-Only Intel Power Discovery and Telemetry

### 8.1 Scope

The first change introduces a focused module, provisionally `auto_cpufreq/modules/intel_power.py`, containing read-only Intel power discovery and telemetry.

It must not change governor, EPP, turbo, frequency limits, RAPL constraints, platform profile, or service behavior.

### 8.2 Sysfs roots must be injectable

The module should not hardcode every path inside each method. At minimum, the CPU sysfs root and Powercap root should be injectable, with production defaults such as:

```text
/sys/devices/system/cpu
/sys/class/powercap
```

This makes the module testable with synthetic trees without forcing the project to introduce a new upstream test framework in the same change.

Development-only fixtures may be used in the feature branch to simulate modern Intel, multiple Powercap controllers, missing Powercap, unreadable topology, and legacy systems.

### 8.3 CPUFreq policy discovery

Discovery must enumerate CPUFreq policy directories when available instead of assuming that `cpu0` represents the entire processor.

For each policy, collect only attributes actually exposed by the kernel, such as:

- related/affected CPUs;
- scaling driver;
- scaling governor;
- `cpuinfo_min_freq` and `cpuinfo_max_freq`;
- `scaling_min_freq` and `scaling_max_freq`;
- EPP and available EPP preferences when present.

Hybrid topology information is diagnostic metadata. The modern policy must not create separate default EPP preferences for P-cores and E-cores.

### 8.4 Powercap discovery

Discovery must enumerate the Powercap hierarchy rather than assume one directory layout.

For every discovered power zone:

- read `name` when available;
- read `energy_uj` when available;
- read `max_energy_range_uj` when available;
- enumerate every `constraint_X_name` that exists;
- associate power limits and time windows with the matching named constraint;
- record min/max constraint attributes if exposed;
- keep control types and zones distinct rather than merging them solely because names match.

The code must tolerate systems with no Powercap interface and systems with multiple Intel-related Powercap control trees or implementations.

### 8.5 Energy sampling

Average power is derived from energy deltas:

```text
Pavg = delta_energy / delta_time
```

Requirements:

- use `time.monotonic_ns()` for elapsed time;
- use the zone's actual `max_energy_range_uj` rather than assuming a raw 32-bit microjoule counter;
- handle one wrap between consecutive samples;
- discard an anomalous wrapped sample if the range is unavailable;
- document that the calculation assumes at most one wrap between two samples;
- never reset `energy_uj` as part of normal telemetry;
- keep energy delta and elapsed time available internally rather than returning only a formatted watt value.

### 8.6 Thermal throttle telemetry

When `/sys/devices/system/cpu/cpu*/thermal_throttle/` is available, report package-level metrics without multiplying the same package event across logical CPUs.

Package identity comes from topology. If package identity cannot be established, the implementation reports the metric as unavailable or non-deduplicated rather than inventing package `0`.

Prefer package-level reporting of:

- `package_throttle_count`;
- `package_throttle_total_time_ms`;
- `package_throttle_max_time_ms`.

No automatic policy change is driven by these values in this project.

### 8.7 User-facing reporting

The first change extends `--debug` and, where appropriate, `--stats` with modern Intel capability and energy information.

The output distinguishes:

- unsupported;
- unavailable because an attribute is absent;
- unknown because an attribute could not be read;
- available and observed.

No warning should imply a fault merely because optional hardware functionality is absent.

## 9. Delivery Stage 2: Policy Backend Abstraction

### 9.1 Purpose

The second change introduces a backend boundary between policy selection and policy execution without changing behavior.

The current implementation mixes hardware discovery, power-source selection, policy decision logic, and actuator writes in `core.py`. The new boundary allows a caller to select a policy backend based on discovered capabilities.

Conceptually:

```text
select_policy(capabilities)
    -> ModernIntelHwpPolicy
    -> LegacyPolicy
```

This stage leaves real hardware behavior equivalent to the current implementation. `ModernIntelHwpPolicy` may exist structurally but does not yet introduce the new automatic policy.

### 9.2 Backend scheduling contract

The policy interface can express whether it requires a periodic policy tick.

For example:

```text
LegacyPolicy
  periodic tick required: yes

ModernIntelHwpPolicy
  periodic tick required: no
```

The event-loop implementation in Stage 4 consumes this generic scheduling requirement rather than containing `if modern_intel:` branches.

This keeps the architecture extensible to future AMD work without implementing AMD in this project.

## 10. Delivery Stage 3: Modern Intel HWP Policy

### 10.1 Automatic defaults

For modern Intel HWP systems using automatic settings, the default policy is:

**On external power:**

```text
intel_pstate algorithm: powersave
EPP: balance_performance
Turbo: allowed / hardware-managed
HWP Dynamic Boost: enabled when available
```

**On battery:**

```text
intel_pstate algorithm: powersave
EPP: balance_power
Turbo: allowed / hardware-managed
HWP Dynamic Boost: disabled by default when available, but configurable
```

The user-facing documentation describes these as a balanced/efficiency policy rather than implying that the kernel `powersave` algorithm means a fixed low-frequency mode.

Dynamic Boost on battery remains configurable because its energy-versus-responsiveness trade-off is empirical and platform-dependent.

### 10.2 Uniform EPP

The automatic modern policy applies the same EPP preference across CPUFreq policies unless the user has explicitly configured otherwise through an existing supported mechanism.

The policy does not assign different default EPPs to P-cores and E-cores. Scheduler migration and Intel hybrid scheduling mechanisms remain authoritative.

### 10.3 Turbo compatibility semantics

Existing configuration values are preserved:

```text
turbo = always
  -> allow turbo

turbo = never
  -> disable turbo

turbo = auto
  -> Modern Intel HWP: allow hardware-managed turbo
  -> Legacy: retain existing load/temperature-based automatic behavior
```

Internally, `auto`, `always`, and `never` remain distinct user intents even if `auto` and `always` both result in turbo permission on the modern backend.

### 10.4 Explicit governor configuration

If the user explicitly configures:

```ini
governor = performance
```

that choice is respected.

The debug/reporting interface should explain that, under active `intel_pstate` with HWP, the `performance` algorithm is intentionally aggressive and does not behave like the balanced automatic policy.

The implementation must not silently replace an explicit `performance` governor with `powersave`.

### 10.5 CPUFreq limit reconciliation

Enabling turbo permission is not sufficient if CPUFreq policy limits remain below the intended hardware-managed range.

However, the modern policy must not blindly write every `scaling_max_freq` to `cpuinfo_max_freq`.

The policy reconciles frequency bounds according to these rules:

1. Preserve explicit user-configured `scaling_min_freq` and `scaling_max_freq`.
2. Work per CPUFreq policy, not by assuming CPU 0 represents all CPUs.
3. Respect each policy's hardware min/max bounds.
4. When no explicit user frequency cap is configured, ensure stale limits created by previous automatic turbo suppression do not unintentionally prevent hardware-managed boost.
5. Use readback to report effective limits after reconciliation.
6. Do not overwrite a lower external limit merely because it differs from the hardware maximum unless ownership or existing `auto-cpufreq` frequency-policy semantics justify the write.

The implementation plan derives the exact reconciliation algorithm from current `set_frequencies()` behavior and the `intel_pstate` ABI rather than adding an unconditional maximum-frequency reset.

### 10.6 Legacy behavior

If the modern capability gate fails, Stage 3 routes the system through the legacy backend with behavior equivalent to the current implementation.

This includes existing periodic load/temperature decisions and automatic turbo handling.

## 11. Delivery Stage 4: Event-Driven Scheduling for the Modern Policy

### 11.1 Scope

Stage 4 changes scheduling, not power semantics.

It removes CPU-load polling from the modern HWP backend while retaining the periodic legacy tick for `LegacyPolicy`.

### 11.2 Unified event loop

A single daemon scheduler handles both event-driven descriptors and timer deadlines.

`selectors.DefaultSelector()` is a suitable standard-library abstraction on Linux, but the design requirement is the behavior rather than the exact class.

The scheduler computes its next timeout from deadlines rather than hardcoded modern/legacy values.

Conceptually:

```text
next timeout = minimum of:
  policy periodic deadline, if any
  watchdog health deadline, if enabled
  internal maintenance deadlines, if any
```

Therefore:

- `LegacyPolicy` contributes an approximately existing periodic policy deadline;
- `ModernIntelHwpPolicy` contributes no periodic load-policy deadline;
- the watchdog contributes its own health deadline.

A service watchdog must never pair with a selector timeout longer than the watchdog deadline. Watchdog timing should be derived from the systemd-provided watchdog configuration rather than an unrelated hardcoded constant.

### 11.3 Native power-supply uevents

The initial event source uses Linux kobject uevents through a native Netlink socket, avoiding a new `pyudev` dependency.

Relevant `SUBSYSTEM=power_supply` events invalidate the cached power context.

The event payload is not treated as the complete state. On receipt, the daemon re-reads the established power-supply sysfs logic and constructs a fresh `PowerContext`.

Duplicate events that produce the same normalized context must not cause repeated actuator writes.

The implementation must remain able to rebuild current state after event loss or socket recreation.

### 11.4 Configuration watching

The project already uses a separate `pyinotify.ThreadedNotifier` for configuration changes. Stage 4 does not rewrite that mechanism merely to claim a completely thread-free daemon.

Configuration-watch refactoring is outside scope unless required by the policy-backend integration.

### 11.5 Systemd watchdog

The existing service already restarts on failure. Stage 4 adds watchdog health reporting only if it can reflect progress of the main event loop.

A completely independent thread that continues to send watchdog heartbeats while the policy loop is dead is not acceptable.

### 11.6 Suspend/resume integration

Direct logind D-Bus integration is not required for Stage 4.

The project currently has no mandatory D-Bus client dependency, and this design does not justify implementing the D-Bus wire protocol manually solely to avoid a dependency.

Suspend/resume event integration is deferred until a small, maintainable D-Bus strategy is selected. A later implementation follows these rules:

- resume invalidates hardware capability and ownership assumptions;
- hardware state is re-read before any write;
- a changed value does not prove firmware ownership or `auto-cpufreq` ownership;
- no write is allowed solely because a post-resume value differs from the pre-suspend value.

This deferral is safe because Stage 4 introduces no RAPL writes.

## 12. Delivery Stage 5: Optional RAPL Power Envelopes and Failsafe Recovery

### 12.1 Opt-in requirement

Active RAPL power-envelope control is initially opt-in.

A configuration switch similar to the following is required:

```ini
[intel_power]
enable_rapl_envelopes = true
```

The exact option name may be refined in the implementation plan, but the behavior is fixed: existing installations do not receive automatic package power-limit writes merely by upgrading.

### 12.2 User-configured envelopes

The first RAPL-control version does not invent universal PL1/PL2 values.

Users explicitly configure package power limits for external-power and/or battery contexts. Human-readable configuration values may be expressed in watts, but internal Powercap writes use strictly converted microwatts.

The implementation validates:

- numeric parsing;
- dimensional conversion;
- kernel-reported min/max limits when available;
- requested constraint availability;
- current control ownership;
- actual accepted value by readback.

### 12.3 Named constraints only

RAPL constraints are selected by `constraint_X_name`.

The implementation must not assume:

```text
constraint_0 == PL1
constraint_1 == PL2
```

Instead it maps names such as `long_term` and `short_term` when the platform exposes them.

Unknown or unnamed constraints are reported but not automatically controlled.

### 12.4 Time windows

RAPL time windows are discovered and reported.

The initial active policy does not automatically retune time windows for fan acoustics or workload shape.

If a future user-configurable time-window setting is introduced, every write must be verified by readback because hardware may quantize or ignore unsupported values.

### 12.5 Firmware locks and failed writes

Writable file mode alone is not proof that hardware accepts a RAPL write.

The write path handles failures such as `PermissionError`, `EACCES`, or other `OSError` values gracefully, reports the failure, and leaves the daemon running.

A failed or unverifiable write does not establish ownership.

### 12.6 Ownership state model

Before the first successful write to a controlled constraint, record the current value as `original_value`.

After a successful write and readback, record the effective value as `last_written_by_us`.

A constraint remains owned only while:

```text
current_value == last_written_by_us
```

If:

```text
current_value != last_written_by_us
```

ownership is considered lost because another agent, firmware, EC, thermal manager, or platform mechanism may have modified the value.

The daemon must not enter a write war to restore its previous value automatically.

### 12.7 Runtime state persistence

Ownership data is stored in an ephemeral runtime file under:

```text
/run/auto-cpufreq/
```

The service should use systemd `RuntimeDirectory=auto-cpufreq` where applicable rather than relying on ad-hoc directory creation.

The ownership state file is written atomically, for example through a temporary file followed by `os.replace()`.

The state file contains only information needed for safe recovery, such as:

```text
controller identity
zone identity
constraint identity
original value
last value written by auto-cpufreq
optional original/last-written time window when controlled
```

It is not a permanent configuration file.

### 12.8 Restore-owned-state helper

A minimal failsafe helper restores only state that can still be proven to belong to `auto-cpufreq`.

For each recorded item:

1. read the current sysfs value;
2. if the current value equals `last_written_by_us`, restore `original_value`;
3. if the current value differs, do not overwrite it;
4. report failures without cascading into unrelated writes;
5. retire the processed runtime ownership record appropriately.

The helper is independent from the main CLI initialization path and avoids unnecessary imports or side effects.

The service invokes the helper using the same installed runtime context as `auto-cpufreq`; it must not assume `/usr/bin/python3` can import the installed package when a source installation uses a virtual environment.

No arbitrary startup-time target is part of the design requirement. Reliability and minimal dependency surface are the goals.

### 12.9 Resume and hibernation ownership rule

If future suspend/resume integration observes that a RAPL constraint differs from `last_written_by_us` after resume, previous ownership is invalidated.

The newly observed value becomes an external baseline. The daemon may reacquire control only according to normal conservative acquisition rules.

A configured target that would raise a newly observed external limit must not be applied automatically merely because the daemon controlled that constraint before suspend or hibernation.

This prevents a restored userspace image from overriding firmware or platform limits re-established during a real power cycle.

## 13. Configuration Compatibility

### 13.1 Existing turbo values

No existing `turbo` value is rejected.

The modern backend changes only automatic handling:

```text
Modern Intel HWP + turbo=auto
  -> hardware-managed boost remains allowed

Legacy + turbo=auto
  -> existing automatic turbo algorithm
```

A concise diagnostic message may explain this mapping when relevant.

### 13.2 Explicit frequency limits

Existing explicit `scaling_min_freq` and `scaling_max_freq` configuration remains authoritative and is incorporated into the CPUFreq reconciliation step.

### 13.3 Explicit governor values

Explicit governors remain authoritative. The automatic modern defaults apply only when the user has not deliberately chosen a different governor.

### 13.4 RAPL configuration

RAPL envelope configuration is additive and disabled by default in the first release that supports writes.

No existing configuration file is required to add the new section to retain current behavior.

## 14. Validation Strategy

### 14.1 Hardware-independent validation

The Intel discovery module is designed so its sysfs roots can be redirected to synthetic directory trees.

This enables local development checks for:

- modern Intel HWP + Powercap;
- multiple Powercap control trees;
- missing Powercap;
- missing HWP;
- unreadable topology;
- named and unnamed constraints;
- one-wrap energy telemetry;
- absent `max_energy_range_uj`;
- unsupported optional throttle metrics.

The upstream repository currently does not run a pytest suite in its Linux workflow. This design therefore does not require introducing a new mandatory test framework as part of Stage 1. If maintainers later request automated unit tests, the injectable-root architecture already supports them.

### 14.2 Hardware A/B validation

Behavior-changing stages are validated on real Intel HWP hardware against the current baseline.

Measurements compare the same machine under controlled conditions, including the same:

- kernel;
- firmware settings;
- display conditions;
- external power state;
- platform profile;
- background services;
- workload;
- measurement tooling.

`turbostat` is appropriate when the requested counters are available on the target platform.

The experiment must not assume universal targets such as 80-90% package C10 residency. Instead, compare baseline and experimental distributions on the same machine.

Recommended categories:

1. **Idle:** package power and deep package C-state residency.
2. **Short deterministic CPU burst:** completion time and energy used.
3. **Medium deterministic workload:** completion time, energy, and average package power.
4. **Sustained multicore workload:** completion time, sustained power, and thermal/throttle telemetry.

Use repeated runs and summarize median plus spread rather than relying on a single measurement.

The central result is not merely lower watts. It is the trade-off among:

```text
energy per task
completion time
average package power
idle package power
responsiveness
thermal/throttle behavior
```

### 14.3 Acceptance criteria for the modern HWP policy

Before optional RAPL control is considered successful, the HWP-only policy should demonstrate that it can:

- preserve or improve short-workload responsiveness;
- avoid a material regression in deterministic workload completion time;
- remove periodic load-based turbo decisions from the modern path;
- reduce or at least not worsen idle overhead attributable to the daemon;
- remain stable across repeated external-power transitions;
- fall back cleanly when a required HWP capability is unavailable.

No single benchmark metric is sufficient by itself.

## 15. Failure and Safety Model

The project follows conservative failure semantics.

### 15.1 Discovery failure

If required capability discovery fails, remain on or return to the legacy behavior rather than partially enabling the modern backend.

### 15.2 Optional telemetry failure

Missing energy or throttle telemetry must not disable an otherwise valid HWP policy.

### 15.3 Active write failure

A failed EPP, governor, CPUFreq, or future RAPL write is reported and verified where possible. The daemon must not crash solely because optional hardware rejected a policy write.

### 15.4 External drift

Unexpected external changes invalidate ownership. They do not trigger an unconditional restore loop.

### 15.5 Crash recovery

RAPL write support is not considered complete until ownership state and the minimal restore helper are delivered with it.

## 16. Deferred Extensions

The following extensions are intentionally deferred until the initial architecture is validated:

### 16.1 USB-C / USB-PD source awareness

Future power context may incorporate negotiated or reported external-source capability, including cases where the battery discharges while external power is online.

The future implementation must use exposed capabilities rather than assume every PD implementation presents the same sysfs layout.

### 16.2 Battery tiers

Future policy may use `capacity_level` where reliably exposed, with a graceful numeric fallback when appropriate. Universal electrochemical thresholds are not part of this design.

### 16.3 D-Bus profile leases

A later desktop integration may expose temporary policy leases for GameMode or other clients instead of scanning process names.

### 16.4 Adaptive acoustic or thermal control

RAPL time-window tuning, thermal feedback, and acoustic profiles require platform measurements and are not part of the initial controller.

### 16.5 AMD backend

The policy-backend abstraction is intentionally compatible with a future AMD implementation, but no AMD control behavior is specified here.

## 17. Delivery Sequence

The intended incremental delivery sequence is:

```text
Stage 1 — Intel Power Discovery & Telemetry
          read-only, no behavior change

Stage 2 — Policy Backend Abstraction
          Modern vs Legacy boundary, no behavior change

Stage 3 — Modern Intel HWP Policy
          HWP/EPP + hardware-managed boost, Legacy unchanged

Stage 4 — Event-Driven Modern Scheduling
          Netlink power events, no load polling for Modern,
          Legacy retains periodic tick, watchdog health

Stage 5 — Optional RAPL Envelopes
          explicit opt-in, named constraints, ownership,
          atomic runtime state, restore-owned-state failsafe
```

Each stage should be independently reviewable and should not depend on speculative future mechanisms.

## 18. Core Invariants

The implementation must preserve the following invariants across all stages:

1. **Legacy support remains functional.** Modernization is additive, not a replacement for unsupported hardware.
2. **No CPU-model allowlist controls eligibility.** Capability discovery decides.
3. **`turbo=auto` does not disable boost by load threshold on eligible Modern Intel HWP systems.**
4. **Explicit user configuration is not silently discarded.**
5. **Powercap zones and constraints are identified through ABI-provided names, not numeric positions.**
6. **Unknown state is never fabricated into a confident value.**
7. **Modern policy does not require periodic CPU-load polling.**
8. **RAPL writes are opt-in in the first active implementation.**
9. **Every controlled RAPL value is read back after writing.**
10. **Unexpected drift invalidates ownership instead of causing a write war.**
11. **Failsafe recovery restores only values still provably owned by `auto-cpufreq`.**
12. **Resume or hibernation never grants automatic permission to raise externally re-established limits.**

## 19. Decision Summary

The architecture deliberately makes `auto-cpufreq` less responsible for instantaneous CPU control on modern Intel systems and more responsible for selecting a coherent system policy.

The core model is:

```text
System context changes
        |
        v
auto-cpufreq selects policy
        |
        +--> EPP / governor policy
        +--> Turbo permission
        +--> HWP Dynamic Boost policy
        +--> optional RAPL envelope later
        |
        v
Intel HWP selects instantaneous performance
```

For legacy systems, the existing polling-driven control remains available.

This separation is the fundamental architectural boundary for the project and should remain stable even if future work adds USB-PD awareness, application leases, AMD support, or adaptive power-envelope policies.
