# Frontend and runtime-mode unification — working architecture plan

> **Experimental planning note, not production code.**
>
> Repository: lucasleocs/auto-cpufreq
> Working branch: test/issue-860-runtime-20261023
> Review date: 2026-10-23
> Targets: GTK dashboard, GTK Monitor, terminal `--stats`, `--monitor`, `--live`, and daemon reporting.
> Background: upstream merged PR #964 (shared SystemReport and GTK runtime state); issue #860 requests actionable GUI diagnostics.
>
> This document is intentionally stored under `investigations/`. Do **not** include it in the eventual production PR. It records hypotheses and requirements, not claims that the work is already implemented.

## Implementation ledger — experimental branch, 2026-10-23

This is a **partial implementation**, not the complete roadmap. All changes below remain in the experimental branch, not production or upstream master.

| Phase | Current state | Verified scope |
| --- | --- | --- |
| 1. Logging concurrency hardening | Implemented | Atomic queue shutdown, traceback detachment, async writer, serialized pruning and tests |
| 2. Toolkit-neutral diagnostics | Implemented | Shared `modules/diagnostics.py` with GTK adapter; terminal session logger independent of GUI toolkits |
| 3. Typed presentation semantics | Implemented and used | `modules/report_presentation.py`; GTK dashboard/Monitor and Urwid Stats/Monitor/Live |
| 4. Shared refresh coordinator | Implemented and used | `modules/report_refresh.py`, with separate GLib and Urwid delivery adapters |
| 5. GTK integration | Implemented, automated integration tested | Dashboard coalescing/pending-control behavior, monitor recovery and closing during collection |
| 6. Urwid integration | Implemented, automated PTY tested | Stats/Monitor/Live *display* through `SystemMonitor`; no policy executor changes |
| 7. Terminal diagnostics and process health | Implemented, automated tests | `--diagnostic-log` on Stats/Monitor/Live, GTK opt-in, low-frequency `/proc` CPU/hottest-TID sampling, private file paths and tests |
| 8. Rendering cost investigation | Synthetic CI baselines complete; code optimization deferred | GTK/Urwid 4/8/16/32-core widget benchmarks and `/proc` overhead measurements; no convincing high-CPU hotspot in isolated rendering |
| 9. Policy-decision explanation | Not implemented | Shadow-only, never calls policy writers |
| 10. Production PR and hardware validation | Not started | Clean branch and manual Linux Mint X11 validation required |

**Safety boundary:** `--live`'s existing privileged policy worker and restoration code are unchanged. The daemon control loop and power writers are unchanged. The new CLI diagnostic flag does not affect control-policy decisions. A separate safety review is still required for the known Live shutdown race.

**Validation status:** The process sampler extended the suite to **59 Python/terminal + 7 GTK/Xvfb tests**, passing in https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053610334. The same CI run measured synthetic GTK/Urwid rendering and `/proc` sampler overhead. Linux/Nix build statuses for a particular commit must be checked separately; CI tests do not replace full Linux Mint Cinnamon X11 runtime testing.

**Remaining risks/next work:** full-runtime CPU/RSS/FD/thread baselines with debug OFF and ON; prolonged execution and suspend/resume; real sudo Stats diagnostic smoke test without touching power state; Python support/packaging; policy explanation kept read-only; production-clean branch. No claim upstream #860 is fixed. Render microbenchmark analysis is kept separately in `investigations/issue860-health-and-render-baseline.md`; avoid widget caching until real end-to-end evidence justifies it.

This working plan and the experimental workflow must not be included in the eventual clean upstream PR.

## Hardening review — October 23, 2026 (current experimental iteration)

Implemented without changing power writers, active daemon, or installed GTK/Stats:

