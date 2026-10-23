# Intel RAPL: configurable limits, GTK controls, and shared telemetry

Status: implementation plan (no runtime behavior change)  
Base: `lucasleocs/auto-cpufreq@070693c94170f6ac4b1a01e3bcc41d3d798825d2`  
Feature branch: `feat/intel-rapl-gtk-stats`  
Date: 2026-10-23

## Goal

Allow users of compatible Intel CPUs to configure Linux Powercap RAPL **package** long-term and short-term limits for charger and battery profiles through the existing `.conf` file only. Present live RAPL information through read-only GTK and terminal `--stats` views. Maintain compatibility when RAPL is absent or disabled; no GUI editor or privileged GTK operation is in scope.

## Prior work to reuse

Do not implement the RAPL writer from scratch without evaluating these existing fork branches:

- `rapl-power-limits`: early opt-in config and `auto_cpufreq/powercap.py` implementation, with discovery, verified writes and telemetry.
- `intel-power-discovery-telemetry`: `modules/intel_power.py` with structured read-only Powercap discovery and unified SystemReport collection.
- `modern-intel-rapl-envelopes`: `modules/intel_rapl.py` (capability-based discovery, persistent ownership, verified apply/restore and drift detection), a dedicated restore entrypoint, systemd fail-safe, and documented physical i3-1315U tests.

Important: the modern Intel branch also carries substantial HWP policy and scheduler changes. Port or adapt **minimal reviewed commits** rather than merging the entire experimental branch blindly.

## Upstream synchronization gate

Fork `master` and `AdnanHodzic/auto-cpufreq@5a1683037a583785f802b5d039022127b9b154cd` diverge: at this snapshot, the fork has seven commits absent upstream and upstream has twelve absent from the fork. Never reset or force-push fork `master` to upstream. Perform an explicit merge on an isolated staging branch/worktree; review conflicts in installer, update/rollback, CLI, core, README and Nix packaging; run CI and installation/rollback smoke tests; only then update `master` by an ordinary merge/fast-forward. Rebase the feature branch onto the verified synchronized base. Do not silently change the live installed daemon during source synchronization.

## Interface and backend design

1. **Read-only capability discovery.** Use the kernel Powercap sysfs ABI, not processor model allowlists or direct MSR writes. Enumerate legitimate `intel-rapl` and `intel-rapl-mmio` control types and package zones, deduplicate symlink aliases and prevent sysfs path escapes. Enumerate actual `constraint_X_name` values (`long_term`, `short_term`) rather than relying on fixed indices. Report unsupported/unreadable/non-writable/locked constraints accurately. Keep control types independent.
2. **Safe opt-in policy.** Default to no RAPL writes. Parse positive finite decimal watts precisely into integer microwatts for independent `[charger]` and `[battery]` targets. Omission means no forced target. Never infer a watt limit from CPU SKU or TDP; never modify time windows, subzones, DRAM, peak-power or voltage settings. A compatible Intel Powercap target should not be unnecessarily coupled to the presence of HWP; separate RAPL capability gating from HWP governor selection. Preserve the legacy policy when RAPL is unavailable.
3. **Ownership, firmware coexistence and recovery.** Reuse the tested conservative controller: initial acquisition never increases an observed external limit, including a stricter independent MMIO limit; subsequent changes are bounded by the originally captured baseline. Validate minimum/maximum metadata without incorrectly treating inconsistent reported maxima as authority to raise a limit. Verify sysfs readback with hardware quantization handling; reject malformed values. Atomically persist owned limits under root-owned runtime storage; restore only if the current value still equals the last value written by this service; on drift, yield to firmware/thermald instead of creating a write war. Provide crash/stop restoration through a verified systemd integration. Non-systemd or confined installations should remain read-only until an equivalent safe restoration path exists.
4. **Unified status representation.** Expose package-zone status in a shared, read-only snapshot: CPU/control type, package, named constraint, current measured cap, configured target (if any), original baseline while owned (if safely available), write capability, and actionable error/status. Preserve distinct meanings of 'configured', 'applied', 'observed', 'overridden externally' and 'unsupported'. Treat measured package energy (if available) as separate from power limits; estimate watts only from monotonic energy deltas with wrap handling.
5. **Terminal reporting.** Extend the existing `SystemReport` / `format_intel_power_summary` / Urwid `SystemMonitor` path instead of reading sysfs again in UI code. Show Intel RAPL package limits and reasons for disabled/unavailable control in `--stats`, and read-only views for `--monitor` and `--live`. Avoid clipping in narrow terminals, unstable row order, duplicated zones and excessive sysfs sampling.
6. **GTK read-only reporting.** Add a compact Intel RAPL / Package Power Limits section to `auto_cpufreq/gui/app.py`, populated with the same `SystemReport` snapshot used by the terminal. Expose live caps per independent control type, package, and named constraint, alongside clearly distinguished configured AC/battery targets where available. Explain unsupported, unreadable, externally changed, and unconfigured states. Never write sysfs, config files, or trigger privilege elevation from GTK.
7. **File-based configuration.** Keep `[intel_power]`, `[charger]`, and `[battery]` options in the existing configuration file. Verify the authoritative daemon config path and reload behavior. Do not add an editing dialog, a GUI-to-root CLI helper, or a privileged config API.
8. **README and examples.** Document requirements, opt-in default, explicit AC/battery example with non-recommended illustrative watt numbers, config locations and permissions, MSR/MMIO differences, conservative no-raise behavior, power-vs-consumption distinction, rollback, firmware/thermald interaction, GUI use, `--stats` sample, and restrictions (NixOS/Snap/non-systemd). Maintain any Nix config example if exposed in that packaging path.

