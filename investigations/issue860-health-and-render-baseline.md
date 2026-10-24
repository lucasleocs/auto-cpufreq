# Issue #860: process-health sampler and frontend render microbenchmarks

**Working experiment only — exclude this file from the production PR.**

- Branch: `test/issue-860-runtime-20261023`
- Environment: GitHub Actions Ubuntu 24.04 runners, Python 3.12; GTK 3/Xvfb and Urwid run in **different** jobs; no physical Cinnamon/Wayland hardware tested here.
- CI benchmark scripts:
  - `tests/regression/issue_860/bench_process_health.py`
  - `tests/regression/issue_860/bench_gtk_render.py`
  - `tests/regression/issue_860/bench_urwid_render.py`
- Baseline workflow: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053528099

## New diagnostic implementation

`auto_cpufreq/modules/process_health.py` reads the current process's `/proc/<pid>/stat`, `/proc/<pid>/task/<tid>/stat`, `status` and `fd` directory at **five-second intervals only when session logging is enabled**.

- First sample is a prime (never treated as a CPU percentage).
- Percent is delta CPU time / elapsed monotonic wall time, normalized such that **100% is one occupied logical CPU**, not the host-wide load. Values above 100% are possible with multiple busy threads.
- Each Linux thread's starttime is checked across samples to avoid incorrect deltas if a TID is recycled.
- Each process's starttime is checked to avoid mixing a recycled PID.
- Retains the hottest Linux TID, its approximate CPU%, RSS MiB, thread count and FD count.
- Sustained high CPU means three consecutive samples >=70% of a logical CPU (nominally 15 seconds). Emit one alert when the episode starts and an informational event when it ends.
- Emit periodic process-health summaries every 60 seconds without a log line for each normal sample.
- Stop sampling on logging disable/terminal UI teardown; do not create any sampler thread while debug logging is off.
- Reuse the shared async rotating logger, with GTK/Urwid sessions independent and the daemon power engine untouched.

This is a low-cost **read-only** diagnostic, not a CPU throttler and not a continuous tracing daemon. It can miss brief spikes, native deadlocks that starve Python scheduling, and abrupt terminal hangups without orderly flush. It does not claim to fix the underlying upstream bug.

## Render baseline, synthetic CPU data (completed)

Numbers below are **one execution on CI**; they are not stable, independently replicated benchmarks. Unit: milliseconds per formatting call; 60 timed iterations after five warmups, one UI component isolated.

| Logical cores in synthetic report | GTK row rebuild median | GTK p95 | GTK child widgets | Urwid listwalker median | Urwid p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 4 | 0.2764 | 0.3180 | 20 | 0.2418 | 0.2589 |
| 8 | 0.4999 | 0.5427 | 36 | 0.5021 | 0.5445 |
| 16 | 0.9329 | 0.9576 | 68 | 0.3439 | 0.6297 |
| 32 | 1.8257 | 1.8974 | 132 | 0.4894 | 0.5170 |

Measurements cover:
- GTK: `SystemReportView._refresh_core_rows()` including removing/recreating CPU rows. Excludes GTK drawing/layout/compositor, daemon state and sensor reads.
- Urwid: `SystemMonitor.format_system_info()` including clearing/repopulating walkers. Excludes terminal screen redraw, PTY I/O and sensor reads.
- **Do not compare GTK-vs-Urwid as if they were on the same hardware/process.** The two jobs use independently scheduled runners with different Python executables. The Urwid measurements are not strictly monotonic by logical core count, suggesting normal runner variation.

**Interpretation:** On the isolated CI benchmark, GTK row rebuild for an eight-thread CPU takes approximately 0.5 ms per refresh. At a two-second interval that is a small fraction of one logical CPU. This does **not** rule out expensive GTK redraw, layout, other application callbacks, accidental rapid timers, thread leaks or an intermittent CPU spin. Do not optimize UI by speculation. Re-measure full runtime and rendering on real hardware before deciding whether incremental row caching is worthwhile.

## Process sampler overhead baseline

The `bench_process_health.py` run completed on the Ubuntu 24.04 Python 3.12.15 CI runner:

| Metric | Result |
| --- | ---: |
| Number of consecutive `/proc` snapshots | 100 |
| Median per snapshot | 0.2043 ms |
| P95 per snapshot | 0.2449 ms |
| Maximum observed | 0.3116 ms |
| Synthetic CPU duty at 5-second cadence using median | ~0.00409% of one logical CPU |
| Process at measurement | 1 thread, 6 open file descriptors |

**Interpret carefully:** the duty estimate is only the synchronous `read_snapshot()` cost divided by the five-second interval. It excludes Python thread wakeup, scheduling, log events, error formatting, file writes and the load of other applications. This is encouraging but not an end-to-end measurement of the real UI with debug enabled.

Run: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053610334

## Independent second render run (runner variation)

Second isolated execution, same synthetic inputs and sample counts but different CI runners/scheduling:

| Logical cores | GTK median / p95 (ms) | Urwid median / p95 (ms) |
| ---: | ---: | ---: |
| 4 | 0.2214 / 0.2414 | 0.2727 / 0.3242 |
| 8 | 0.3969 / 0.4081 | 0.3204 / 0.7455 |
| 16 | 0.7559 / 0.8007 | 0.3748 / 0.4015 |
| 32 | 1.4731 / 1.5217 | 0.5403 / 0.5899 |

The variability is material, but both runs imply that isolated CPU row/widget rebuilding is a small part of a normal two-second frontend refresh. It is therefore premature to add incremental widget caching just to address issue #860. Focus next on full-loop latency, sampler logs and end-to-end CPU use on a real Cinnamon machine.

## Reference and validation

- Kernel procfs counters and starttime: https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html
- GTK Inspector and rendering profiling: https://developer.gnome.org/documentation/tools/inspector.html
- GTK Sysprof support: https://chromium.googlesource.com/external/github.com/GNOME/gtk/+/HEAD/docs/reference/gtk/running.md
- Synthetic render workflow: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053528099

## Follow-up gates (not yet met)

1. Verify terminal high-CPU trigger log with an isolated synthetic busy-thread process (avoid busy-looping on the user's installed service).
2. Test logger disabled vs enabled over several minutes with Linux Mint/X11, including actual event-loop latency and memory consumption; sample GTK/Urwid processes separately.
3. Repeat suspend/resume and normal/abrupt PTY close; inspect whether previously flushed records are useful.
4. Measure full GTK layout/draw using Sysprof, if the synthetic row-rebuild cost fails to explain a real regression.
5. Run multiple benchmark replicates per machine and record CPU model, kernel, compositor, refresh interval and core count.
6. Consider incremental widget reuse **only if measured end-to-end benefit outweighs added hotplug/scroll-focus complexity**.