- **Thread ownership:** `ProcessHealthSampler.stop()` signals termination and retains the worker reference if a blocked `/proc` read outlasts the 200 ms join. A subsequent start refuses to create a duplicate worker while the previous one is still alive. The shared session logger likewise retains an active retiring file writer after its bounded shutdown wait, and refuses another logging session until that writer exits. The GTK adapter also tracks its watchdog thread and refuses another activation while a previous watchdog remains alive; normal repeated toggles interrupt and join the watchdog safely.
- **Error visibility:** the toolkit-independent diagnostic engine stores last write failure and a monotonically increasing error count, retrievable from the UI. GTK retains its own GLib error notification; Urwid shows a persistent footer warning on the main loop when a new writer error occurs, without touching widgets from the background thread.
- **Display robustness:** Urwid catches unexpected `_display_report()` exceptions, acknowledges the refresh result and schedules another attempt. Temporary sensor errors remain distinct from rendering errors.
- **Data parity:** terminal battery percentage, AC state, charge thresholds and wattage now use the same typed interpretation as GTK rather than direct formatting. This does not refactor energy policy or driver APIs.
- **Sampler reliability:** persistent `/proc` errors are rate-limited, subsequent recovery is logged, and a sustained high-CPU episode optionally records a bounded Python stack for the hottest Linux TID when the TID can be mapped to a live Python thread. This cannot obtain native-only stacks and is not a guarantee against GIL starvation. Stack trace paths may be sensitive; users must review logs before sharing them.
- **Lifecycle tests:** blocked sampling, blocked writer, background write error, real busy-thread detection and bounded Python stack capture, GTK watchdog reactivation, GTK Monitor open/close cycles, and Stats terminal hangup with diagnostics enabled; Urwid render error and shared battery semantics are covered by separate tests.

Validation reference for the first set of hardening changes: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40054500032 (68 Python/terminal + 8 GTK/Xvfb tests; Linux build/Nix both passed at commit `72ca91e6a74c`). Follow-on hot-thread stack capture has its own CI run and must be checked before the new commit is described as fully verified.

Not attempted by request: Python 3.9 compatibility adjustments or `pyproject.toml` version changes.

Still required before an upstream production PR: isolated real GTK launch and long-duration resource benchmark on Linux Mint, repeated suspend/resume, privileged CLI diagnostics inspection without changing energy policy, and building a clean PR branch from current upstream master.

## Additional hardening — shared collection lane and bounded diagnostics

This experimental phase implements the post-review robustness corrections without changing the daemon, `--live` energy policy, or the installed application.

- **No overlapping process collectors:** `report_refresh._ProcessCollectionLane` runs one short-lived daemon collection worker per process with a weak-reference queue. Each view still owns independent generations, rendering and cancellation. Closing the GTK Monitor while its sysfs/procfs read is blocked does not start a second concurrent read on reopen; queued jobs of closed views are canceled. The worker exits when its queue empties. This is process-local, not IPC between GTK and Stats.
- **Urwid delivery recovery:** `SystemMonitor` has a recurring two-second Urwid alarm that also drains finished reports when the `watch_pipe` write fails. Its pipe write end is nonblocking; the failure is logged once per episode, and the display shows a fallback warning rather than spinning or silently freezing.
- **Observable coordinator errors:** `ReportRefreshCoordinator` accepts a frontend-specific dispatch-error callback, with a standard logger fallback. GTK and Urwid log unexpected delivery failures without importing either toolkit into the coordinator.
- **Nonblocking GTK debug toggle:** the GTK menu no longer waits for joins when disabling debug. Ownership of still-running watchdog/sampler/writer is retained, and a re-enable request is rejected while workers are stopping. The final GTK process shutdown still makes bounded attempts to flush logs. Tests were adjusted to distinguish asynchronous stop signaling from eventual worker termination.
- **Safe log record queues:** queued `LogRecord` messages and traceback/stack text are length limited, and raw args and traceback frames are detached before entering the bounded queue. This trades a small amount of synchronous string formatting for bounded references/memory retention, not disk I/O.
- **Sampler robustness:** unexpected errors are reported and the process sampler disables itself after three consecutive unexpected faults rather than looping forever or dying silently; routine procfs errors remain throttled.
- **Regression tests:** serialized collectors across independent views; cancellation of a queued closed view; dispatch callback failures; Urwid broken pipe; bounded logger messages without args retained; unexpected sampler faults; the actual CLI entrypoint's Click-option-to-Stats/Monitor/Live constructor wiring (static AST check, with separate PTY runtime tests). No privileged modes are executed in these tests.

**Caveats:** the read-only collect function cannot forcibly cancel blocking kernel reads; the worker remains daemonized while a read is blocked, but concurrent process-wide collections remain capped. `Thread.join` at CLI shutdown remains bounded and no lossless crash logging is promised. The CLI wiring AST check is not a substitute for safely launching the real installed CLI using hardware-independent mocks. A rapid GTK debug re-enable may temporarily display a 'still stopping' error; the expected next step is to offer a deferred retry if this becomes a meaningful usability issue.

**Verification:** use the latest commit's Issue 860 Runtime Regression, Linux Build and Nix Flake CI runs, not older results. An isolated real Linux Mint/X11 or Wayland session test remains required before the production PR.

## 1. Executive decision