## Suggested implementation sequence

- **A — Sync gate:** upstream diff/conflict audit, isolated merge, CI, safe `master` update.
- **B — Backend:** adapt tested RAPL discovery, policy, persistence and restoration as focused commits. Preserve capability gating and the existing legacy path. No GTK edits yet.
- **C — Shared observability:** one common snapshot and formatter for CLI/Urwid/GTK. Make reporting read-only first.
- **D — Read-only GTK:** compact status widget, accessible labels, background refresh and capability-aware states.
- **E — Configuration documentation:** update file-based options, selected config path, daemon reload behavior, and packaging-specific limitations.
- **F — Docs and regression:** example configs, README, packaging/service validation, and manual UI/hardware gates.

## Required tests / acceptance gates

- Fake-sysfs tests for no RAPL, read-only RAPL, MSR-only, MMIO-only, both, multiple packages, aliases, subzones, reordered constraints, partial/missing attributes and symlink escapes.
- Decimal parsing: 0/negative, NaN/Infinity, bad precision, overflow, unsatisfied limits, missing sections and unchanged defaults.
- No writes when disabled, running read-only modes, unsupported hardware, missing systemd fail-safe or insufficient permission.
- Verify and restore after partial write failure, quantized readback, different observed maximum, external drift, AC/battery transitions, config disable, service stop, crash, and unexpected reboot/restart.
- Ensure report consistency across GTK, CLI `--stats` and Urwid; no stale 'applied' state when firmware modifies a cap; no root operations from GUI refresh worker.
- GTK display without main-thread blocking; correct handling of unsupported/unconfigured states, external limit changes, refreshing, and `SystemReport` errors. Confirm GTK never elevates privileges or writes RAPL/config files.
- File-based config tests for invalid options, correct daemon-selected path, config notifications, and policy transitions.
- Compatibility checks for non-Intel CPU and Intel without RAPL, standard existing config, GTK absent, Nix builds, package installation and uninstall/rollback.
- Controlled physical-hardware test only after read-only preflight, baseline capture and a tested restoration path. Recheck on current kernel/firmware; historical tests are evidence, not proof about the presently installed system.

## References

