# Issue #860 — GUI and terminal runtime investigation

Status: investigation plan; no runtime changes applied  
Base: AdnanHodzic/auto-cpufreq master, 5a1683037a583785f802b5d039022127b9b154cd  
Working branch: test/issue-860-runtime-20261023  
Scope: GTK dashboard, GTK Monitor Mode, terminal stats/monitor lifecycle, configuration watching

## Objective

Determine whether the sustained single-thread CPU load, GTK freezes, and the reported terminal-hangup behavior still occur on the current master. Identify root causes separately before changing shared components. Preserve the current GUI redesign and avoid speculative dependency replacement.

This branch is for investigation notes, reproduction harnesses, synthetic tests, trace collection, and optional experimental patches. Investigation files and test-only workflows must not be merged into the release branch. Any accepted fix will be rebuilt as a minimal production patch from the current upstream master.

## Evidence and chronology

- #860: opened June 27, 2025; GTK reportedly consumes approximately one logical CPU on Cinnamon 6.0.4 / Ubuntu 24.04 / auto-cpufreq 2.6.0.
- #860: subsequent reports identify hibernate/resume as a trigger.
- #685: the GTK interface can freeze while the daemon remains functional; reports include GNOME and Cinnamon.
- #860: June 12, 2026 report on auto-cpufreq 3.0.0 says the symptom is less frequent but still present after multiple sleep/wake cycles.
- #964: merged August 26, 2026; shared SystemReport, background collection, serialized GTK dashboard refresh, and monitor cleanup substantially changed the relevant paths.
- #860: September 12, 2026 report of 100% CPU following closure of a terminal running sudo auto-cpufreq --stats. This is a distinct hypothesis, not confirmation of the GTK defect.
- #860: September 13, 2026 maintainer question asks whether the issue persists after #964.
- #156 / #160: distinguish persistent diagnostic event logs from the live stats interface. The older terminology change does not implement structured GTK diagnostic logging.

There is no confirmed post-#964 reproduction of the original GTK high-CPU scenario in the referenced issue discussion. Both upstream and fork master resolved to the same base commit during this investigation.

## Relevant code paths

- auto_cpufreq/gui/app.py: ToolWindow refresh timer, pending refresh handling, background worker, GLib idle delivery, GTK widget reconstruction, update checking and destruction.
- auto_cpufreq/gui/objects.py: GTK Monitor Mode worker lifecycle, timers, navigation cleanup, asynchronous privileged actions.
- auto_cpufreq/modules/system_monitor.py: Urwid refresh worker, watch_pipe write FD, alarm registration, event loop and finally cleanup.
- auto_cpufreq/bin/auto_cpufreq.py: entry into stats, monitor, live and daemon modes, resource ownership and exit behavior.
- auto_cpufreq/bin/auto_cpufreq_gtk.py: GTK startup, config resolution and main loop entry.
- auto_cpufreq/config/config.py and config_event_handler.py: pyinotify WatchManager, ThreadedNotifier, watched path and event dispatch.
- auto_cpufreq/modules/system_info.py: blocking telemetry collection, psutil calls, per-CPU data, and dependency on sysfs.

On master, the config notifier object is instantiated at import time, but the GTK entry point and the stats path do not explicitly call notifier.start(). Test this observation before treating pyinotify as the primary cause.

## Questions to answer

1. Can GTK CPU load be reproduced on master in a fresh session, after repeated refreshes, after an exception, or after suspend/resume?
2. If GTK freezes, is the GLib main loop blocked, starved by callbacks, or alive with a worker that has not completed?
3. Does GTK Monitor Mode create overlapping refresh workers or leave GLib sources after navigation or destruction?
4. Does terminal stats exit after Q, Ctrl+C, SIGHUP, and PTY hangup? Is behavior different when launched under sudo or through a terminal emulator?
5. If stats survives terminal closure, are SIGHUP disposition, session/process group, inherited file descriptors, or a busy terminal event loop responsible?
6. Is the pyinotify notifier actually running in any affected process? Does it have a measurable wakeup rate?
7. Is any apparent CPU increase from normal SystemReport polling or from a genuine uncontrolled busy loop?
8. Does synchronous update checking before Gtk.main() introduce a separate startup responsiveness issue? Keep it distinct from sustained CPU load.

## Phase 0 — baseline and environment capture

