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
| Read-only governor/Turbo suggestion rules and Monitor recommendation states | `auto_cpufreq/modules/report_recommendations.py` |
| Shared report semantics (missing battery/sensors, platform profile, config, offline CPUs) | `auto_cpufreq/modules/report_presentation.py` |
| Cross-interface refresh concurrency | `auto_cpufreq/modules/report_refresh.py` |
| GTK report layout for dashboard and Monitor | `auto_cpufreq/gui/report_view.py` |
| GTK Monitor screen | `auto_cpufreq/gui/monitor_view.py` |
| Main GTK window, power-apply UI state and navigation | `auto_cpufreq/gui/app.py` |
| GTK controls, menus, dialogs and daemon actions | `auto_cpufreq/gui/objects.py` |
| Desktop integration (opening directories) | `auto_cpufreq/gui/desktop.py` |
| GTK scheduling and delivery | `auto_cpufreq/gui/refresh_timer.py`, `auto_cpufreq/gui/refresh_bridge.py` |
| Optional GTK debug preference and watchdog | `auto_cpufreq/gui/diagnostics.py` |
| Shared log session lifecycle and rotation | `auto_cpufreq/modules/diagnostics.py` |
| Process/CPU/thread health measurements | `auto_cpufreq/modules/process_health.py` |
| Terminal Stats, Monitor, Live display | `auto_cpufreq/modules/system_monitor.py` |
| CLI modes, installation and policy lifecycle | `auto_cpufreq/bin/auto_cpufreq.py`, `auto_cpufreq/power_helper.py` |
| Configuration and file notifications | `auto_cpufreq/config/` |

The shared recommendation functions are the **only place to change the
governor/Turbo suggestion formulas**. `SystemInfo.governor_suggestion()`
and `SystemInfo.turbo_on_suggestion()` remain compatibility methods and
delegate to those rules. Do not make their behavior diverge from the read-only
GTK and terminal Monitor recommendations. Changes to the daemon's actual
power policy require a separate review.

New code should import `MonitorModeView` from `gui/monitor_view.py` and
`open_containing_folder` from `gui/desktop.py`. The old imports via
`gui/objects.py` remain usable for existing callers. The old unused GTK
statistics widgets were removed from `gui/objects.py`: they were not used
by any tracked application entrypoint and performed separate synchronous
collection. Avoid reintroducing a second sampling path.

## Consistent report fields across GTK and Stats

Both frontends receive the same `SystemReport` data model. Do not duplicate
presentation rules in separate toolkit modules. `report_presentation.py`
defines semantic text and availability, including absent batteries, unavailable
temperature and frequency-limit measurements, absent governor data, offline
CPUs, config-file labels and platform-profile providers. `gui/report_view.py`
and `modules/system_monitor.py` only decide **where** and **how** to show
each field. `format_system_report()` in `system_info.py` preserves the historical
text labels (including `Platform profile status`, `Platform profile provider`
and the `Not available` fallback) but obtains profile status, available choices
and provider summaries from `present_platform_profile()` rather than
reinterpreting raw provider devices. Keep those existing text labels stable
unless a deliberate compatibility change is reviewed.

A **missing battery** is different from an unknown AC source. Hardware
collection in `SystemInfo.battery_info()` returns a `BatteryInfo` even when
no battery path is found: `is_present=False` marks physical absence,
while `is_ac_plugged=True` retains the existing daemon power-source fallback.
The shared `battery_present()` function determines which battery rows GTK
and Stats display. Older `BatteryInfo` objects omit `is_present` and
remain compatible (treated as present). Do not replace a missing battery
with `None` inside hardware collection or use presence to change daemon policy.
When modifying this contract, test both actual collector branches (battery
path absent/present) using mocked sysfs reads, and check both frontends.

When changing a report field, add a synthetic scenario to
`tests/test_frontend_report_parity.py` and verify both frontends. When
changing platform-profile semantics, also check the legacy text report
against explicit expected outputs for detected, partial, read-only and
absent interfaces; do not only compare two functions calling the same helper. Keep
the tests independent of host cpufreq governors and hardware sensors.
If a value is unknown, report that explicitly rather than inventing a
default; omit optional sections only when both interfaces intentionally
follow the same policy.

## Refresh behavior

Both GTK and terminal screens use the same `ReportRefreshCoordinator`. It collects a snapshot in a background worker and keeps at most one collector active per process. Each UI has its own event loop and must render widgets on its own UI thread.

- **Periodic ticks:** call `request_if_idle()`. If collection or delivery is in progress, skip the tick without queuing another sample.
- **Explicit requests:** call `request()`. These may coalesce a single follow-up sample when busy. The GTK dashboard also uses explicit requests to confirm power-control changes.
- **Completion:** the UI acknowledges the delivered report's generation, even when presentation or diagnostics fail. Closing a view invalidates pending delivery; blocked hardware reads cannot be forcibly terminated.
- **GTK:** `GtkRefreshTimer` owns its GLib source and `GtkRefreshBridge` schedules UI-thread delivery. **Urwid:** keep using `watch_pipe` and its own alarm loop.

Telemetry and recommendations must remain **read-only**. Only dedicated power-policy/control code should issue privileged writes or modify services. Do not collapse GTK's `Applying…` transaction state into ordinary periodic refresh state: it protects the UI from stale reports.

## Before submitting a pull request

Keep the change coherent, preserve CLI and GTK entrypoints, and avoid unrelated dependency updates or formatting-only changes. Run compilation and the frontend regression suite in an isolated development checkout (never against the system-installed executable):

```bash
python -m compileall -q auto_cpufreq tests
PYTHONPATH="$PWD" python -m unittest discover -s tests -p 'test_*.py' -v
```

GTK test modules need their normal optional GTK runtime; use a graphical
session for real UI checks. The tests in `tests/` do not install the daemon
or apply power policy. Cover both paths through the shared suggestion rules
when changing recommendations (the legacy `SystemInfo` wrappers and the
Monitor evaluator), and include a no-hardware-I/O snapshot scenario.
Record manual GTK and terminal validation against the **exact branch commit**.
Do not treat test results from a separate lab branch as evidence for the
current commit.

The frontend regression suite includes pure report tests, deterministic
refresh worker-race tests, and GTK/GLib adapter tests. The GitHub Actions
`Frontend regression tests` workflow runs them with distro-provided GTK
introspection bindings. Tests must not require root, run the daemon, install
the package system-wide, or modify power-policy settings. When adding a new
feature, ensure the suite can still be discovered with the command above.

### Opt-in diagnostic logging

`gui/diagnostics.py` holds the GTK preference, but importing a GUI module
must **not** open logs or start diagnostic threads: GTK's entrypoint activates
saved logging when it attaches the GLib main loop. For the terminal, the
`--diagnostic-log` option is supported only with `--stats`, `--monitor`
and `--live`. It uses `modules/diagnostics.py`, and does not replace
the existing CLI `--debug` command. Keep logging read-only, bound its
resource usage, and test missing or unwritable directories. Do not make
diagnostic I/O prevent the UI from updating or closing.

For changes to scheduling, include evidence that refresh requests do not overlap, old GTK/Urwid callbacks are cleaned up, and the view recovers after a failed collection. For power-policy changes, explain how temporary actions and competing services are restored on error or exit.

In the PR description, include what changed, why, how you tested it, and any relevant distro/kernel/CPU details. Include logs only after checking them for private paths and other sensitive information.
