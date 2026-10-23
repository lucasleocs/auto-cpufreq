# Intel RAPL — systemd recovery and installation integration
Internal implementation plan — 2026-10-23

## 0B. Pre-implementation audit: minimum safe implementation (2026-10-23)

**Result: plan refined after checking the current Intel Powercap implementation, systemd documentation, versioned installer and experimental writer. This section overrides less precise recommendations later in the document; no production implementation was made.**

### Definitive implementation choice

Retain **exactly one independently executable recovery helper** invoked by the existing managed systemd unit via `ExecStopPost=`, plus a **small, same-boot ownership journal and one cooperative lock**. No separate long-lived rescue daemon, watchdog, custom updater, release symlink logic, direct MSR access, persistent cross-reboot recovery, or new GUI controls. Keep all non-RAPL CPU policies independent.

For normal version updates: old daemon stops -> recovery helper runs while the old release is installed -> original versioned updater continues -> new daemon discovers fresh hardware/config and may opt in again. A restoration failure is reported but **must not fail the service's stop operation solely because RAPL is optional**. The standard systemd command prefix `-` is available to record a nonzero cleanup command result without treating it as an operation failure; test the actual unit and installer path. Never use that masking as a substitute for journal reconciliation or a user-visible warning.

### Required code fixes before wiring any service (P0)

1. **Do not discard journal on ambiguous restoration failure.** Current lab `intel_rapl.py::_restore()` deletes the ownership record after certain failed writes if a positive readback differs from the pre-write value even though the original baseline was **not** restored. Fix: retain a pending/uncertain record and prohibit further writes; delete only on proven baseline readback or well-defined external ownership loss. On an already-original readback, retire without an unnecessary sysfs write.
2. **Do not claim unexpected readback as owned.** Current `_recover_unsafe_readback()` may assign any positive unexpected hardware value to `last_written_power_limit_uw`. That value may have come from firmware or another writer; preserve an unresolved observation and decline new writes, without laundering uncertainty into confirmed ownership.
3. **Do not let an unverified `enabled` attribute authorize writes.** The lab `_zone_write_allowed()` only checks `path.exists()`. Distinguish (a) explicitly disabled, (b) present-but-unreadable, and (c) legitimately absent optional `enabled` attribute; do not automatically assume present-but-unreadable means enabled. When absent, use the documented ABI and an explicit compatibility test rather than blindly failing all legitimate platforms.
4. **Stop conflicting Powercap discovery implementations.** Experimental `intel_power.py` and clean `rapl_capabilities.py` have different zone/alias and canonical-path logic. Refactor toward one shared, pure discovery/identity implementation for telemetry and writing. Named `long_term`/`short_term` constraints are authoritative: never assume indexes 0/1; do not follow unrelated links out of the expected control tree. Validate absent, late, renamed and disappearing zones.
5. **Harden ownership storage.** The experimental lock uses `O_NOFOLLOW` for the leaf but the entire root-owned directory ancestry, pre-existing file owner/type and schema must be checked. Test `os.replace`, file/parent fsync, unlink/fsync, invalid JSON, unexpected file type, and stale identity. A failed fsync *after* a successful atomic replace is not equivalent to proof of rollback. Avoid destructive cleanup when state durability is uncertain.
6. **Handle quantized or ignored writes.** After sysfs write, read back and compare to original baseline and requested target. If the driver rounds a target to a safe lower-than-baseline value, store the *actual* verified readback and do not rewrite it on every daemon tick. If it ignores, rejects or raises the value, preserve/recover safely. Do not invent fixed power tolerances without evidence.
7. **Handle early boot correctly.** The user's custom unit was skipped at ~25.929 s because the Powercap file was not yet present; the kernel discovered RAPL at ~26.285–26.889 s. Do not gate the **whole auto-cpufreq daemon** on a once-only `ConditionPathExists=` for RAPL. Start ordinary governor/EPP policies normally and retry lightweight discovery until the driver appears; opt-in writer remains inactive until full capability/readiness checks pass. Do not modprobe/load the driver or write a probe cap.
8. **Lifecycle readiness must be real.** An arbitrary `AUTO_CPUFREQ_RAPL_FAILSAFE=1` environment variable cannot prove a compatible cleanup hook. The active writer must only arm in the audited installed systemd variant, and must be disabled on unsupported init/install types. Test that a manually invoked `--daemon` cannot bypass the lifecycle safeguard.

### systemd implementation boundaries (P1)

- **`ExecStopPost=`:** designed for post-mortem cleanup after standard stop, crash or failed startup. It is not a power-loss handler. `ExecStop=` alone is insufficient. Use a small independent entry point, no `core.py` imports or GUI/config dependency, and idempotent no-op when the journal is empty.
- **`RuntimeDirectory=auto-cpufreq`, `RuntimeDirectoryMode=0700`, `RuntimeDirectoryPreserve=yes`:** same-boot ownership records survive stops and updates. No cross-reboot replay from `/var/lib` by default. Verify root-only `0600` journal and helper's ability to access it at cleanup time.
- **`ExecStopPost=-...`:** **candidate** to keep optional RAPL failures from blocking versioned updates. Helper still returns correct nonzero failures, logs and retains pending state. Test that normal stop and `--update` really proceed; verify startup refuses RAPL on unresolved journal rather than assuming a successful `systemctl stop` means restored.
- **Timeouts:** validate real command termination and `ExecStopPost` behavior with systemd; do not assume an unbounded helper will run forever. Test SIGTERM/SIGKILL, canceled start, helper timeout and post-failure restart. Keep `Restart=on-failure` upstream. No `WatchdogSec` unless the daemon actually implements and tests `sd_notify WATCHDOG=1`.
- **Installer:** first implementation leaves `auto-cpufreq-installer`, `releases/`, atomic `current` and rollback untouched. Test managed-unit `cmp` checks as already written by upstream. Any discovered incompatibility must be reproduced and justify a **narrow** separate change.
- **Packaging:** `systemd-analyze verify` validates syntax and dependency/executable references, **not** stop/restart semantics. Real-manager tests belong in an isolated VM/container that genuinely runs systemd as PID 1; plain GitHub Actions Python/unit tests cannot prove systemd lifecycle. NixOS/AUR/Snap/other init environments remain read-only until independently verified.

