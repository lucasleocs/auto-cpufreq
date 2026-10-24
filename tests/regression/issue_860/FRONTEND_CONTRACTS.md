# Issue 860 — frontend refactoring contracts

This document records the observable contracts before GTK/Urwid adapters are
restructured. The tests and CI enforce these, rather than forcing a common
event loop or changing privileged power policy.

| Mode | Collector | UI delivery | Configuration observer | Applies power policy |
| --- | --- | --- | --- | --- |
| `--stats` | `SystemInfo.generate_system_report` | Urwid `watch_pipe` + alarm | No | No |
| `--monitor` | Same, with suggestions | Urwid `watch_pipe` + alarm | Yes | No |
| `--live` | Same | Urwid `watch_pipe` + alarm | Yes | Yes, through `LiveModeSession` |
| `--daemon` | Shared report for logs | CLI loop | Yes | Yes |
| GTK dashboard | Shared report | `GLib.idle_add` + periodic timer | No | Only explicit privileged user actions |
| GTK Monitor | Shared report, with suggestions | `GLib.idle_add` + shared periodic timer | No | No |
| `--debug` | Snapshot and additional diagnostics | Synchronous text | No | No policy writes |

## Cross-mode invariants

- Only one report collector can run at a time per process; pending refreshes
  are coalesced, and results from closed views are discarded.
- Toolkit-specific scheduling and widget mutation stay on their UI thread.
  GTK uses GLib; Urwid uses its own event loop and wakeup pipe.
- Both GTK views own one periodic `GtkRefreshTimer` per active view. A busy
  periodic tick checks for stalls but never queues an additional collection;
  explicit user/configuration requests may coalesce one successor.
- Suspend/resume detection belongs to the optional GUI diagnostic heartbeat,
  which compares Linux CLOCK_BOOTTIME and CLOCK_MONOTONIC. A periodic refresh
  timer does not duplicate those readings or catch up missed ticks.
- The main GTK view can hold a field in **Applying…** until a fresh
  report confirms the observed state. Refactoring refresh must not override
  this transaction state with an older snapshot.
- Battery/CPU readings and recommendations are observations, **not**
  instructions to write sysfs or alter CPU governors.
- Unknown source or unreadable controls must not be represented as
  recommendations to turn a feature on or off.
- `--debug` and `--stats` do not install helpers or start an inotify
  watcher. CLI Monitor and GTK Monitor are both read-only for power policy.
- Error/timeout diagnostics must not spawn concurrent collectors. A
  permanently blocked kernel read is recorded, not forcibly cancelled.

## Validation before GTK lifecycle refactoring

- Run both GitHub Actions jobs (`stats-lifecycle` and `gtk-refresh`),
  Linux Build and Nix Flake on the same commit.
- Test unavailable/unknown Turbo, no CPU sensors, AC/battery/unknown source,
  refresh coalescing, closing/reopening GTK Monitor, worker stop failures,
  and read-only CLI wiring.
- Preserve the existing synthetic benchmarks as comparative baselines;
  do not claim the 100% Cinnamon CPU issue is fixed without physical
  suspend/resume and long-running GTK testing.
