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
