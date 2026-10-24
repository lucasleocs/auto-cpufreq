# Experimental GUI debug logging — issue #860

Status: prototype for review; do not merge the investigation branch into master  
Branch: test/issue-860-runtime-20261023  
Scope: GTK UI only, not the daemon or CLI stats.

## Rationale

The GUI high-CPU/freeze issue (#860) and the related freeze issue (#685) were not reproduced during the available Linux Mint Cinnamon X11 baseline, suspend/resume test, PTY tests, or sudo terminal-close tests. Commenters and the maintainer independently requested GUI logging because the symptom is intermittent and previously lacked diagnostics.

The existing auto-cpufreq --debug command prints a one-time CLI snapshot. It does not capture the GTK main-loop lifecycle, stuck callbacks, active collection threads or exception history. No equivalent "Enable debug logging" GUI option was present in current master before this experimental change.

## User-facing functionality

- **Enable debug logging** is a check item in the GTK dashboard menu, alongside refresh settings and About.
- When the daemon is not running, the corresponding checkbox is displayed in the idle page before entering GTK Monitor Mode.
- **Debug log location** in the dashboard menu shows the log path and reminds users to review its contents before posting.
- Logging is off by default, can be enabled or disabled without restarting, and the setting persists between GUI launches.
- Logs reside in $XDG_STATE_HOME/auto-cpufreq/gtk.log or ~/.local/state/auto-cpufreq/gtk.log.
- The setting resides in $XDG_CONFIG_HOME/auto-cpufreq/gtk-debug.json or ~/.config/auto-cpufreq/gtk-debug.json.
- Logs are created with mode 0600, and the state/config directories are created with mode 0700 where absent.
- A 1 MiB rotating log retains three backups, limiting the retained set to approximately 4 MiB.

No daemon configuration, sysfs setting, RAPL control, power governor or system service is modified.

## Runtime events

With logging enabled, instrumentation records:

- Startup and shutdown, PID, XDG session type, and actual GDK display backend.
- GTK dashboard refresh interval changes and requests arriving during active telemetry collection.
- Duration and exceptions of dashboard telemetry collection and UI rendering.
- Duration and exceptions of GTK Monitor Mode collection and rendering.
- GUI/Monitor Mode lifecycle and cleanup.
- A recurring GTK main-loop heartbeat, normally every five seconds.
- Main-loop wakeups delayed by more than twice the heartbeat interval.
- An optional lightweight watchdog, checking from a separate daemon thread; when the main loop remains unresponsive for at least twenty seconds, it captures a bounded Python main-thread stack trace once until it recovers.
- Linux CLOCK_BOOTTIME versus CLOCK_MONOTONIC deltas, to distinguish suspend/resume time from real event-loop stalls when both clocks are supported.

Log creation, the heartbeat and the watchdog are disabled when the user has not opted in. Normal CLI stats, daemon reporting and CPU settings are untouched.

## Limits and security

- A watchdog cannot guarantee collecting a stack if the Python interpreter itself is fully deadlocked or unable to schedule its threads. A full kernel/GTK native stack still requires external tracing.
- Event logging does not guarantee the root cause can be identified from one session or that no hangs remain.
- File writes may fail because of filesystem permissions or storage exhaustion. The GUI can show an error when changing the setting, and the attempted toggle is reverted on a preference-save failure.
- Python exception text and stack traces may reveal local source file paths or application environment details. Users must inspect logs before uploading.
- No full SystemReport, per-core telemetry dumps or configuration file contents are intentionally written to debug logs.
- RotatingFileHandler writes synchronously; sustained disk stalls could still delay callers while logging is enabled. This is a prototype tradeoff and should be evaluated for production. If needed, a bounded queued logging handler may be preferable before final release.
- The logging implementation is Python-only and requires no extra third-party dependencies.

## Test coverage in experimental CI

Tests verify:
- No log files created when disabled by default.
- Enable/disable without restart, persisted settings, private file permissions, and bounded file rotation.
- Error rollback for failure to persist the enabled preference.
- Synthetic heartbeat delays, background watchdog capture of a blocked main thread, and distinguishing suspend gaps from event-loop stalls.
- Real GTK 3 menu and checkbox toggle signals in Xvfb when the daemon is running or absent.
- Existing GTK refresh lifecycle and PTY/Urwid regression tests remain green.

Relevant CI workflow: https://github.com/lucasleocs/auto-cpufreq/actions/workflows/issue-860-experimental.yml

## Required before a production PR

1. Verify launch and UI layout with the actual installed GTK application on a Linux Mint Cinnamon desktop, without changing the production installation.
2. Enable logging in a disposable/test launch; check the log path and timestamped startup, refresh and normal exit entries.
3. Verify no unexpected CPU, memory, FD or thread increase when logging is disabled, and acceptable overhead when enabled.
4. Confirm on X11 and, if available, native Wayland, distinguishing actual GDK backend from XDG_SESSION_TYPE.
5. Test long idle runs, multiple suspend/resume cycles, failures to create the state directory and interrupted UI shutdown.
6. If accepted, start a clean branch from the current upstream master and transplant only essential GUI files and README changes. Do not include investigation markdown, extra PTY harnesses, diagnostic experiments or experimental workflow in the production PR.

## Hardening pass — October 23, 2026

The initial synchronous rotating handler was replaced with a bounded asynchronous writer. The GUI now submits log records through a customized QueueHandler with a maximum of 256 pending records; the writer formats exceptions and writes/rotates files outside the GTK main loop. Queue overflow never waits for the disk and records a count of dropped events. A standard QueueListener is not used because its blocking stop could hang the UI if the disk is unresponsive; the writer instead processes an explicit sentinel and shutdown waits at most 300 ms.

Each activation uses a unique session log named gtk-YYYYMMDD-HHMMSS-PID-RANDOM.log. This avoids rotation collisions between processes and between repeated activations. The current log and other active writers are protected from cleanup. A new session retains four older completed sessions after pruning, and each individual session rotates at 1 MiB with three backups. Currently active concurrent sessions are not deleted, so total disk usage can exceed the inactive-session bound.

The logger validates file opening before enabling. Subsequent write failures are deduplicated and delivered to a nonmodal GTK warning dialog through GLib.idle_add, rather than attempting to log recursively. The option remains persistent and the GTK display is not blocked by log output under normal operation.

Routine refresh durations are collected into one-minute count/mean/maximum summaries; abnormally slow collections and renders are logged immediately. Five-second heartbeat callbacks no longer produce a line when GTK is healthy. After a 20-second apparent stall, the watchdog records the main Python thread stack and the accumulated refresh summary. Suspend/resume is distinguished using CLOCK_BOOTTIME and CLOCK_MONOTONIC.

Experimental checks include queue saturation under an artificially blocked writer, simulated write and open failures, simultaneous instances, repeated toggles, log permissions, per-session rotation and pruning, and retained GTK/Urwid regression coverage.

Verified: 24 Python/terminal tests and 5 GTK/Xvfb tests passed in experimental CI run https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050619733 . A successful CI run is not a substitute for testing the complete installed GUI on Linux Mint Cinnamon. The actual installed application and user daemon have not been modified.

Remaining limitations:
- A system crash or SIGKILL may lose pending asynchronous records.
- A stuck filesystem can hold a daemon writer thread after the bounded GUI shutdown wait. A subsequent activation uses a different log path.
- The watchdog is a Python-level diagnostic, not a native GTK/kernel stack profiler.
- Errors from session-file cleanup are treated as nonfatal; a read-only or damaged filesystem may preserve older logs.
- Real hardware/X11 and Wayland sessions require a separate end-to-end launch and overhead test.

## Sources

- https://github.com/AdnanHodzic/auto-cpufreq/issues/860
- https://github.com/AdnanHodzic/auto-cpufreq/issues/685
- https://specifications.freedesktop.org/basedir/0.8/
- https://docs.python.org/3/library/logging.handlers.html
- https://docs.gtk.org/gtk3/signal.ToggleButton.toggled.html
- https://man7.org/linux/man-pages/man2/clock_gettime.2.html