Pursue **one reusable, read-only observability stack** with separate GTK and Urwid event-loop adapters and separate privileged policy executors.

The shared stack should cover: collection, typed observed state, semantics/presentation, retry and refresh orchestration, diagnostics and process-health measurements. No permanent log daemon and no cross-process GTK/Stats singleton are necessary.

Preserve the essential boundary:

- **Observed state**: what kernel, battery, and drivers report now.
- **Suggested/explained state**: a prediction based on a documented input snapshot, not necessarily the daemon's actual decision.
- **Requested state**: an explicit user override or requested power action.
- **Applied state**: a completed privileged command, which does not establish immediate hardware convergence.
- **Re-observed state**: subsequent kernel readings used to confirm the effective result.

Refactoring the active daemon's write policy or changing power-service side effects is **not** required for unifying the interfaces, and should be a separate safety-reviewed change.

## 2. Grounded current-mode inventory

| Mode | UI loop | Observed data | Mutates power state? | Runtime preparation / cleanup |
| --- | --- | --- | --- | --- |
| GTK dashboard | GTK 3 / GLib | `SystemReport`, `SystemReportView` | Only via explicit privileged controls | Starts GUI worker refresh; local diagnostic lifecycle; GTK shutdown |
| GTK Monitor Mode | GTK 3 / GLib | `SystemReport` + governor/turbo heuristics | No | Own monitor worker/timer and back navigation |
| CLI `--stats` | Urwid | `SystemReport`, live collection | No | Daemon-running guard and optional warning prompt, Urwid loop/pipe |
| CLI `--monitor` | Urwid | `SystemReport` + governor/turbo heuristics | No policy writes in main mode | Daemon-conflict guard, current CLI root check, configuration notifier, Urwid loop |
| CLI `--live` | Urwid + separate policy worker | `SystemReport` | **Yes**, repeatedly via `set_autofreq()`; may stop competing power services | Root/daemon guards, battery daemon setup, config notifier, stop/restore power services; worker lifecycle needs separate review |
| CLI `--daemon` | Service/CLI control loop, no GTK or Urwid | `print_system_report()` `SystemReport` | **Yes** via governor/EPP/EPB/Turbo/profile/frequency controls | Privileged loop, service/setup, configuration notifier, legacy stdout redirection to stats file |

Notes:
- Current `--stats` **generates its own live SystemReport** through `SystemMonitor`, rather than simply following `/var/run/auto-cpufreq.stats` with `tail -f`. The old daemon stats file and `read_stats()` remain separate, legacy pathways.
- GTK dashboard and GTK Monitor Mode share `SystemReportView`, but not the refresh controllers.
- All three terminal modes already share `SystemMonitor` with different `ViewType` settings; there is little reason to create separate Urwid implementations for them.
- Terminal `--monitor` currently calls `root_check()`, whereas GTK Monitor is designed as a nonprivileged preview; do **not** remove the CLI root check without analyzing what the legacy mode requires.
- `--live` is **not** a read-only variant of `--monitor`.

Code entrypoints: `auto_cpufreq/bin/auto_cpufreq.py` lines ~73–170 and ~258–277; `auto_cpufreq/bin/auto_cpufreq_gtk.py`; `auto_cpufreq/modules/system_monitor.py`; `auto_cpufreq/gui/app.py`; `auto_cpufreq/gui/objects.py`.

## 3. Existing unification to preserve

Upstream PR #964 intentionally established:

1. A shared, read-only `SystemReport` in `modules/system_info.py`.
2. Shared GTK `SystemReportView` for dashboard and monitor.
3. Legacy textual `print_system_report()` and `format_system_report()` from the same snapshot.
4. Correct handling of offline CPUs, CPUFreq policy-sharing, host info under Snap, missing sensors and battery thresholds.
5. Monitor work outside the GTK and Urwid UI threads, with cleanup.
6. A distinction between actual observed values and asynchronous privileged setting requests.

These are invariants. Avoid introducing a parallel telemetry system or conflating UI suggestions with actual policy decisions.

Upstream PR: https://github.com/AdnanHodzic/auto-cpufreq/pull/964

## 4. Further opportunities, ranked

### P0 — Interactive safety and correctness

**CLI Live worker lifecycle (HIGH RISK; power writes).**