### Test design and clear GO/NO-GO gates (P1)

| Gate | Test | Must pass |
| --- | --- | --- |
| A | Recovery state-machine faults (before journal, after journal, before/after sysfs write, readback failure, changed hardware, failed fsync) | Never delete uncertain recovery state or overwrite unrelated external values |
| B | Two-controller/multi-package, disabled/locked BIOS, optional/malformed `enabled`, missing named PL1/PL2, late driver probe | Only explicitly selected and safely writable constraints can change |
| C | Normal stop/restart, SIGTERM/SIGKILL of **daemon**, failed service start, helper failure/timeout, stale journal within same boot | Idempotent helper; new daemon can run other CPU optimizations while RAPL stays unarmed |
| D | Existing upstream versioned update with RAPL off/on, error recovery, failed activation/rollback, unit ownership verification | No change to usual updater semantics, exit codes or release layout caused by optional RAPL failures |
| E | Config disabled, AC/battery switch, config hot reload, stale controller selector, driver appears after boot, script conflict | Bounded, non-spamming warnings, no unintended reacquisition or forced equality |
| F | One explicitly authorized hardware test after A–E, with read-only baseline and a small downward change to **one** constraint | Exact safe restoration verified; other interface and PL2 unchanged |

**Hard stop:** previous 55 synthetic tests and one successful 15 -> 12 -> 15 W MMIO experiment are useful but **do not** validate A–E. Do not port active writer to contribution branch, enable it on the notebook, or submit an upstream PR until the gaps are closed.

### Primary references checked

- Linux Power Capping Framework: https://docs.kernel.org/power/powercap/powercap.html
- Linux Intel driver (BIOS may lock limits but keep them readable): https://github.com/torvalds/linux/blob/master/drivers/powercap/intel_rapl_common.c
- systemd.service: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- systemd.exec: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
- systemd-analyze verify: https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html
- Linux flock(2), advisory locking: https://man7.org/linux/man-pages/man2/flock.2.html
- Exact upstream source: `AdnanHodzic/auto-cpufreq`, version 3.2.0; source-installed unit points at `/opt/auto-cpufreq/current/venv`, and versioned releases switch via the existing `current` symlink.

---

## 0. October 23 clarification: updater non-interference is mandatory

**User decision overrides earlier conservative proposals that could block an ordinary version update.** Upstream's versioned `/opt/auto-cpufreq/releases/` layout and atomic `current` activation must be preserved. The RAPL feature may pause during an update, just like existing CPU governor/EPP management, and safely reapply the opt-in configuration after the daemon starts again.

Separate the **normal** and **exceptional** paths:

**Normal update (must remain unchanged):**
1. Existing auto-cpufreq update/remove/install sequence requests the regular systemd service stop. Do not introduce a new RAPL-specific updater or manipulate `current`/release generations.
2. While the **old installed version** is still present, its standard `ExecStopPost=` cleanup attempts to restore *only provably owned* RAPL limits; a daemon stop by itself does **not** reset Powercap sysfs limits.
3. The existing updater switches versions by its current mechanism, then starts the new daemon. No new Powercap writes are required during the update.
4. The new daemon re-reads the **actual .conf** file and, only if opt-in is still enabled and the supported lifecycle is healthy, applies the selected constraint(s) again from a freshly observed firmware baseline. If opt-in is off or the new version has no RAPL support, only ordinary CPU management resumes. Do **not** claim that the new version always supports or reapplies RAPL.
5. No new failure point is allowed to block normal version replacement merely because optional RAPL telemetry is missing or a controller is unsupported.

**Exceptional cleanup failure (keep normal updates possible without hiding danger):**
- If the old helper cannot prove restoration, it keeps an unresolved, root-protected journal, reports a high-signal warning, and the new daemon's RAPL module enters **unarmed / no new writes** until a compatible recovery action can inspect it. Normal governor, EPP, turbo and update controls must continue working.
- A nonzero `ExecStopPost=` could cause `systemctl stop` failure and indirectly block the pre-existing `--update` flow. Therefore, **do not ship a cleanup unit whose failure propagates to block normal updater operations**. Investigate documented systemd `-` command prefix, which records a failed command but does not mark the entire stop failed, together with explicit error output and retained journal. This is not approval to silently ignore failures: real recovery success must be determined from the journal/readback, never from the service stop status.
- Never delete the journal merely because the cleanup command was attempted or because the update proceeds. In an unresolved case, don't reapply RAPL in the new version; give a clear manual remediation path. Assess how to retain a compatible recovery implementation if an old generation is eventually removed, but **do not rewrite the release manager solely for RAPL**.
- **Full final uninstall** is a separate workflow from a routine version update. Investigate the safe warning/recovery strategy for a final uninstall with pending state; do not silently remove recovery records or promise seamless cleanup when it failed. If satisfying this requirement would demand intrusive modifications to the existing updater, postpone active-writer enablement rather than regress the updater.

**Minimal-change design criterion:** first prefer adding one package-provided restore entry point, adding `ExecStopPost` plus runtime directory preservation to the *managed service unit*, and wiring a single opt-in daemon controller. No change to upstream `auto-cpufreq-installer`, source install/remove scripts, release layout, wrapper or current-symlink activation should be included in the first implementation unless a reproducible incompatibility proves strictly necessary. Test existing installed-unit `cmp` ownership checks across version transitions, without bypassing them.

