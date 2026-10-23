# Intel RAPL: configurable limits, GTK controls, and shared telemetry

Status: implementation plan (no runtime behavior change)  
Base: `lucasleocs/auto-cpufreq@070693c94170f6ac4b1a01e3bcc41d3d798825d2`  
Feature branch: `feat/intel-rapl-gtk-stats`  
Date: 2026-10-23

## Goal

Allow users of compatible Intel CPUs to inspect and optionally configure Linux Powercap RAPL **package** long-term and short-term limits for charger and battery profiles. Provide consistent reporting through the GTK dashboard and `--stats`, an authenticated GTK settings flow, and documented command-line configuration. Preserve existing behavior when the feature is disabled or hardware is unsupported.

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
6. **GTK reporting.** Add a compact 'Intel RAPL / Package Power Limits' section to `auto_cpufreq/gui/app.py`, populated via the same report snapshot on its background refresh path. Hide or gracefully disable edit controls when RAPL support is absent while preserving an explanation. Show each independent control type and named constraint; explicitly distinguish live effective values from configured values.
7. **GTK editing.** Add an explicit 'Configure limits' dialog or expander with separate charger/battery long/short term fields, an opt-in control, validation, and 'Apply'/'Cancel'. No root GUI and no privileged sysfs write directly in GTK. Integrate with the existing asynchronous `pkexec auto-cpufreq ...` pattern through a **narrowly scoped** CLI/helper action that validates all input, checks the authoritative daemon configuration path, and performs an atomic, durable edit (preserving unrelated keys/comments and rejecting symlink/unsafe paths). Avoid silently writing an XDG user config ignored by the root daemon, and treat Nix-managed/Snap/confined configurations as read-only unless their supported config mechanism is proven. Configuration changes should be picked up through the existing config watcher or a controlled reapply path; read back actual state before reporting success.
8. **README and examples.** Document requirements, opt-in default, explicit AC/battery example with non-recommended illustrative watt numbers, config locations and permissions, MSR/MMIO differences, conservative no-raise behavior, power-vs-consumption distinction, rollback, firmware/thermald interaction, GUI use, `--stats` sample, and restrictions (NixOS/Snap/non-systemd). Maintain any Nix config example if exposed in that packaging path.

## Suggested implementation sequence

- **A — Sync gate:** upstream diff/conflict audit, isolated merge, CI, safe `master` update.
- **B — Backend:** adapt tested RAPL discovery, policy, persistence and restoration as focused commits. Preserve capability gating and the existing legacy path. No GTK edits yet.
- **C — Shared observability:** one common snapshot and formatter for CLI/Urwid/GTK. Make reporting read-only first.
- **D — Privileged configuration API:** validate edits independently of the GUI, atomic config persistence, detection of externally managed settings, daemon refresh semantics.
- **E — GTK:** responsive status widget, edit dialog, auth/error/result flow, accessibility, and unsupported-state behavior.
- **F — Docs and regression:** example configs, README, packaging/service validation, and manual UI/hardware gates.

## Required tests / acceptance gates

- Fake-sysfs tests for no RAPL, read-only RAPL, MSR-only, MMIO-only, both, multiple packages, aliases, subzones, reordered constraints, partial/missing attributes and symlink escapes.
- Decimal parsing: 0/negative, NaN/Infinity, bad precision, overflow, unsatisfied limits, missing sections and unchanged defaults.
- No writes when disabled, running read-only modes, unsupported hardware, missing systemd fail-safe or insufficient permission.
- Verify and restore after partial write failure, quantized readback, different observed maximum, external drift, AC/battery transitions, config disable, service stop, crash, and unexpected reboot/restart.
- Ensure report consistency across GTK, CLI `--stats` and Urwid; no stale 'applied' state when firmware modifies a cap; no root operations from GUI refresh worker.
- GTK validation without GTK main-thread blocking; authentication cancellation, concurrent config edits, external config changes, malformed system config, no accessible editing destination, and successful persistence/reload.
- Compatibility checks for non-Intel CPU and Intel without RAPL, standard existing config, GTK absent, Nix builds, package installation and uninstall/rollback.
- Controlled physical-hardware test only after read-only preflight, baseline capture and a tested restoration path. Recheck on current kernel/firmware; historical tests are evidence, not proof about the presently installed system.

## References

- Linux Power Capping Framework: https://docs.kernel.org/power/powercap/powercap.html
- Intel RAPL common kernel driver: https://github.com/torvalds/linux/blob/master/drivers/powercap/intel_rapl_common.c
- Existing physical tests: `modern-intel-rapl-envelopes:docs/superpowers/validation/2026-09-11-modern-intel-rapl-hardware-validation.md`
- Existing systemd kill/recovery gate: `modern-intel-rapl-envelopes:docs/superpowers/validation/2026-09-11-modern-intel-rapl-systemd-failsafe-gate.md`

This commit records a design and acceptance criteria only. It does not enable RAPL, change daemon behavior or update the installed application.