Current `auto_cpufreq.py` creates `Thread(target=live_daemon, daemon=True)`, whose loop is `while True: sleep(1); set_autofreq()`. Its `on_quit=live_daemon_off` callback restores power-profile/tuned services and cpufreqctl state but **does not signal or join the policy thread first**. A final write could race with restoration. Further, `sys.stdout = NullWriter()` is assigned inside the worker, but `sys.stdout` is process-global. The worker broadly swallows exceptions.

Recommended independent safety work:
- Explicit `Event` stop condition instead of unconditional `while True`.
- Signal worker, arrange bounded and verified exit, **then** restore services and cpufreqctl. If stopping cannot be guaranteed, do not blindly claim restored state.
- Service state snapshot and restoration of exactly what was changed; preserve existing guardrails around installed daemon and `--live` eligibility.
- Do not globally replace `sys.stdout` from a worker; use explicit, injectable output sinks or Python logging for controlled messages.
- A `try/finally` scope covering all partially initialized resources and errors during preflight, startup and run, not just normal exit.
- Prefer testing policy *calls* under mocks and an isolated VM before touching hardware. **Never reproduce this risk by running a modified --live on the user's workstation before review.**
- Because this changes a policy-writing lifecycle, use a separate PR or a separate, explicitly gated safety subproject; not an incidental part of the frontend/debug-logging PR.

**Stats error recovery.**

In `SystemMonitor._refresh_ready()`, `raise error` terminates the Urwid loop on a temporary collection failure. GTK instead reports the problem and continues. Introduce a shared `RefreshResult` carrying success/error, and a policy distinguishing recoverable collection failures from deliberate shutdown or fatal programming errors. UI-specific presentation remains in each adapter. Preserve terminal exit semantics.

**Diagnostics core race fixes.**

The experimental `gui/diagnostics.py` is still GTK-scoped. Resolve the queue-stop race when producers compete with insertion of the sentinel; discard/format exception tracebacks safely rather than retaining Python frames on queued records; test repeated enabling, blocked disk, exception callbacks, crash/exit and concurrent sessions. Do this **before** moving the logger into a common module.

### P1 — Reusable read-only functionality

**Typed report presentation / view model.**

Currently `SystemMonitor.format_system_info()`, `SystemReportView.apply_report()`, and `format_system_report()` independently format overlapping values. Extract neutral typed values plus an explicit availability/status enum or tagged optional state for Turbo, CPU frequencies, fan, thermal data, battery, EPP/EPB and platform profile. Leave alignment, colors and widgets to Urwid/GTK/text adapters.

Treat `None` / unknown / driver-managed / unavailable / disabled as semantically distinct. Record exactly which display differences are intentional (e.g. suggested governor only in monitor modes). Maintain existing stable UI headings where practical.

**Refresh coordinator, one instance per active view.**

A pure-Python `ReportRefreshCoordinator` could own request coalescing, at-most-one active collector, generation IDs, timings, result/error events, slow-operation thresholds and latest-only pending requests. Use a small, explicit state machine: IDLE -> COLLECTING -> READY/ERROR -> IDLE; CLOSED discards any late result. Do **not** attempt to forcibly cancel a blocking psutil/sysfs operation, or create a global one-per-device coordinator across separate processes.

GTK dispatch adapter: put UI mutations on the designated GLib main context. Urwid dispatch adapter: use `watch_pipe` / existing event-loop delivery. Do not directly invoke GTK/Urwid widgets on background threads. Each view may choose its own refresh period and shutdown behavior.

**Optimize UI rendering only when measured.**

GTK currently destroys/recreates core row widgets on every refresh; Urwid clears/repopulates its list walkers. Improve incremental update keyed by CPU ID for hotplug-safe GTK rows and re-use existing Urwid widgets when useful. Measure CPU/memory before optimizing. Avoid stale rows when online logical CPUs change.

**Unified diagnostics used independently by every process.**

