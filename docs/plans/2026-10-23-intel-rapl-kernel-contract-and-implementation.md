# Intel RAPL: kernel-contract-led implementation plan

Status: internal design review, 2026-10-23. **NOT for the upstream pull request.**
Scope: optional package PL1/PL2 management for auto-cpufreq, with **read-only** terminal statistics and GTK. Tests, validation reports and planning documents are confined to internal/lab branches. The final upstream PR contains only necessary functional code; no tests or documentation are committed to that PR, per project-owner direction.

## 0. Source of truth and assurance levels

A. **Documented Linux userspace contract:** Linux Power Capping Framework:
https://docs.kernel.org/power/powercap/powercap.html

B. **Actual Intel Linux driver ABI behavior:** common driver:
https://github.com/torvalds/linux/blob/master/drivers/powercap/intel_rapl_common.c
and TPMI driver:
https://github.com/torvalds/linux/blob/master/drivers/powercap/intel_rapl_tpmi.c

C. **Intel platform thermal design:** Intel Package Power Control:
https://edc.intel.com/content/www/us/en/design/platforms/core-processor-series-3-datasheet-volume-1-of-2/002/package-power-control/
and i3-1315U specifications:
https://www.intel.com/content/www/us/en/products/sku/232136/intel-core-i31315u-processor-10m-cache-up-to-4-50-ghz/specifications.html

D. **Linux HWP is independent of RAPL:** Intel P-state:
https://docs.kernel.org/admin-guide/pm/intel_pstate.html

E. **systemd service and lifecycle requirements:**
https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html

The kernel/API documents establish ABI semantics; they **do not** guarantee firmware cooperation, successful writes, reliable ownership across independent processes, or a transactional powercap interface. Everything beyond the documented contract is a **conservative application-level policy or experimentally verified behavior**, explicitly labeled as such.

Kernel driver internals are reference evidence, **not a new userspace contract**: do not parse MSR layout, rely on internal kernel structs, or implement direct MSR/MMIO writes.

## 1. Non-negotiable functional scope

