# Contributing to auto-cpufreq

Thanks for helping improve auto-cpufreq. Please open a GitHub issue or discussion for substantial behavioral changes, and keep pull requests focused on one purpose.

## Development

See [Development installation](README.md#installation-development-mode-only) for the supported installation methods. With Poetry:

```bash
poetry install
poetry run auto-cpufreq --help
```

For GTK development, install the optional GUI dependencies and start the application in a graphical session:

```bash
poetry install --extras gui
poetry run auto-cpufreq-gtk
```

Prefer a development environment over replacing a running system installation. Do not test privileged power-policy changes on a production machine without first reviewing their effects.

## Where to make changes

| Responsibility | Start here |
| --- | --- |
| Read-only hardware telemetry and `SystemReport` | `auto_cpufreq/modules/system_info.py` |
| Interpretation of available/unavailable values | `auto_cpufreq/modules/report_presentation.py` |
| Read-only Monitor recommendations | `auto_cpufreq/modules/report_recommendations.py` |
| Cross-interface refresh concurrency | `auto_cpufreq/modules/report_refresh.py` |
| GTK report layout | `auto_cpufreq/gui/report_view.py` |
| GTK Monitor screen | `auto_cpufreq/gui/monitor_view.py` |
| Main GTK window and navigation | `auto_cpufreq/gui/app.py` |
| GTK scheduling and delivery | `auto_cpufreq/gui/refresh_timer.py`, `refresh_bridge.py` |
| Terminal Stats, Monitor, Live display | `auto_cpufreq/modules/system_monitor.py` |
| Live-mode service lifecycle | `auto_cpufreq/bin/auto_cpufreq.py`, `auto_cpufreq/power_helper.py` |
| Configuration and file notifications | `auto_cpufreq/config/` |

Some older GTK widget imports remain available from `gui/objects.py` for compatibility. For new work, prefer the component's responsible module.

## Refresh behavior

Both GTK and terminal screens use the same `ReportRefreshCoordinator`. It collects a snapshot in a background worker and keeps at most one collector active per process. Each UI has its own event loop and must render widgets on its own UI thread.

- **Periodic ticks:** call `request_if_idle()`. If collection or delivery is in progress, skip the tick without queuing another sample.
- **Explicit requests:** call `request()`. These may coalesce a single follow-up sample when busy. The GTK dashboard also uses explicit requests to confirm power-control changes.
- **Completion:** the UI acknowledges the delivered report's generation, even when presentation or diagnostics fail. Closing a view invalidates pending delivery; blocked hardware reads cannot be forcibly terminated.
- **GTK:** `GtkRefreshTimer` owns its GLib source and `GtkRefreshBridge` schedules UI-thread delivery. **Urwid:** keep using `watch_pipe` and its own alarm loop.

Telemetry and recommendations must remain **read-only**. Only dedicated power-policy/control code should issue privileged writes or modify services. Do not collapse GTK's `Applying…` transaction state into ordinary periodic refresh state: it protects the UI from stale reports.

## Before submitting a pull request

Keep the diff small, preserve CLI and GTK entrypoints, and avoid unrelated dependency updates or formatting-only changes. Run compilation and the available targeted tests in your development checkout:

```bash
python -m compileall -q auto_cpufreq
```

For changes to scheduling, include evidence that refresh requests do not overlap, old GTK/Urwid callbacks are cleaned up, and the view recovers after a failed collection. For power-policy changes, explain how temporary actions and competing services are restored on error or exit.

In the PR description, include what changed, why, how you tested it, and any relevant distro/kernel/CPU details. Include logs only after checking them for private paths and other sensitive information.