- Linux Power Capping Framework: https://docs.kernel.org/power/powercap/powercap.html
- Intel RAPL common kernel driver: https://github.com/torvalds/linux/blob/master/drivers/powercap/intel_rapl_common.c
- Existing physical tests: `modern-intel-rapl-envelopes:docs/superpowers/validation/2026-09-11-modern-intel-rapl-hardware-validation.md`
- Existing systemd kill/recovery gate: `modern-intel-rapl-envelopes:docs/superpowers/validation/2026-09-11-modern-intel-rapl-systemd-failsafe-gate.md`

## October 2026 backend review: unresolved gates

1. `_acquire` may write a new sysfs limit, then fail the readback or receive an error after a partial write, returning without a durable ownership/restore record. Add fault-injection tests and a fail-closed transaction model.
2. `_apply_owned` currently discards ownership when post-write readback is unavailable; that may leave a modified constraint without reliable recovery metadata. Preserve uncertainty for a safe recovery attempt.
3. State persistence rollback is best-effort. A failed write-back after a state-store error must be reported as unresolved and recoverable; test combinations of read/write/fsync/replace failures and interruption boundaries.
4. On external drift, ownership is retired, but a later policy cycle can acquire the same limit again if the external controller has raised it. Prevent repeated fights with firmware/thermald by retaining a temporary yield/suppression decision.
5. RAPL write dispatch is currently attached only to `ModernIntelHwpPolicy`. Detect Intel Powercap capability independently of HWP mode while leaving unsupported/unsafe environments read-only.
6. The controller never raises the originally detected external baseline; document that deliberate behavior. An explicitly enabled ability to raise limits should be evaluated separately, not silently introduced.
7. The existing `--stats` already calls `generate_system_report(include_intel_power=True)` and uses `format_intel_power_summary`; GTK currently calls the report generator without that flag and will need the read-only discovery path enabled.
8. Existing physical hardware tests from September 2026 are useful evidence, but are not substitutes for fresh synthetic tests on the current tree/kernel. Commit runnable regression coverage before merging.

This is a design/audit commit only. No RAPL writes, runtime modifications, or upstream merge were performed.

## October 23, 2026 follow-up: unsupported CPUs and kernels

**Principle:** Optional functionality must fail gracefully and not affect existing CPU policy; even an explicit unsupported RAPL configuration must never fail the entire daemon or attempt a sysfs write. A requested-but-unavailable feature should produce a concise, informative warning, not silent success.

### HWP Dynamic Boost precedent

Current `master:core.py` checks `/sys/devices/system/cpu/intel_pstate/hwp_dynamic_boost` in `get_hwp_dynamic_boost_target()` and again in `set_hwp_dynamic_boost()`. If missing, it skips the write and (when explicitly configured) explains that the feature is unsupported. `get_hwp_dynamic_boost()` returns `None` for absent, unreadable or unknown content. The Linux `intel_pstate` documentation says the knob exists only in active mode with HWP enabled. This is a useful no-crash precedent, but RAPL needs zone-level detection rather than a single `.exists()` test. RAPL must also remain **disabled by default**, unlike the existing auto-selected Dynamic Boost policy defaults.

### RAPL capability assessment

Implement read-only discovery before considering configuration or writes, using the kernel Powercap sysfs ABI:

- Discover actual Intel RAPL **control types** (`intel-rapl`, `intel-rapl-mmio`); do not infer support from CPU model, vendor string, HWP state, the `intel_pstate` driver or an empty `/sys/class/powercap` directory.
- Resolve package zones and named `long_term`/`short_term` constraints; use canonical deduplicated paths, forbid symlink escapes and never mistake subzones for packages. Independent MSR/MMIO limits can coexist and differ.
- Distinguish missing control type, no package zone, no named PL1/PL2, readable-only limits, unreadable/permission errors, zone/control disabled (where `enabled` is exposed), and temporarily unavailable or dynamically removed files. Never automatically enable a disabled zone or load a kernel module.
- Test `enabled` when available, but don't assume that successful path discovery or an existing power-limit file proves it accepts writes. A firmware-locked or read-only attribute can reject privileged writes. Only an **explicit opt-in** can reach the write path, and the backend must handle write errors and verify readback.
- Support partially available systems: one recognized constraint remains usable even if another is absent or locked, provided safety preconditions hold. Distinguish 'feature supported for telemetry' from 'safe for policy control'.
- Presence of Intel RAPL is not equivalent to active Intel HWP. Detach RAPL capability and opt-in dispatch from `ModernIntelHwpPolicy`, retaining the normal legacy CPU governor policy on machines without HWP. On no-RAPL hardware, RAPL is a no-op, not a daemon startup failure.
- Preserve crash-safe restoration as a hard prerequisite for writes. An environment variable on its own is not a comprehensive proof of recovery; install/packaging and actual systemd unit lifecycle must be verified.
- If configuration is absent or globally disabled: no RAPL warnings and no writes. If configuration explicitly requests unsupported RAPL: report a concise warning on startup or meaningful state changes, suppress repeated messages in periodic loops and reflect the reason in `--stats` and GTK. Status should distinguish `disabled_by_config`, `not_exposed_by_kernel`, `no_package_constraints`, `zone_disabled`, `read_only_or_locked`, `unreadable`, `unsafe_to_apply`, `available`, `active`, and `external_override`; avoid inventing hardware claims from missing data.

### Test strategy to commit **with implementation**

Prefer deterministic stdlib `unittest` plus `tempfile.TemporaryDirectory` and injected fake-sysfs paths as the project currently declares no pytest development dependency. If future maintainers prefer pytest, keep new test dependencies optional and isolated. Test names and cases are more important than the framework choice.

1. AMD/ARM and Intel hardware with no `intel-rapl` control type: defaults and explicit opt-in, daemon remains running, all existing governor features continue, no write attempts.
2. `intel_pstate` active/HWP available but no RAPL; Intel RAPL available while HWP is absent/passive; independent feature gates behave correctly.
3. Package zone without any PL1/PL2, PL1-only, PL2-only, MSR-only, MMIO-only, both, multiple packages, different named constraint indexes and read-only energy-only zones.
4. Powercap directory missing, unreadable, renamed, dynamically disappeared, a symlink alias, an escape outside allowed sysfs boundaries, and malformed/absent `name`/limit attributes.
5. Control type or zone `enabled=0`; invalid/unknown `enabled`, missing `enabled`, and a file readable but unwritable as root. Do not try to enable a zone; show capability/reason correctly.
6. Invalid configured power values; opt-in false; config requested but no recognized limits; changed AC/battery profile; service without proven restoration; zero RAPL writes in read-only monitor, GTK and `--stats`.
7. Privileged writes rejected (EACCES, EROFS, EPERM), partial accepted/quantized writes, failed readback, persistence/fsync failure, failed rollback, drift and re-acquisition suppression; verify original baseline restoration and clear unresolved diagnostics.
8. Dedicated snapshot, shared renderer and GTK smoke tests verifying unsupported/partial/available states; do not block the GTK main loop or require GTK in headless test suites.

CI should run runnable behavior tests alongside installation/build checks. Only focused tests and user-facing documentation belong in the eventual upstream PR; do not commit these internal task plans, retrospective development gates, machine-specific logs or internal workflow notes to the feature branch.

### Source documentation

- Powercap sysfs API: https://docs.kernel.org/power/powercap/powercap.html
- Intel P-state/HWP sysfs attributes: https://docs.kernel.org/admin-guide/pm/intel_pstate.html

### Branch policy

This planning document is retained only under `internal/intel-rapl-review-notes`, independently of the clean `feat/intel-rapl-gtk-stats` integration branch. Future implementation commits and **runnable regression tests** should land on the latter; before any upstream PR, inspect `git diff upstream/master...HEAD --name-status` and `git log upstream/master..HEAD --oneline` and confirm internal docs are absent from both the final changed-file list and implementation commit history.