- RAPL active writes are **off by default**, requiring opt-in via .conf. An upgrade must never begin writing CPU package power caps on existing systems.
- Manage **package** power limits only. Never touch core/uncore/DRAM/PSYS, PL3/PL4, time windows (Tau), energy counters, or zone \`enabled\`.
- Named constraints: \`long_term\` = PL1 and \`short_term\` = PL2, discovered by \`constraint_<N>_name\`. **Never assume PL1 is index 0 or PL2 is index 1**: the kernel driver explicitly remaps valid PL IDs to Powercap constraint IDs.
- Units: powercap \`power_limit_uw\` is in micro-watts. Config is decimal watts; parse with \`Decimal\`, reject NaN/infinite, zero/negative, malformed, absurd size, and more than 6 fractional decimal places; use integer microwatts on sysfs. When available, inspect kernel min/max metadata conservatively; this metadata does not authorize exceeding a known external baseline.
- No automatic numeric recommendations based on Intel Processor Base Power or Maximum Turbo Power. Those CPU SKU specifications do not establish the OEM laptop's cooling/power delivery limits.
- No root GTK, no config editing in GTK, no direct RAPL sysfs access from GUI write paths. Both GUI and CLI read the same passive snapshot. Clearly distinguish current configured **limit** from instantaneous **consumption** and from **RAPL control status**.
- Keep existing normal governor/turbo/EPP behavior and error handling on all non-RAPL systems.

## 2. Capability/discovery contract

Read-only discovery starts at the actual sysfs Powercap tree (normally \`/sys/class/powercap\`, resolving legitimate aliases to the kernel's underlying tree). Scan recognized Intel control type trees; \`intel-rapl\` may be implemented via a traditional MSR or TPMI driver, and \`intel-rapl-mmio\` may coexist. **Do not infer the backend from CPU model names or require active HWP.**

Find zones reporting names matching \`package-<N>\` and confirm they are root package zones, not nested subdomains. Resolve aliases/canonical paths, deduplicate by resolved identity, and guard against following paths outside the appropriate sysfs Powercap control tree. Class paths can be symlinks. Do not assume \`package-0\` means a single universal power envelope when multiple control types or CPU packages exist.

Use explicit capability states for each named constraint:
- not_exposed (no usable Intel RAPL control type),
- no_package_zone / no_named_constraint,
- disabled (control or zone explicitly reports \`enabled=0\`),
- unknown_enabled (enabled exists but cannot be read or is malformed),
- unreadable (limit missing/inaccessible/malformed),
- observed (the limit is readable; **no proof of writability**),
- opted_out / selected_for_control (configuration-level state),
- owned / pending_recovery / yielded_external / failed (control-level state).

A missing optional \`enabled\` file is unknown rather than false. For the **active writer**, require an explicitly safe capability predicate; fail closed on unreadable \`enabled\` where it is expected. Do not write a tentative value just to probe whether an attribute is writable. Do not load kernel modules or change \`enabled\` automatically.

The existing \`rapl_capabilities.py\` passes initial synthetic tests, but its current labels \`available\` and \`unavailable\` are telemetry-centric: **refine them before the writer consumes them**. Confirm root-alias behavior and kernel version differences with fake sysfs trees and actual hardware. The read-only GTK remains non-authoritative about whether auto-cpufreq owns a limit.

## 3. Control-type selection and ambiguity policy

The user's current hardware previously exposed:
- \`intel-rapl\` package PL1 at 28 W;
- \`intel-rapl-mmio\` package PL1 at 15 W;
- independent PL2 values at 41 W.

It would be misleading to say a 28 W \`intel-rapl\` cap determines effective sustained package power while a stricter 15 W MMIO cap is also present. The kernel ABI does not specify a universal compositional formula between independent control types.

**Proposed safe configuration contract:** when more than one recognized Intel RAPL control type exposes package constraints, require explicit control-type selection before any write; otherwise remain read-only and warn once. A single control type may be selected implicitly **only when unambiguous** and explicitly opted in. Never modify MSR and MMIO together by default. Future explicit multi-control-type targeting must be separate, documented, and individually tested. Preserve per-package/constraint identity; if multiple packages exist and config is package-agnostic, require an unambiguous scope or a clearly tested "all packages" choice; no implicit cross-package writes.

Open issue to resolve against available targets/tests before implementation: whether to use \`[intel_power] rapl_control_type = intel-rapl\` or a separate exact selector, with warnings for stale/unavailable selection. Do not assume one controller type is always the most authoritative one.

### 3A. MSR/MMIO equality review (23 October 2026)

The Intel 10th-generation U/Y datasheet, Vol. 2, section 3.3.31, explicitly states that MMIO package power limits and their MSR counterparts are **evaluated separately**. This is primary Intel evidence that these two channels need **not** contain identical numbers. It is not the datasheet for the user's i3-1315U, so do not extrapolate exact electrical/thermal arbitration to every generation:
https://www.intel.com/content/dam/www/public/us/en/documents/datasheets/10th-gen-core-families-datasheet-vol-2-datasheet.pdf#page=149

The Intel thermald developer documentation states that its cooling-device selection **prefers RAPL MMIO over MSR when available** and, in adaptive mode, may raise the MSR value to 200 W specifically to avoid MSR interference. This is an intentionally unequal software strategy, not a universal rule for auto-cpufreq or authority to raise firmware limits:
https://github.com/intel/thermal_daemon/blob/master/DEVELOPER.md

**Updated contract:**

- Do **not** normalize, mirror, or synchronize MSR/MMIO PL1 or PL2 merely because both are present. An unequal readback is not intrinsically an error. Preserve the untouched interface and its independently discovered baseline.
- A configured target should be described as a **requested upper cap**, not a guarantee of measured package watts and not a promise that hardware will run exactly at that cap. A stricter untouched limit may keep the effective performance below target. Different time windows make "effective limit = min(PL1 entries)" only an approximation, **not** a documented universal formula.
- In the first active-writer version, apply the configured cap to **one unambiguously selected controller** (implicit only when just one is present; explicit selection when both are present). When both are present, report the other values and explain possible interactions. Do not unilaterally prefer MMIO for every model simply because thermald does so.
- Optionally investigate a future explicit \`rapl_control_type = both\` **upper-ceiling** policy, but only if requested, separately validated and with per-controller ownership/recovery. Even that mode must **never raise** a stricter OEM value just to make the numbers equal. Its contract would be "each eligible active limit will be lowered at most to the requested value", **not** "all registered limits become identical".
- If Intel thermald, DTT/ACPI thermal policy or firmware changes an observed limit, do not reassert it every 2 seconds. Suppress reacquisition after drift and report the conflict; do not disable existing thermal daemons.
- Add lab tests for unequal starting limits such as 28 W MSR / 15 W MMIO, single-control-type configurations, missing/locked interfaces, differing PL1 and PL2 availability, accidental cross-write detection, time window mismatch, and "target greater than one OEM cap" with **zero raising**. A snapshot that shows both interfaces is read-only evidence, not permission to write to both.

These distinctions are explicitly sourced. Preserving existing OEM limits is an **application policy** chosen for safety, not an invariant guaranteed by the Powercap ABI.

## 4. Policy parsing and daemon integration

Existing configuration structure may use \`[intel_power] enable_rapl_envelopes\`, \`[charger]\`/\`[battery]\` \`rapl_package_long_term_w\` and \`rapl_package_short_term_w\`. Add explicit selector only if required by ambiguous hardware. Settings are optional for each PL. Absent opt-in or invalid config means no writes; log validation errors only where appropriate, without stopping the rest of auto-cpufreq.

Keep RAPL independent of Intel HWP/CPUFreq governor backends. \`hwp_dynamic_boost\` is an example of fail-gracefully behavior but has a **different** documented sysfs availability condition (active \`intel_pstate\` plus HWP). RAPL support must not be selected by HWP eligibility.

Use **one long-lived RAPL controller object** in the daemon so a yield/suppression event is not forgotten at each 2-second policy cycle. Never create a new controller for every periodic tick or charger transition. Do not poll to reassert caps against firmware continuously. Handle config reload and AC/battery transitions using existing daemon lifecycle; read current config from the daemon's **authoritative path**, not an assumed GTK user's XDG file.

Never perform active RAPL writes through \`--monitor\`, \`--stats\`, GTK, \`--debug\`, or standalone read-only telemetry. \`--live\` is read-only for this RAPL feature until a reliable restore hook and explicit user-authorized live mode are designed.

## 5. Write authorization and preconditions

Grant RAPL write permission *only if all*:
1. global opt-in enabled and profile's PL target is valid;
2. exact package/control type/constraint selected with no ambiguity;
3. discovered matching Powercap path is current and valid, enabled state allows it, current external value is readable and positive;
4. profile target is <= current external limit on first acquire, and <= recorded baseline on owned updates (strict **lower-only relative to baseline** policy);
5. safe independent recovery mechanism has been installed, exercised and is available for that daemon installation; a mere environment variable claiming failsafe support is **not proof**;
6. exclusive app-level lock acquired for apply/restore, and no pending/ambiguous prior journal requiring reconciliation;
7. requested numeric value is within safe discovered attributes and kernel will accept it (acceptance verified by readback; do not assume write success or unsupported min/max consistency).

On unsupported AMD/ARM, absent RAPL, firmware lock, disabled zone, permission failure, stale sysfs, ambiguous controller selection or unverified recovery, **do nothing** to powercap and let normal CPU policy continue. Surface a non-spamming warning when user explicitly enabled RAPL.

## 6. Recovery transaction design (engineering policy, not kernel guarantee)

The kernel Powercap sysfs API does **not** provide compare-and-swap, an atomic multi-constraint transaction, or an ownership token shared with firmware/other processes. The application must not claim it can guarantee restoration when external writers race it.

- Serialize its own apply/restore operations using \`flock\` (or equivalent) and a single daemon controller. This prevents multiple local auto-cpufreq instances conflicting, not arbitrary firmware/other apps.
- Maintain a root-owned, permission-restricted journal, schema-versioned, with exact control type, canonical zone identity, named constraint, original readback, previous owned value, intended next value, transaction state, and process/boot identity as needed.
- Persist and fsync an **intent** before the sysfs write. Atomic file replacement + parent-directory fsync is an application requirement; verify fsync failures and rollback scenarios.
- Before writing, re-read the current value and other predicates under the application lock. Abort on unexpected drift. This reduces but does not eliminate the time-of-check/time-of-use race with external firmware.
- Perform a **single** sysfs power-limit write; then read back. Validate kernel-accepted effective value without assuming exact equality when hardware quantization occurs; define an explicit safe tolerance or reject/restore if a greater-than-requested value would violate the conservative policy.
- On success, commit \`owned\` with verified effective readback. On unreadable post-write state, **retain pending recovery information**. On write exceptions, re-read if possible; do not clear the journal without proving no change occurred.
- If external drift is observed, mark that exact constraint as \`yielded_external\` and **suspend reacquisition** for a controlled lifetime (until opt-in is deliberately reset/re-enabled, or service restart policy after proper reconciliation). A subsequent 2-second cycle cannot immediately reacquire.
- Recovery before new writes: if value exactly equals last owned or documented in-flight target, cautiously attempt restoring prior baseline; if it equals original, retire the pending entry; otherwise do not overwrite an unrelated value. Preserve unresolved state and report manual-recovery requirement.
- The legacy lab's \`pending_target_uw\` implementation passes current synthetic tests but needs more review: it does not make ownership atomic against an external actor coincidentally setting the same number, and journaling multi-package writes remains non-atomic. Do not deploy it solely on the basis of green tests.
- For two independent controllers or multiple packages, use separate per-constraint records with clear partial-apply reporting. Apply failure on one should not be incorrectly presented as success for the group. Avoid blind multi-constraint rollback after another actor changes hardware.

## 7. systemd recovery integration, installation and upgrades

Official systemd.service states \`ExecStopPost=\` runs after the service stops, including failed starts and unexpected exits; use an independent restore helper that does not import \`auto_cpufreq.core\` or start config monitors. Confirm \`TimeoutStopSec\`, helper executable, privileges and logs.

Official systemd.exec states \`RuntimeDirectory=\` is removed at service stop by default; \`RuntimeDirectoryPreserve=restart\` keeps files over restart, and \`yes\` preserves them after ordinary stop (but **not reboot**, because /run is tmpfs). For interrupted recovery requiring later manual retry, **evaluate \`yes\` rather than assuming \`restart\` is sufficient**. Use \`0700\` directory and \`0600\` journal. Test failed cleanup followed by restart, remove and upgrade.

Upstream 3.2.0 deploys the active source under \`/opt/auto-cpufreq/current\` and uses \`.../current/venv\` for the service. The experimental RAPL branch uses obsolete \`/opt/auto-cpufreq/venv\`: **never copy its service verbatim**. The updater may rotate \`current\`; verify that the recovery helper remains executable and compatible with an old journal before switching releases.

Do **not** enable \`WatchdogSec\` without confirming a sustained \`sd_notify WATCHDOG=1\` implementation and testing timing: otherwise systemd will kill a healthy daemon.

An opt-in RAPL writer must be disabled for service environments without a tested cleanup lifecycle (e.g. unsupported non-systemd init, Snap confinement, Nix service customization, arbitrary \`--live\`). Start by providing read-only reporting universally. Enable other init systems only following their own documented installation/recovery lifecycle tests. Do not silently claim feature parity.

At boot, a /run journal may be absent. Do not attempt cross-reboot recovery from a stale copied file. If persistent storage under \`StateDirectory=\` is considered, first define boot-id/hardware identity validation and a policy to avoid restoring firmware defaults in a new boot. This is a **separate design gate**.

## 8. Reporting (shared system snapshot, no GUI editing)

- \`SystemReport\` collects immutable, read-only RAPL snapshot, preferably opportunistically at the existing refresh frequency without expensive blocking on GTK's main thread.
- \`--stats\`: show \`intel-rapl\` vs \`intel-rapl-mmio\`, package and named PL1/PL2, current readback watts, read-only/disabled/unavailable, and—only when provable—whether daemon owns that exact constraint. Restrict long lines in urwid.
- GTK: read-only section with selectable/wrapped text; no elevation and no config changes. Do not show nonexistent PL2 as 0 W or "locked" unless kernel evidence supports that specific conclusion.
- Do not label configured cap "CPU current power consumption." If optional average power is later calculated from \`energy_uj\` deltas, keep it separate with time interval and rollover/one-wrap caveats; no instantaneous-power claim.
- If feature is disabled and no RAPL is exposed, consider concise "Not exposed by kernel" rather than cluttering all screens with empty rows.
- Current four-file read-only feature is on \`feat/intel-rapl-gtk-stats\` and builds on upstream. The writer stays in the lab until all gates pass.

## 9. Test matrix and evidence gates (LAB ONLY — never include in final PR)

**A. Read-only ABI layout:** absent sysfs, AMD/ARM, single Intel type, dual MSR/MMIO types, TPMI-backed \`intel-rapl\`, missing PL1/PL2, arbitrary constraint indexes, multi-packages, nested subdomains, disabled/unreadable \`enabled\`, symlink aliases, symlink escape, malformed values, vanished zone, root and non-root reads.

**B. Config:** disabled by default, boolean parsing, bad W values, >6 decimals, overflow, no profile values, AC/battery transitions, selected/unselected control type, unsupported CPU, config reloaded, invalid path.

**C. Write fault injection:** EROFS/EACCES/EPERM, firmware ignoring write, partial sysfs write failure, readback missing/quantized/unsafe, metadata min/max conflicts, process interruption at every journal boundary, os.replace/fsync failure, concurrent calls, external drift, re-acquisition suppression.

**D. Reconciliation:** pending before write, after write, after readback, after commit; stop, restart, SIGTERM/SIGKILL, timed-out helper, failed \`ExecStopPost\`, manual stop and reboot boundary. Assert all baseline values restored where provable, and unresolved state retained otherwise; never claim unverified success.

**E. Installer/upstream:** Linux source install in upstream 3.2.0 layout, service stop/reinstall/remove/update and rollback, Snap read-only, Nix packaging builds, headless Python 3.9 and 3.12, GTK no-write, no daemon regression, no changed legacy CPU policy.

Current lab baseline: 20 synthetic tests passed on Python 3.9 and 3.12, plus the Linux build and Nix workflow. This is encouraging but **not** a verified active-writer release. Preserve test code and reports only on lab/internal branches.

## 10. Execution order and stop/go criteria

**Gate 0 — isolate upstream:** use upstream 3.2.0 as the feature base; keep fork master and draft synchronization PR independent. Verify that the final contributor branch contains only intended functional files, no fork-only feature commits or internal plans.

**Gate 1 — telemetry quality:** strengthen capability resolution and wording; no root or writes. Validate both TUI and GTK and build/CI. (Initial implementation exists; confirm exact behavior on hardware.)

**Gate 2 — profile/config and controller selection:** validate full opt-in grammar and make ambiguous dual-controller case fail closed. Reuse root daemon config path; prove no side effects on legacy policy.

**Gate 3 — journal and writer:** settle journaling schema, status model, atomicity limitations, observer coexistence and numerical bounds. Synthetic fault tests must pass before any hardware writes.

**Gate 4 — service lifecycle:** wire only supported systemd-installed service to independent restoration helper, align with /opt/.../current paths and tested runtime directory persistence. Validate installation/update/restart and error recovery before enabling opt-in on physical hardware.

**Gate 5 — physical validation:** first record a read-only baseline (all constraints, control types, firmware state, uname, installation method, service file). Then, only with explicit authorization and reversible isolated conditions, lower one chosen constraint modestly; verify other constraints unchanged, restore exact baseline, then simulate drift/service termination. Do not casually manipulate laptop power caps in a live notebook session.

**Gate 6 — contribution hygiene:** construct clean commits or selected-code cherry-picks based on current upstream; no testing files, private hardware logs, planning docs, notes, unrelated HWP/event-loop changes, personal config or references in final upstream PR. Final diff audit by path AND commit history. Submit only after feature passes all mandatory gates.

## 11. Remaining decisions / known limits

1. Exact selection key when both RAPL control types exist; avoid silently writing to both.
2. Persist suppression across daemon restarts? A restart is not automatically a new permission to fight firmware; consider at least one manual opt-in reset or initial-check handshake.
3. How to communicate persistent uncertainty to the user if journal recovery cannot be completed without overwriting a newer external value.
4. Which non-systemd init systems can ever support active writes; use read-only as default for untested modes.
5. One controller per daemon plus an independent helper requires cross-process locking and journal schema compatibility; no environment-marker shortcut.
6. Updating caps while system is suspended or resumes may require rediscovery; do not assume paths or firmware limits remain unchanged.
7. Scope to one or multiple CPU packages should be explicit rather than silently using a single global watts value.
8. Kernel does not promise atomic compare-and-swap for Powercap. Strong recovery claims must be framed as **best-effort under verified preconditions**, never as a blanket guarantee.

### Review result

**Approved now:** read-only package RAPL discovery and shared GTK/stats reporting as a staged feature, subject to further ABI edge-case tests.

**Not approved now:** enabling the active PL1/PL2 writer, regardless of synthetic green checks, until the recovery journal, systemd lifecycle, control-type ambiguity and external-owner gates above are satisfied.
