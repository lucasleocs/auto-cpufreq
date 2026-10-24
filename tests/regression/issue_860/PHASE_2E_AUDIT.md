# Phase 2E: GTK update consolidation audit (2026-10-24)

## Architecture after phases 2A–2E

- The GTK dashboard and GTK Monitor both construct `GtkRefreshBridge` for
  GLib main-loop delivery, error handling, measurements and coordinator
  acknowledgement.
- They each construct one `GtkRefreshTimer`, whose periodic tick requests
  telemetry **only when the coordinator is idle**. Busy periodic ticks merely
  report a stall, with one warning per episode.
- The read-only `ReportRefreshCoordinator.in_progress` is the sole source
  for the dashboard's `refresh_in_progress` property; there is no separately
  maintained boolean or post-ack synchronization callback.
- `refresh_pending` is deliberately **not** eliminated. Explicit refresh
  and power-operation follow-up can request a new snapshot during delivery,
  independently of periodic tick skipping. `pending_power_release` protects
  `Applying…` against older reports.
- The GTK Monitor no longer owns a separate health timer or a timer scheduled
  from inside the rendering/error paths. Shutdown and closing-window guards
  remain idempotent.
- The periodic tick compares `CLOCK_BOOTTIME` and `CLOCK_MONOTONIC` where
  available, but does not trigger catch-up reads after resume. The optional
  diagnostic heartbeat remains separate to detect main-loop stalls.

## Comparable CI rendering baselines (synthetic; milliseconds per rebuild)

These benchmarks operate on the **same** row-rebuild or list-walker methods,
not on the entire UI and not on real sensor reads. Results were collected on
different GitHub Actions runs. This comparison is *not* a CPU/power
improvement claim.

| Isolated benchmark | 2C before timer change (run 40088946918) | 2D after timer change (run 40090569860) |
| --- | ---: | ---: |
| GTK 4 logical cores, median | 0.2755 ms | 0.2771 ms |
| GTK 8 logical cores, median | 0.4990 ms | 0.5027 ms |
| GTK 16 logical cores, median | 0.9172 ms | 0.9367 ms |
| GTK 32 logical cores, median | 1.8127 ms | 1.8104 ms |
| Urwid 4 logical cores, median | 0.2879 ms | 0.2806 ms |
| Urwid 8 logical cores, median | 0.3238 ms | 0.3173 ms |
| Urwid 16 logical cores, median | 0.3939 ms | 0.3945 ms |
| Urwid 32 logical cores, median | 0.5705 ms | 0.5535 ms |

Observed differences are small and subject to runner scheduling noise.
The new scheduler is exercised in synthetic tests for single-source ownership,
repeated interval changes, busy-tick skipping, error recovery, shutdown and
suspend/resume clock gaps. Those tests validate **behavior**, not energy savings.

## Retained limitations / next real-world validation

- GTK full-process CPU consumption during hours of real sensor refresh was
  **not** measured by CI. Neither was suspend/resume behavior on Cinnamon.
- A collector blocked inside a kernel/psutil call remains blocked until that
  call returns; the coordinator intentionally does not start a replacement
  worker and cannot forcibly cancel such calls.
- The Linux `CLOCK_BOOTTIME` comparison reports resume gaps when the next
  GTK tick executes; it does not wake the machine.
- Resource-count and power-operation safety should be checked on a disposable
  installed build, not by altering the user's active power configuration:
  record CPU consumption, FDs, thread count and update cadence before and
  after normal use and several suspend/resume cycles.
- GLib/GObject and toolkit thread affinity continue to be enforced by
  `GLib.idle_add` and GTK's main loop; terminal modes retain their own
  event loop.

**Scope:** experimental branch only; no master/upstream merges or local
privileged power configuration changes.