- Confirm upstream/fork base commit and save the exact working branch SHA in evidence.
- Capture Python, Urwid, GLib/PyGObject, psutil, desktop session, kernel, installation type and active runtime entry points.
- Identify daemon PID and GTK PID separately. Preserve the daemon's configuration and state.
- Record expected baseline process CPU, RSS, thread count and open FDs. Interpret 100% as approximately one logical CPU, not total multicore machine load.
- Review code ownership of each timer, alarm, worker, watch pipe and callback. Note which layer starts and disposes of each resource.
- Verify that ordinary CI installations cannot faithfully exercise kernel CPUFreq or suspend/resume behavior; use mocks for those paths.

Exit criterion: repeatable launch/setup steps, environment record, and process-specific telemetry. No runtime patch yet.

## Phase 1 — terminal stats and hangup

Build a disposable pseudo-terminal (PTY) test harness and run the display code against mocked telemetry, without requiring root privileges or a running auto-cpufreq daemon. Cover:

- Q and Ctrl+C normal exits.
- EOF or detached stdin where applicable.
- PTY master closure with a controlling terminal and realistic process/session ownership.
- Explicit SIGHUP and SIGTERM, distinguishing an expected signal death from an application-level clean exit.
- Both normal output and broken/unwritable stdout/stderr.
- Repeated launch/exit cycles and cleanup after errors.
- Timing of shutdown, process descendants, threads and file descriptor counts.
- CPU samples if the child survives termination or terminal closure.

Use a wall-clock test timeout and terminate the entire disposable process group if it survives. Never leave a runaway child behind. Preserve a failing trace before modifying production behavior.

On actual hardware, reproduce the sudo/terminal-emulator case only after the isolated harness is stable. Do not change the service or power settings for this step.

Success: no persistent orphaned monitor process or sustained CPU load after a normal exit or applicable terminal hangup. A signal-terminated process is acceptable when that is the documented default action; distinguish signal semantics from cleanup guarantees. A failed reproduction alone does not establish a fix.

## Phase 2 — GTK dashboard and Monitor Mode

Run GTK tests in an isolated Xvfb display with mocked SystemReport instances and no privileged writes. Simulate:

- Dashboard updates at 1, 2 and 5 seconds; repeated UI refresh and widget rebuilding.
- Delayed telemetry, raised exceptions, and recovery after failure.
- Refresh requested while a prior worker is still running; maximum concurrent worker count.
- Navigation to/from GTK Monitor Mode and destruction during an active worker.
- Pending GLib idle callbacks after the view is destroyed.
- CPU count changes and optional telemetry becoming unavailable.
- Repeated open/close cycles to detect timer, thread and FD growth.
- Controlled suspend/resume analogs using wall-clock gaps or simulated delayed workers; these are not substitutes for real suspend/resume.

Measure completed callbacks per time unit, maximum simultaneous workers, wall time spent in the GTK main loop and stable process CPU. Capture Python stacks, sampling profiles or strace traces only when a failure occurs.

Pay special attention to GTK startup update checking, GLib source IDs and paths where a callback returning true would accidentally make an idle source permanent. Do not infer a leak merely from an active, intentional recurring timeout.

Success: UI remains responsive, stale work does not update a destroyed view, no unbounded accumulation of workers/sources/FDs, and no sustained full-core load without a matching workload.

## Phase 3 — configuration watcher

- Compare importer-only, configured-but-not-started, and started/stopped ThreadedNotifier states.
- Check polling/wakeup behavior before and after config file modification, replacement and deletion.
- Inspect watch ownership and error cleanup when the watched directory does not exist or becomes inaccessible.
- Use temporary config directories; do not modify the host's /etc/auto-cpufreq.conf.
- Consider changing or replacing pyinotify only if a trace implicates it. Validate packaging implications for Python 3.9+ and Nix, Ubuntu and other supported distributions before any dependency change.

Success: no busy loop, excessive wakeups or leaked watcher threads in the relevant operating modes.

## Phase 4 — real Linux Mint Cinnamon reproduction

With the existing installed application left intact:

1. Capture a fresh idle baseline for the daemon, GTK and desktop session.
2. Open and close GTK repeatedly, observe the normal dashboard and Monitor Mode, and collect CPU/thread/FD trends.
3. Exercise normal refresh settings and switch between views.
4. Suspend and resume if safe and already supported by the system; repeat several times and examine responsiveness and resource usage. Do not enable or change hibernation just for this investigation.
5. Compare behavior with the version actually installed on the laptop. A repository SHA does not establish the installed binary version.
6. If a problem occurs, collect PID, thread-level CPU, a bounded stack/strace sample, relevant journal messages and the reproduction steps.

Do not stop, reinstall, or reconfigure the production daemon, override CPU governors, write to sysfs, or change Intel RAPL controls as part of this investigation.

## Measurements and evidence

Maintain temporary evidence outside the release patch:

- Environment, versions and base SHA.
- Exact launch mode, duration, refresh interval and shutdown mechanism.
- Per-process and per-thread CPU samples, RSS, thread count and open FD count.
- Expected vs observed behavior and repeat count.
- Process exit code or terminating signal.
- Condensed diagnostic logs, traceback and trace snippets when applicable.
- Results for baseline and patched candidate on the same environment.

Flag sustained use of roughly one logical CPU as a reproduction candidate. Avoid fixed CPU/memory pass limits that would falsely fail slower hardware; compare representative repeated runs against the baseline and inspect traces.

## Proposed experimental files

Create only when tests are implemented:

- tests/regression/issue_860/test_stats_lifecycle.py
- tests/regression/issue_860/test_gtk_refresh.py
- tests/regression/issue_860/test_config_notifier.py
- tests/regression/issue_860/pty_harness.py
- .github/workflows/issue-860-experimental.yml

These files and the present investigation document stay on the test branch. They are not part of the eventual upstream production PR. Python tests should remain nonprivileged, deterministic, isolated from physical hardware, and automatically clean up spawned processes.

## Fix-selection rules

1. Reproduce and isolate a failure before editing the implicated runtime code, when feasible.
2. Prefer fixing incorrect event-loop ownership, shutdown or scheduling over broad rewrites.
3. Make GTK/Urwid changes independently when they have distinct mechanisms.
4. Do not replace dependencies or add always-on verbose logging merely because the issue is open.
5. If logging is needed, prefer an opt-in, bounded diagnostic mode with precise event timestamps and no sensitive paths or configuration contents by default.
6. Retain the current GTK dashboard and SystemReport contract. Update GTK, terminal stats or README only when the shipped behavior changes and the change benefits users.
7. Keep release changes limited to essential source files and user-facing documentation. Reapply accepted changes from a fresh master-derived fix branch, rather than merging this investigation branch.

## Acceptance and handoff

- Baseline reproduction is documented, or the issue is explicitly classified as not reproduced with the limitations of each environment.
- Focused regression test fails against the known affected path where reproduction is possible and passes after the patch.
- Q, Ctrl+C and hangup/termination scenarios are independently verified.
- GTK dashboard and Monitor Mode lifecycle tests pass, including cleanup during an active refresh.
- Existing CLI behavior, daemon reporting and installed GTK launch remain functional.
- Linux Build and Nix Flake workflows pass.
- At least one hardware validation on Linux Mint Cinnamon is reported before claiming the original bug resolved.
- Final production PR contains no test harnesses, temporary workflows, investigation documents, generated logs or debugging-only instrumentation.
- Report upstream with precise reproduction status, base SHA, environments and the specific cases tested; do not claim #860 fixed based solely on an inability to reproduce it.

## Reference material

Issue discussions:
- https://github.com/AdnanHodzic/auto-cpufreq/issues/860
- https://github.com/AdnanHodzic/auto-cpufreq/issues/860#issuecomment-5651887854
- https://github.com/AdnanHodzic/auto-cpufreq/issues/685
- https://github.com/AdnanHodzic/auto-cpufreq/issues/677
- https://github.com/AdnanHodzic/auto-cpufreq/issues/156
- https://github.com/AdnanHodzic/auto-cpufreq/pull/160
- https://github.com/AdnanHodzic/auto-cpufreq/pull/964

API and kernel documentation:
- https://urwid.readthedocs.io/en/latest/reference/main_loop.html
- https://urwid.readthedocs.io/en/latest/manual/mainloop.html
- https://docs.gtk.org/glib/func.idle_add.html
- https://docs.gtk.org/glib/func.timeout_add_seconds.html
- https://docs.gtk.org/glib/type_func.Source.remove.html
- https://man7.org/linux/man-pages/man7/signal.7.html