Shared lightweight logging/session/queue/process CPU sampler with `component=gtk|stats|monitor|live`, PID, session ID, elapsed times, health indicators, and explicit opt-in. This is a reusable library, not a service. Separate files per process/session with private permissions and bounded retention. A GTK setting controls GTK; a CLI diagnostic flag controls the *individual terminal invocation*, including when executed via sudo (never silently target the regular user's home with root-owned files). `--debug` remains an existing one-shot CLI behavior, with a different purpose; do not silently change its semantics.

For high-CPU issue #860, a heartbeat is not enough. Sample **process** CPU and optionally hot thread IDs at a low diagnostic-only rate; prime psutil's first sample, which is not useful. Log sustained high CPU separately from a stalled UI event loop. Per process, the first-party logger shuts down when the UI process exits.

**Consistent mode initialization and teardown without over-generalizing.**

Factor reusable preflight checks (service conflict, environment, version, configuration path) and a declarative mode capability table where useful. Preserve mode-specific privilege expectations. For CLI `--monitor` and `--stats`, use explicit context-managed cleanup of config notifier and Urwid resources. For `--live`, leave control-service teardown outside generic read-only interface cleanup until the safety issues above are independently addressed.

**Legacy code candidates for inspection, not deletion by name.**

`gui/objects.py` still contains `SystemStatsLabel` redirecting process-global `sys.stdout`, `CPUFreqStatsLabel`, `CurrentGovernorBox`, `BatteryInfoBox`, `CPUFreqScalingBox`, and `SystemStatisticsBox`. Some may be dead since PR #964. Search all call sites, installation entrypoints and tests before removal. Never remove code solely because the new dashboard doesn't reference it.

### P2 — Control-policy *explanation* (separate safety boundary)

`SystemInfo.turbo_on_suggestion(report)` uses a simple >=20% usage threshold; actual `set_powersave()` and `set_performance()` also consider charger/battery config, Turbo overrides, per-core load, temperature, thresholds, EPP/HWP/driver constraints and time-varying inputs. Today the Monitor preview may therefore differ from what the daemon applies.

Do not call `set_autofreq()`, `mon_autofreq()`, `set_powersave()`, or `set_performance()` to construct a 'read-only' preview; those paths mix prints, system reads and/or writes.

Potential future pure, non-applying `PolicyDecision`:
- Inputs: coherent sampled metrics, relevant config snapshot and override snapshot, power-source classification, driver capabilities.
- Outputs: requested targets, skipped/not-supported decisions and **reasons**, without sysfs writes, subprocess invocation or service changes.
- Initial use: read-only shadow comparison against legacy code in controlled fixtures, **not** an automatic policy replacement.
- Important limit: a power prediction made from a past snapshot is not guaranteed identical to an active daemon decision made from later temperature/usage samples.
- Changing actual policy writers is a separate PR with explicit hardware-independent equivalence tests and a rollback plan.

## 5. Architectural contract

```text
                        READ-ONLY OBSERVATION
SystemInfo / SystemReport (existing authoritative snapshot)
           |
           +--> ReportPresentation / typed state semantics
           |
           +--> ReportRefreshCoordinator (per active UI instance)
                           |
               +-----------+-----------+
               |                       |
      GTK/GLib adapter          Urwid adapter
      Dashboard + Monitor       Stats + Monitor + Live display
               |                       |
               +----------+------------+
                          |
                Diagnostics core (library)
         per-process session, bounded logs, CPU/watchdog metrics
                          |
                          v
                  local private log files

                     CONTROL BOUNDARY
        explicit GTK controls / CLI Live / daemon
                          |
            existing privileged policy backend
                          |
              kernel/driver/service changes
```

No synchronous disk logging on UI event threads; no policy writes from presentation or snapshot collection; no accidental GTK imports into CLI runtime; no Urwid imports into GTK read-only model.

## 6. One upstream PR: allowed scope and exclusions

Working title: `Unify frontend reporting, refresh lifecycle and diagnostics`.

**Include if independently validated:** neutral report presentation, shared refresh coordination, adapter integrations for GTK dashboard + GTK Monitor + Urwid Stats/Monitor/Live *display*, shared opt-in process diagnostics, related regression tests, small performance optimizations, documentation.

**Do not include in this PR:** changing active `--daemon` policy decisions, turning `--live` into a new control engine, rewriting platform-profile backend, refactoring `set_performance`/`set_powersave`, modifying system service toggles, forcing all processes through a new persistent service, or merging experiment notebooks/scripts.

**Safety note:** Since a verified Live shutdown race was identified, a separate high-priority safety PR is appropriate. The shared read-only coordinator should not introduce more policy writer concurrency and should not be used to disguise that existing risk.

If the unified PR becomes too hard to review, split by clear boundaries: (A) diagnostics library + CLI option; (B) report presentation + refresh adapters; (C) optional incremental UI optimization. A single PR is a preference, not an absolute requirement.

## 7. Build plan and gates

| Phase | Changes | Validation gate |
| --- | --- | --- |
| 0 | Freeze current master and experimental branch; capture snapshots and source tests | Existing tests green, no installation writes |
| 1 | Correct async logging races, tracebacks, permissions and shutdown | Saturated queue, blocked writer, repeated toggles, exit/crash tests |
| 2 | Extract toolkit-neutral diagnostics and adapters | Import isolation: core must not import GTK/Urwid |
| 3 | Shared typed presentation, golden parity fixtures | Power/battery/Turbo/platform/CPU hotplug parity; unknown != off |
| 4 | Shared report coordinator without toolkit imports | One worker/view, newest-result policy, error recovery, late callbacks discarded |
| 5 | GTK adapter migration (dashboard and Monitor) | Xvfb, production-like startup, controls, status transitions |
| 6 | Urwid adapter migration (Stats, Monitor, Live *display only*) | PTY tests for Q, Ctrl+C, HUP, terminal close; no policy call changes |
| 7 | Optional Stats/Monitor/Live process diagnostic flag | Test CLI with/without sudo; explicit log location/permissions |
| 8 | Measured GTK/Urwid incremental rendering | Real CPU/RSS/FD/thread baseline comparison |
| 9 | Read-only shadow policy explanation, if not over scope | Fixed-input parity and explicit known divergences |
| 10 | Clean production branch from upstream master | Only necessary code/tests/docs, CI, manual X11; optionally Wayland |

### Automated acceptance matrix

- Modes: GTK dashboard; GTK Monitor; `--stats`; `--monitor`; Live **display logic under mocks**; legacy daemon textual reporting.
- Terminal lifecycle: Q, Ctrl+C, SIGHUP, terminal closure, with and without sudo where safe.
- Report variants: no battery, unknown AC, offline/hotplug CPUs, CPUFreq shared policies, sensors missing, driver-managed Turbo, unsupported EPP/EPB, 1/2/5 sec GTK refresh.
- Refresh: slow CPU collection, multiple update requests, exceptions, close-during-collection, restart, no orphan callbacks, no worker accumulation.
- Diagnostics: disabled => no file/thread/sampler; active => per-session private log, bounded retention, graceful shutdown and tracebacks, CPU load and main-loop health distinguished.
- Policy boundary: mocks assert **zero** governor/Turbo/EPP/sysfs/power-service mutations during Stats, GTK Monitor, CLI Monitor and diagnostic-only code. GTK explicit controls remain behind authorized actions. Live writer actions unchanged in the unified frontend PR.
- CI: Python compatibility, Linux build, Nix, GTK/Xvfb, Urwid PTY. Manual hardware tests occur *only after* isolated gates and must not replace them.
- Packaging: monitor and Stats must not implicitly depend on `gi`; GTK optional runtime must not require terminal-event-loop initialization.
- Performance: compare CPU usage, process RSS, threads and file descriptors before/after, with logging OFF and ON.

## 8. Open design questions

1. Define `RefreshResult` / coordinator event APIs that neither GUI toolkit must import.
2. Determine how current UI 'Applying…' transitions remain authoritative over older reports during command acceptance.
3. Distinguish values absent at the kernel source from values unsupported or driver-managed.
4. Determine whether enabling debug logging should survive GTK restarts by default, while CLI diagnostics remain invocation-specific.
5. Ensure root-run Stats logs never create root-owned files in the original user's directory.
6. Decide if daemon 'stats file' and terminal `--stats` naming could be clarified in docs; do not silently change existing CLI behavior.
7. Confirm candidate legacy GTK widgets have zero active imports before removal.
8. Agree on a strict stop-before-restore contract for Live in a separate safety track.

## 9. Official references

- Urwid event-loop and `watch_pipe` lifecycle: https://urwid.readthedocs.io/en/latest/reference/main_loop.html
- GNOME main-context threading: https://developer.gnome.org/documentation/tutorials/main-contexts.html
- GNOME threading safety: https://developer.gnome.org/documentation/tutorials/threading.html
- Python thread shutdown / daemon threads: https://docs.python.org/3/library/threading.html
- Python redirection of process-global stdout: https://docs.python.org/3/library/contextlib.html#contextlib.redirect_stdout
- Python logging handlers/queues: https://docs.python.org/3/library/logging.handlers.html
- psutil CPU process/sampling semantics: https://psutil.io/api/
- XDG Base Directory: https://specifications.freedesktop.org/basedir/latest/
- Original issue #860: https://github.com/AdnanHodzic/auto-cpufreq/issues/860
- Merged PR #964: https://github.com/AdnanHodzic/auto-cpufreq/pull/964

## 10. Change control

Documentation-only commit. No main/master changes, no patch to production logic, no installed daemon or GUI changes, and no upstream PR created by this file. Keep this planning note within the investigation branch and omit it from the eventual clean production history.