**Normative sources:**
- [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html): `systemctl restart` is a stop/start, `ExecStopPost` runs on stop and abnormal termination; executable `-` prefix records failure but does not propagate failure to the service operation.
- [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html): `RuntimeDirectoryPreserve=yes` persists across stops until reboot, not permanently.
- [Linux Powercap](https://docs.kernel.org/power/powercap/powercap.html): changing a constraint is independent of the userspace daemon process lifetime.

**Regression gates before implementing active writes:**
1. Normal update with RAPL *disabled* produces the same updater exit code, current symlink, rollback and service state as upstream.
2. Normal update with RAPL *enabled*: old service stop recovers baseline, updater proceeds unmodified, new version restarts and conditionally reapplies config (subject to new hardware/support checks).
3. Cleanup failure: updater is not stuck due to the cleanup hook, journal survives, user receives a warning, and the new daemon runs normal CPU policies but does no RAPL writes.
4. Failed version activation and rollback: old daemon can restart; no orphaned Powercap state or destructive journal overwrite.
5. No additional permanent service, release-switch wrapper, custom install hook, or installed-unit ownership bypass is introduced for RAPL.

This is a **planning correction only**. Existing code, updater and service on the user system remain untouched.

---


**Status:** research and design only; no service/daemon changes authorized by this document.  
**Owner constraints:** functional final upstream PR only. No development tests, plans, logs, or internal documentation in the final PR. The GTK UI remains read-only. RAPL writes are opt-in and initially disabled. Do not modify the user's running notebook while preparing or validating this plan.

## 0A. Hardware/firmware resets and proportional recovery (23 October 2026)

**Design clarification prompted by existing notebook observations:** The kernel Powercap ABI **does not promise that a sysfs RAPL constraint reverts on daemon exit or stops being effective when its writer disappears**. A Powercap write targets platform power controls, not a daemon-scoped lease. Conversely, firmware/EC/thermal drivers **may** reset or rewrite limits on reboot, suspend/hibernate, charger changes, thermal events, and platform-profile transitions. An Intel power-profiles guide documents RAPL register reinitialization on reboot for its specific Panther Lake configuration, not as a guarantee on all CPUs.

References:
- https://docs.kernel.org/power/powercap/powercap.html
- https://docs.kernel.org/admin-guide/laptops/samsung-galaxybook.html
- https://docs.kernel.org/userspace-api/sysfs-platform_profile.html
- https://docs.openedgeplatform.intel.com/2026.2/edge-ai-suites/ai-suite-federal-and-aerospace/handheld-multi-modal-application/infrastructure/power-profiles.html

The Samsung `samsung-galaxybook` driver maps `low-power`, `quiet`, `balanced`, and `performance` to platform-specific ACPI/firmware performance modes. The generic ABI makes **no promise** that selecting a mode overwrites Powercap PL1/PL2. In the notebook's earlier tests, Fn+F11 platform-profile transitions did not visibly modify 28/41 W MSR values; hibernation did restore MSR 28/41 W and MMIO 15/41 W. The prior `/usr/local/sbin/apply-intel-rapl-limits` script was intended to **apply** 18/30 W, not a generic kernel-default reset; the associated services were later masked. These findings distinguish firmware reinitialization from a property of daemon stop.

**Revised engineering priority: choose the simplest recovery that passes tests.**

1. Start by experimentally establishing whether the platform/other daemons rewrite package limits after **ordinary user-space process exit**, **service stop**, **platform-profile transitions**, and **resume**; collect read-only before/after snapshots. Never infer the stop case from reboot/resume behavior.
2. Require a minimal **owner-aware restore on normal service stop** as first candidate: capture the pre-write baseline, journal the pending value before writing, compare current with last known owned value, and restore only where ownership is still credible. The small independent `ExecStopPost` helper may suffice; avoid inventing another init system, new release-management infrastructure, long-lived watchdog, or persistent cross-boot rescue service.
3. For unexpected termination, first verify whether normal systemd `ExecStopPost` already covers the cases at issue (including SIGKILL of the daemon, **not** a host-wide power loss). If it does, do not add a redundant recovery framework. Keep only the journal/lock features actually required by tested interruption points.
4. If the original power cap has *already been restored by firmware*, journal retirement must be idempotent and must not write the same value again. When external drift cannot be explained, do not overwrite firmware values or reapply RAPL; warn and continue ordinary CPU policy.
5. Maintain same-boot, short-lived state as needed to recognize known in-flight operations. Do not attempt cross-reboot restoration of old values: the firmware may set new safe caps at boot. Avoid blocking ordinary version updates; if cleanup fails, retain an explicit warning and suspend only RAPL in the new process.
6. **Do not eliminate restoration solely because the user's Samsung usually resets limits.** Upstream must also work on machines where Powercap changes remain active indefinitely after a writer exits. If the minimal approach cannot be proven safe, keep active RAPL unimplemented/read-only rather than overengineering the updater.

**Validation gate:** compare the *observed* PL1 and PL2 of each recognized MSR/MMIO package at service stop and after ordinary service restart, with both the old 18/30 W script and any other known RAPL actor inactive. Validate on a mocked sysfs/systemd lab first. Do not change the user's actual notebook during this planning step.

---

## 1. Sources and what they actually guarantee

1. [systemd.service(5) — ExecStop and ExecStopPost](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)
   - `ExecStopPost=` executes after service termination, including unexpected main-process exit and failed service startup. In a restart the service is stopped first. It must not rely on the main daemon still running.
   - `$SERVICE_RESULT`, `$EXIT_CODE`, and `$EXIT_STATUS` are diagnostic context, not proof that the RAPL write occurred or was restored.
   - `TimeoutStopSec=` also applies to stop/cleanup command phases: time-limited recovery is not guaranteed to finish.
   - `Restart=on-failure` does not establish RAPL restore success; failure of a cleanup command can affect unit result or restart behavior. Test these transitions rather than assuming them.
2. [systemd.exec(5) — RuntimeDirectory and StateDirectory](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html)
   - `RuntimeDirectory=` is removed at stop by default. `RuntimeDirectoryPreserve=yes` retains it after ordinary stop and restarts; `restart` is not sufficient for manual stop/failure recovery.
   - `/run` is tmpfs and disappears at reboot. `StateDirectory=` is persistent under `/var/lib` and changes the cross-boot threat model.
   - The generated runtime directory and mode are systemd-managed; do not silently create a weaker directory via Python before verifying ownership/symlink rules.
3. [systemd-analyze(1)](https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html): `systemd-analyze verify` checks service unit syntax and references, but cannot certify recovery semantics.
4. [flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html): Linux `flock` is an **advisory** cooperative lock. `LOCK_EX|LOCK_NB` rejects concurrent participating processes; it does not exclude thermald, DTT, BIOS or other uncooperative writers.
5. [Linux Power Capping Framework](https://docs.kernel.org/power/powercap/powercap.html): Powercap represents control types, zones, constraints and optional attributes, including power limits in microwatts. It has no documented atomic compare-and-swap or exclusive ownership API.
6. [Intel Package Power Control](https://edc.intel.com/content/www/us/en/design/platforms/core-processor-series-3-datasheet-volume-1-of-2/002/package-power-control/): PL1/PL2 and windows have distinct semantics. Thermal/electrical system properties are vendor- and model-dependent; do not infer writable caps or appropriate power settings from a SKU's base/turbo power.
7. **Exact code under review:** upstream `AdnanHodzic/auto-cpufreq` `master` at `5a1683037a583785f802b5d039022127b9b154cd` (3.2.0). Inspect:
   - `scripts/auto-cpufreq.service`
   - `scripts/auto-cpufreq-install.sh`
   - `scripts/auto-cpufreq-remove.sh`
   - `auto-cpufreq-installer`
   - `auto_cpufreq/bin/auto_cpufreq.py`
   - `pyproject.toml`, `nix/module.nix`
   - Existing lab `auto_cpufreq/modules/intel_rapl.py` and `rapl_policy.py`.
   These specific repository findings are implementation evidence, not normative kernel/systemd guarantees.

## 2. Audited integration hazards

### Hazard A: obsolete service paths

The old experimental RAPL unit uses `/opt/auto-cpufreq/venv` and `PYTHONPATH=/opt/auto-cpufreq`. Upstream 3.2.0 uses `/opt/auto-cpufreq/current/venv`, `PYTHONPATH=/opt/auto-cpufreq/current`, and a release-management symlink. **Never copy the old unit verbatim**; especially do not replace the current `ExecStart` with the obsolete one.

### Hazard B: release replacement and helper provenance

The upstream installer builds a complete versioned tree under `/opt/auto-cpufreq/releases/`, then atomically switches `current`. The restore helper must remain usable while old journal metadata exists. A helper pointed at `current` might change implementation during an upgrade or rollback, and older journal schemas must be interpreted safely. A helper path pinned to a release needs protection from release deletion. Investigate both before choosing; favor minimal, documented lifecycle changes over introducing an unrelated new installer.

The source installer guards against replacing an installed daemon and uses a daemon-removal marker; analyze the actual update/rollback sequence including a failed candidate and a partially completed removal.

### Hazard C: managed-unit equality

The upstream systemd install/remove scripts compare the installed unit with the version under the active release; a differing/mutated unit is considered unmanaged and removal is refused. Adding `ExecStopPost`, `RuntimeDirectory=`, etc. changes these bytes. Plan the transition: remove the old managed daemon **before** replacing the release, then install the new unit, without losing recovery state. Test both clean upgrade and a failed update where the old binary remains authoritative.

Do not silently overwrite a custom unit. If the detected unit is unmanaged, disable active RAPL and give a specific actionable diagnostic.

### Hazard D: complete uninstallation (distinct from ordinary updates)

The source installer's `tool_remove` eventually removes the entire installation root. An unresolved journal must not be silently discarded or presented as restored. **Do not treat a routine version update as a final uninstall, and do not add an unconditional blocker to the existing update path.** For final uninstall, research a narrowly scoped warning/retry/rescue procedure, preserving recoverability. If that cannot be achieved without a disruptive overhaul of the current release manager, defer active RAPL writes rather than add invasive updater logic. Preserve normal uninstall behavior on systems that never enabled RAPL.

### Hazard E: non-systemd packaging

Upstream supports Dinit, OpenRC, runit, s6 and special distro packaging; NixOS defines its own systemd service `ExecStart`; Snap/AUR may use different layouts. `systemd.service` changes alone do not secure them. Initial active writer is supported only in the audited source-installed systemd service. All other environments remain read-only, retaining their existing frequency management. Do not claim they provide RAPL restoration until separate lifecycle verification.

### Hazard F: no true write atomicity

Kernel sysfs accepts individual writes; journal atomic rename/fsync is an *application-level* mitigation. There remains a race with firmware and other daemons. Save *write intent* before modifying a constraint, re-read under lock, and verify post-write state. Restore only when current observation matches known owned/in-flight states. Never blindly normalize Intel MSR and MMIO or overwrite a different external value.

### Hazard G: startup and daemon availability

No startup/preflight command should disable all existing auto-cpufreq functionality merely because optional RAPL control cannot be armed. A systemd control command that exits nonzero can prevent the service starting. Design fail-closed RAPL readiness as an isolated state within the daemon, not an unconditional `ExecStartPre` failure. On an unresolved journal at the beginning of a daemon run, **do not apply new caps**. Use the same cross-process lock and reconciliation logic as the stop helper. Allow governor/EPP/turbo code to continue normally.

## 3. Chosen baseline architecture (subject to lab gates)

**One runtime RAPL manager**, cached for the lifetime of the daemon and independent of HWP/CPUFreq backend, holds the decision to apply a constraint. It uses exactly one root-owned journal and lock.

**One separate restoration entry point**, packaged as a versioned Python console script and importing only the RAPL controller/discovery modules (no `core.py`, GTK, config notifier or daemon main). Its work is synchronous, idempotent and noninteractive.

**A shared journal schema** stores original value, last confirmed owned value, pending target (if any), canonical control/zone/constraint identity, schema version and suitable same-boot identity. Multiple constraints are independently recorded; no claim of cross-controller atomicity. Unknown schema versions must abort without writes or loss of state.

**One cooperative lock** on a root-controlled `0600` regular file in a `0700` directory. Apply, cleanup, and startup reconciliation all take the lock. Protect against symlink-following and unexpected owner/mode. Any lock or journal validation error disables *only RAPL*.

**No configuration-dependent restoration**: restoring must operate on the trusted journal even if `enable_rapl_envelopes=false` or the config file was removed after the original write.

**One-way enablement gate**: only the audited and installed systemd lifecycle can allow active RAPL writes. A string environment variable alone is not proof; choose an install-time integration proof or systemd-managed readiness check that can be tested without allowing unsupervised `--daemon`, `--monitor`, `--live`, `--stats`, or GTK to modify Powercap.

### Proposed state transitions

- `disabled`: absent/false opt-in, no writes; outstanding owned journal must still be reconciled.
- `unsupported` / `ambiguous`: no writes, non-spamming diagnostic.
- `unarmed`: valid opt-in, but no verified recovery lifecycle, no writes.
- `ready`: valid config and selection, writable candidate and verified compatible restoration mechanism; no unresolved journal.
- `pending`: persisted intention before single sysfs write. Must be retained on failed write/readback.
- `owned`: readback confirms an applied value with persistent ownership record.
- `yielded`: unexpected drift; retire that ownership conservatively and suppress re-acquisition, keeping diagnostics.
- `recovery_failed`: attempted cleanup couldn't prove safe restoration; preserve journal and disable further writes.
- `cleared`: restored or proven already at baseline; delete only that specific record under lock.

The journal is not permission to overwrite; it is evidence to *consider* whether cleanup is safe.

## 4. Service unit design: evaluate in isolated lab, not deploy now

Illustrative directives, **not an approved production patch**:

```ini
[Service]
Type=simple
User=root
WorkingDirectory=/opt/auto-cpufreq/current/venv
Environment=PYTHONPATH=/opt/auto-cpufreq/current
RuntimeDirectory=auto-cpufreq
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
ExecStart=/opt/auto-cpufreq/current/venv/bin/python /opt/auto-cpufreq/current/venv/bin/auto-cpufreq --daemon
ExecStopPost=/opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl
Restart=on-failure
```

This *illustrates* the systemd lifecycle and 3.2.0 paths; it **does not prove helper stability across release switch**. Confirm whether to use a stable package-owned launcher that resolves the correct compatible helper, with tests against stale/missing versions and old journal schema. Do not apply the unit above without those tests.

No `WatchdogSec` in this initial change. Only enable after proving `sd_notify WATCHDOG=1` support and reasonable timing. The documented systemd `-` prefix is a **candidate** for isolating an optional recovery-hook failure from the routine update/stop result; if used, the helper must still log its real status, preserve unresolved journal state, and prevent later RAPL writes until resolved. Test the exact `systemctl stop` / updater behavior.

**Runtime journal proposal:** `/run/auto-cpufreq/power-state.json`, root `0600`, parent root `0700`, `RuntimeDirectoryPreserve=yes`, same-boot only. Test manual stop, daemon crash, auto restart, and failed cleanup; the file survives within the same boot. Do not claim recovery after power loss/reboot. Persistent `StateDirectory` across reboot is deferred until a safe boot-id + hardware identity + no-stale-restore policy exists.

**Stop timeouts:** choose bounded stopping and helper operation timeouts empirically. `TimeoutStopSec` applies to cleanup phases. A killed/hung helper must leave journal state intact, and the next start must enter `recovery_failed` / `unarmed` until reconciled.

**Logging:** report cleanup action and `$SERVICE_RESULT` diagnostics through journald. Distinguish success, already restored, drift/no overwrite, inaccessible constraint, journal corrupt, lock busy, helper unavailable, and cleanup timeout. The helper reports true success/failure via its exit code and journal; service-unit failure handling must be tested separately so an optional hook cannot block routine updates. Do not infer successful recovery from `systemctl stop` alone.

## 5. Execution sequence (all development tests remain on lab branch)

### Gate 0 — provenance snapshot

Record upstream commit, actual systemd unit, installer version, `pyproject` console entry points, generated scripts, supported init systems and known active branch SHAs. Freeze a fake-sysfs fixture. Do not touch host Powercap.

**Pass:** no uncertainty about source of `ExecStart`, helper and unit.

### Gate 1 — recovery helper and journal semantics

In isolated Python tests, validate that a minimal console restorer can read and validate journal, acquire same lock, distinguish `owned` and `pending`, and return actionable exit codes. Introduce schema/version compatibility tests and failed fsync/replace/unlink fault injection. Require strict no-side-effect imports.

**Pass:** every synthetic lifecycle interruption either safely restores/proves no change or preserves unresolved journal without touching unrelated limits.

### Gate 2 — service unit and startup handshake

Prototype unit in a lab systemd manager/container/VM. Verify with `systemd-analyze verify`; then exercise real manager stop/start/restart behavior using fake sysfs. Ensure daemon RAPL remains unarmed unless helper and service lifecycle are actually configured. Do not rely only on env flags. Test service with invalid, missing, or unmanaged unit without disrupting normal CPU management.

**Pass:** all stop/restart paths invoke helper where documented; no false cleanup claims; ordinary daemon works if RAPL unsupported/unarmed.

### Gate 3 — source installer upgrade and removal

Test fresh installation, existing 3.2.0 installation, versioned release switch, rollback on failure, remove/reinstall, stop failure, unresolved journal at uninstall, missing helper, and mismatched/unmanaged unit. Account for the source installer's marker and unit-content `cmp` protections. Ensure no successful uninstall deletes the only compatible recovery implementation while unresolved caps exist.

**Pass:** normal update remains compatible and unblocked; pending recovery prevents *new RAPL writes* while preserving a recoverable journal and clear diagnostics. Full uninstall with unresolved recovery receives separate safe handling. Do not alter the versioned installer's established switching/rollback contract.

### Gate 4 — control scope integration

Wire lab `rapl_policy.py` to a cached manager, not HWP. Enforce `[intel_power]` opt-in, explicit MSR/MMIO selector on dual-interface machines, explicit package selector on multiple packages, zero writes on unsupported hardware. On config switch, reconcile old-owned constraints before allowing new scope. Distinguish observed from writable and active.

**Pass:** no write to unselected MSR/MMIO/package, no write while `unarmed`, no governor regression.

### Gate 5 — failure and concurrency matrix

On synthetic sysfs + systemd environment: `SIGTERM`, `SIGKILL`, helper exit status 1, helper timeout, lock occupied, false/stale boot-id, external drift, kernel attribute disappearance, two daemon processes, failed journal write, false-positive readback, bad `enabled` value, unreadable lock, and failed daemon launch. Include power outage/reboot **as expected limitation**; do not mislabel them as covered.

**Pass:** no unintended write; pending state survives same-boot stop where possible; no endless restart/write war.

### Gate 6 — explicit real-hardware test authorization

Read-only baseline first: model, kernel, both control types, zone/constraint names, enabled status, PL1/PL2 values, systemd unit and other thermal managers. Only with separate explicit authorization, test a modest **downward** change on one selected constraint and restore baseline. Never touch time windows or raise a stricter OEM cap.

**Pass:** verified readback and exact permitted restoration with other interfaces unchanged. If hardware writes are rejected, record unsupported/locked behavior and still pass no-write safety.

### Gate 7 — clean contribution assembly

Port only validated functional code into `feat/intel-rapl-gtk-stats` on upstream. Remove accidental testing files, workflows, reports and docs from **both tree and final branch history** (avoid mere delete-last-commit). Verify `compare upstream/master...feature` file list and CI. No upstream PR until full active-writer gates pass.

**Pass:** only implementation files, reproducible build results, no internal documents/tests and no fork-only unrelated modifications.

## 6. Architecture decisions deliberately deferred

- Persistent `/var/lib` recovery across reboot: unsafe without boot/generation identity and a hardware-state policy; `/run` deliberately scopes restoration to same-boot session.
- Support for Snap/NixOS/other init ecosystems: read-only initially until a documented equivalent lifecycle exists.
- Automatic writes to both MMIO/MSR: forbidden by default; no forced equality or raising of OEM limits.
- Active RAPL in `--live` or unsupervised manual daemon: disabled until independent cleanup lifecycle proven.
- More complex watchdog, sd-notify startup protocol and additional systemd units: not introduced without a documented need.
- Multiple package/constraint atomic transactions: Linux Powercap offers no such primitive; report partial status rather than pretend it does.

## 7. Code review concerns before implementation

1. The lab `RaplStateStore.locked()` currently uses `parent.mkdir` and `os.chmod` before checking full parent ancestry; inspect root ownership, insecure pre-existing paths, and race conditions, not merely filename symlink protection.
2. Journal reads/writes currently have no per-record boot-id/generation binding. In the first same-boot version, verify that `/run` really is authoritative and that an administrator-copied stale journal cannot overwrite a new boot's limits.
3. The lab's pending recovery infers ownership from value equality. Identical firmware values do not establish exclusive ownership. Document remaining race and stop if ambiguous.
4. `restore_owned()` must correctly report all failure actions, not just literal `restore-failed`. The legacy helper's limited failure classifier is not sufficient for new `pending` / `unsafe-readback` outcomes.
5. The implementation has two discovery modules (`intel_power.py` and `rapl_capabilities.py`). Avoid divergent zone identity, canonical path checks, or inconsistent enabled-state handling. Prefer a shared capability layer once the behavior is proved.
6. The daemon's current 2-second loop is unrelated to a two-second entitlement to override firmware; apply only in response to valid opt-in state / source transitions and suppress reacquisition after drift.
7. Upstream Nix module forces a different ExecStart, so Linux/Nix CI green **does not** prove matching recovery lifecycle in every packaging method.
8. Current desktop/GUI must remain passive; there is no path for GUI to mark recovery successful or authorize a write.

## 8. Acceptance summary

**Allowed now:** read-only telemetry and internal development of the isolated recovery helper, journal, service integration tests and installer compatibility tests.

**Disallowed until gates 1–5:** enabling physical RAPL writes in a user-facing build or modifying the notebook's live service. Synthetic CI pass alone is necessary but not sufficient.

**Final PR policy:** only source code and functional integration files; no tests, extra workflows, internal documents or plans.

### Evidence links

- Official systemd service: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- Official systemd process environment: https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
- Official unit validation: https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html
- Official kernel Powercap: https://docs.kernel.org/power/powercap/powercap.html
- Linux flock(2): https://man7.org/linux/man-pages/man2/flock.2.html
- Intel datasheet: https://edc.intel.com/content/www/us/en/design/platforms/core-processor-series-3-datasheet-volume-1-of-2/002/package-power-control/

## Field diagnostic — GalaxyMint, 2026-10-23 (read-only; no write validation)

Observed notebook: Linux XanMod 7.2.12-x64v3-xanmod1, Samsung platform profile `performance`; active upstream `auto-cpufreq.service`, inactive `thermald`, `power-profiles-daemon`, `tuned`.

Kernel sysfs readback this boot: `intel-rapl:0` package PL1 28 W, PL2 41 W; `intel-rapl-mmio:0` PL1 15 W, PL2 41 W. Both zones `enabled=1`. Original custom apply script at `/usr/local/sbin/apply-intel-rapl-limits` targeted **both** MSR and MMIO with PL1 17 W and PL2 35 W; raising MMIO PL1 from OEM 15 W to 17 W would violate the new policy. Do not reuse or invoke it as the new writer.

`intel-rapl-custom-clamp.service` was installed/enabled but **skipped** at +25.928651 seconds, because `ConditionPathExists=/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw` was false. Kernel journal first logged Intel RAPL discovery at +26.285184 seconds, then MSR support at +26.888886 seconds; by the time the user manually checked, sysfs attributes existed. **Likely startup race (not proof of a universal probe time):** systemd conditions are evaluated when the start job executes; `After=auto-cpufreq.service` only orders the daemon service, not Powercap/driver availability. Avoid a startup-only condition that can silently skip optional RAPL indefinitely.

The `intel-rapl-resume.service` and `intel-rapl-custom-clamp.service` both were observed enabled but inactive, and were subsequently disabled manually; the systemd status confirmed both disabled. The RAPL limits at defaults this boot **do not prove automatic restoration after a writer exits**, because the writer was skipped before any successful write. Do not use this boot as evidence that a restore helper is unnecessary.

**Next tests:** observe PL1/PL2 read-only before and after manual platform-profile transitions; do not run the old script. To establish daemon-stop persistence, a future separately authorized test must make a reversible downward write to *one* selected interface, then observe process/service exit and readback; ensure restoration/fault handling before proceeding.


## Field diagnostic follow-up — Samsung platform profile transition

The user manually changed the Samsung platform profile with Fn+F11 from `performance` (19:19:04) to `low-power` (19:19:15) with no direct RAPL write. All four package limits remained unchanged:

| Controller | PL1 before | PL1 after | PL2 before | PL2 after |
| --- | ---: | ---: | ---: | ---: |
| `intel-rapl` | 28 W | 28 W | 41 W | 41 W |
| `intel-rapl-mmio` | 15 W | 15 W | 41 W | 41 W |

Interpretation: **this particular platform-profile change does not reprogram the exposed PL1/PL2 limits**. It does not rule out EC fan/temperature controls, thermal throttling, different policy behavior under load, later asynchronous changes, or changes during suspend/hibernate. In particular it does **not** establish that the kernel resets a value written by a process when that process exits.

Upstream Linux docs:
- https://docs.kernel.org/admin-guide/laptops/samsung-galaxybook.html — the platform profile selects Samsung ACPI-specific firmware performance modes, not a specified set of Powercap writes.
- https://docs.kernel.org/userspace-api/sysfs-platform_profile.html — the generic interface is for selecting platform power/temperature/fan policy, and it does not expose every resulting hardware policy decision.

This strengthens the earlier decision to **avoid requiring profile-based restoration** of a written RAPL value. For a definitive process-exit persistence test, use a separately authorized, individually bounded **downward** write to *one* selected constraint, performed by a short-lived subprocess and observed by an independent parent/supervisor that restores the recorded baseline only if it still owns the value. Validate against fake sysfs first. Do not reuse the legacy script, which writes to both controllers and raises the 15 W MMIO baseline to 17 W.

---


## Field evidence — short-lived RAPL writer persistence

Test date 2026-10-23, Samsung GalaxyMint on XanMod 7.2.12; user-executed supervised experiment. This is **observed hardware behavior**, not a universal kernel ABI guarantee.

**Baseline:** Intel RAPL MMIO package-0 named `long_term` = 15,000,000 microW; other RAPL constraints were not targeted by the test. Both custom RAPL systemd units were previously disabled; upstream auto-cpufreq daemon remained active.

**Procedure:** a root-owned short-lived Python subprocess re-read MMIO PL1, wrote 12,000,000 microW to its named `long_term` constraint, then exited with code 0. Separate supervisor read the exact same constraint immediately after exit and again three seconds later. Supervisor restored 15,000,000 microW only when current readback still equaled its requested 12,000,000 microW, then verified the readback.

**Observed terminal evidence:**

```text
PL1 MMIO inicial: 15.000 W
Processo filho encerrado: código 0
PL1 logo após saída do filho: 12.000 W
O limite permaneceu alterado após o processo sair.
PL1 após 3 segundos: 12.000 W
Restaurando PL1 MMIO para 15 W...
PL1 MMIO final: 15.000 W
Restauração verificada.
```

**Interpretation:** exit of the writer did **not** automatically restore a Powercap package limit within the measured three-second interval. Successful write and explicit restoration were confirmed by readback. This experiment did **not** test a systemd daemon stop or a full reboot/hibernate cycle, nor does it establish indefinite persistence or rule out later firmware changes.

**Design consequence:** retain a **minimal, independently verified cleanup on daemon service stop**; no justification to remove the RAPL restore hook based on firmware reset assumptions. Do not modify the versioned updater: normal stop invokes cleanup, then the existing installer can switch versions and the restarted daemon may reapply an explicitly enabled profile. Simulated failure tests and recovery-state safety remain mandatory before active-write release; do not add cross-boot restoration or a new update mechanism without separate evidence. The lab controller already has journal/locking experiments, but active RAPL remains unshipped.


## Laboratory implementation checkpoint — 2026-10-23

The **laboratory only** branch `lab/intel-rapl-capability-20261023` now has the first P0 recovery corrections and a minimal, independent restore command. Final working commit `492d52c717e62edce70913b275d19137a42fc7df`.

**Corrections already implemented in the lab:**
- A restore with an uncertain post-write value preserves a durable pending intent. It no longer removes ownership merely because the readback differs from the previous value.
- An unexpected post-write readback higher than the baseline (or invalid) is not silently claimed as an auto-cpufreq-owned value and is not overwritten in an eager rollback. The controller yields and preserves pending recovery metadata.
- If firmware has already restored the exact external baseline, cleanup retires the journal entry without writing the sysfs constraint.
- A dangling `enabled` symlink and malformed `package-...` zone name cannot authorize Powercap writes.
- RAPL journal, lock and immediate runtime directory now require restrictive permissions, expected service-user ownership and regular-file/directory type checks. Journal reads use `O_NOFOLLOW` and `O_NONBLOCK`; lock opens use `O_NOFOLLOW`. Pre-existing insecure files and directories are rejected instead of silently chmodded. These controls are **not** a proof against all ancestor-directory races; review with systemd-managed RuntimeDirectory before shipping.
- `auto_cpufreq/bin/auto_cpufreq_restore_rapl.py` implements a separate CLI restore process with no GUI/core/notifier dependency. `pyproject.toml` exposes `auto-cpufreq-restore-rapl` (lab only). It returns nonzero for an unresolved or unreadable journal; normal outputs go to stderr for journald capture.

**Evidence:** three targeted recovery regressions originally failed against the old implementation and passed after the corrections; additional file-permission, symlink, malformed-path and helper tests passed. Lab GitHub Actions `Intel RAPL lab checks` run [#40035814189](https://github.com/lucasleocs/auto-cpufreq/actions/runs/40035814189) PASS **70 tests on Python 3.9 and 3.12**. Read-only feature branch and upstream-aligned fork `master` remain unchanged. The laboratory has not installed a unit, called real Powercap writes, or changed the user notebook.

**Remaining next gate before the active writer is deployable:** validate `ExecStopPost` and `RuntimeDirectoryPreserve` with a real, isolated systemd manager; confirm the helper remains available through source-installer updates and rollbacks; harden startup readiness and eliminate divergent discovery implementations. Any installation-mode not verified with an equivalent restore lifecycle remains RAPL read-only. Keep the installer/versioned `releases/current` flow unchanged unless a narrow, reproducible incompatibility demands it.


## Real-systemd laboratory checkpoint — 8 scenarios (2026-10-23)

**Branch:** `lab/intel-rapl-capability-20261023`, checkpoint commit `93b32e8a99039d3d44939138559762e485702c6d`.

Added `internal_tests/rapl_systemd_fixture.py` and `internal_tests/rapl_systemd_integration.py` and a *lab-only* real systemd job in `.github/workflows/rapl-lab.yml`. The job uses an Ubuntu GitHub Actions VM with **systemd as PID 1**. The unique test unit is created under `/run/systemd/system/auto-cpufreq-rapl-lifecycle-lab-*.service`; all fake Powercap controls and ownership journals are under that unit's separate `/run` directory, with automatic fixture/unit cleanup. No live machine/sysfs Powercap was written, no real auto-cpufreq service was installed, and no installer was executed.

The **experimental** `scripts/auto-cpufreq.service` includes `RuntimeDirectory=auto-cpufreq`, `RuntimeDirectoryMode=0700`, `RuntimeDirectoryPreserve=yes`, and `ExecStopPost=-/opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl`; this is deliberately not in fork `master` or the clean upstream PR branch. The integration harness **derives its unit from this actual lab service file** (substitutes only executable paths, private runtime directory, and test restart/timeout settings) and runs `systemd-analyze verify` against the substituted unit.

**Observed PASS**, [Intel RAPL lab checks run #40036386363](https://github.com/lucasleocs/auto-cpufreq/actions/runs/40036386363), real-manager scenarios:

1. Ordinary service stop invokes `ExecStopPost`, restores fake PL1 from 12 W to 15 W, and retires the journal.
2. `systemctl restart` runs cleanup before reapplying on start; event order explicitly checked.
3. `SIGKILL` of the main daemon triggers cleanup and standard `Restart=on-failure`, with restoration before next acquisition.
4. An atomic `current` symlink switch from simulated release v1 to v2 and rollback to v1 restores state between generations.
5. Injected write failure in `ExecStopPost` leaves the journal and 12 W unchanged but the normal `systemctl stop` succeeds due to the documented `-` prefix.
6. Restart after unresolved journal allows daemon startup but does not perform another fake RAPL write.
7. After fixing cleanup, a blocked daemon can stop, restore the baseline, and safely re-arm RAPL only after state reconciliation.
8. Failed daemon startup *after fake PL1 acquisition* still invokes `ExecStopPost`, restores 15 W and removes the journal.

Pure-Python synthetic tests still pass on Python 3.9 and 3.12 (70 tests per interpreter as of this checkpoint).

**Strict interpretation:** this validates real systemd lifecycle semantics and the *simulated* release-switch order, **not** a full execution of upstream `auto-cpufreq --update`/installer, and not Linux kernel/firmware behavior on other hardware. The production writer is not connected to the daemon; no support on NixOS/Snap/AUR or other init systems is implied. The next gate is precise integration into actual daemon config/AC-battery policy, dynamic Powercap discovery, and a real source updater/rollback regression harness if it can be run in a disposable environment. Keep all unsupported deployments RAPL read-only.

Official references:
- https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html (`ExecStopPost` and `-` command prefix)
- https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html (`RuntimeDirectoryPreserve`)

