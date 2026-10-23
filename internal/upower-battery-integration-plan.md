# Battery reporting integration plan — UPower-first, read-only

Status: implementation plan, not production documentation
Baseline: `AdnanHodzic/auto-cpufreq` master at `5a1683037a583785f802b5d039022127b9b154cd` (2026-10-23)
Implementation target: separate feature branch derived from a verified upstream baseline
Scope: battery telemetry, capability reporting, GTK, terminal stats
Excluded from this phase: writing charge thresholds, editing UPower hwdb, privileged helpers, vendor-specific code, changes to CPU power policy

## Objective

Use UPower over the system D-Bus as the preferred source for **battery presentation**, retaining the current Linux power_supply sysfs implementation as a complete fallback. Preserve the familiar GTK field names. Where UPower exposes charge threshold capabilities, show additional read-only details in the GTK and terminal stats. Do not suggest arbitrary supported percentages.

## Confirmed facts and constraints

- UPower devices are enumerated by `org.freedesktop.UPower.EnumerateDevices()`; device properties are read through `org.freedesktop.DBus.Properties` for `org.freedesktop.UPower.Device`. The battery object can be correlated with the existing selected sysfs battery by `NativePath`. The aggregate `DisplayDevice` is not an acceptable replacement for a selected physical battery in multi-battery systems.
- D-Bus `State` is an integer enumeration: 0 Unknown, 1 Charging, 2 Discharging, 3 Empty, 4 Fully charged, 5 Pending charge, 6 Pending discharge. Do not derive direction from AC connectivity for labels.
- `EnergyRate` is expressed in watts, `Percentage` in percent, and `UpdateTime` as a Unix timestamp. A zero reading is valid, not missing. Validate types, finite values, bounds, and an individually identified physical battery.
- UPower charge threshold properties are optional on older daemon versions. `ChargeThresholdSupported` is not a list of percentage presets. `ChargeThresholdSettingsSupported` is a bitmask: 1 start, 2 end, 4 firmware-managed charging. A missing mask is not equivalent to zero.
- `ChargeStartThreshold` and `ChargeEndThreshold` indicate configured levels, not all percentages the firmware allows. `G_MAXUINT` (4294967295) must not be displayed as a percent. The UPower `EnableChargeThreshold(bool)` method changes charging behavior and is explicitly excluded.
- Linux `charge_control_start_threshold` and `charge_control_end_threshold` are standard sysfs attributes. Some drivers round written values; no universal enumeration of accepted percentages exists. This phase is read-only.
- The existing battery threshold implementation in `auto_cpufreq/battery_scripts/` includes vendor-specific drivers and a periodic writer (`BATTERY_APPLY_INTERVAL = 3600`) activated through existing configuration. Preserve it unchanged. No new code may contend with it or reinterpret the meaning of `enable_thresholds`.
- On the Samsung BAT1 reference system, UPower daemon 1.90.3 reports `fully-charged` at 99% and no UPower threshold API; sysfs provides `charge_control_end_threshold=100`. `ADP1` and USB-C power supply states must continue to be handled by existing power-source logic.

## Data ownership and backend choice

There are **two independent concerns**:

1. CPU power-source/control policy: leave `SystemInfo.external_power_state()` and callers such as `core.py` unchanged. The UPower device state is not a substitute for knowing whether external power is connected.
2. Battery presentation: use one snapshot chosen from UPower or sysfs for status, percentage, and battery power together. Prefer UPower when a correctly matched, valid physical-battery snapshot is available; otherwise use the current sysfs path. Preserve the raw sysfs status as optional diagnostic information if needed, but never silently mislabel AC-connected discharge.

For threshold presentation, treat existing sysfs readings and optional UPower capability properties as *separate*, source-attributed facts. UPower's reported configured thresholds may represent its own saved settings rather than the active value applied by another manager. When the current UPower and sysfs thresholds disagree, do not label them as verified hardware-effective values. Do not change the battery policy.

### Proposed internal interfaces (design, not final API)

- `SysfsBatteryProvider.read(selected_battery)` returns the existing battery snapshot and standard threshold values.
- `UPowerBatteryProvider.read(selected_battery)` returns an optional battery snapshot and optional UPower charge control metadata; failures become a typed unavailable result.
- `BatteryReport` or an extension of `BatteryInfo` carries presentation status, percentage, power watts, threshold metadata and source; retain backward-compatible fields consumed by existing views.
- A shared status/label formatter is used by `gui/app.py`, `gui/objects.py`, and `modules/system_monitor.py`.
- No GTK import in `modules/system_info.py`, and no raw `upower -i` output parsing. A D-Bus transport adapter must work for the terminal without requiring a GTK session.

### D-Bus transport choice

Before implementing, run a small **read-only dependency/packaging spike**. Compare a pure-Python D-Bus client (for example `dbus-next`) against a GLib/Gio adapter. The core requirement is operation under headless CLI and GUI with bounded latency, without making the UPower daemon mandatory. Validate Poetry lock, source installer, Nix and Snap impact before selecting a new required dependency. Do not put `gdbus`, `busctl`, or `upower -i` text parsing into the permanent data model as an unreviewed shortcut.

### Selection and fallback rules

1. Determine the physical battery using existing `battery_device`, `get_battery_path()`, and exclusion rules; do not select UPS or peripheral batteries accidentally.
2. Enumerate UPower devices on the system bus and select only a battery with matching physical `NativePath` / resolved sysfs identity; do not assume `battery_BAT1` is a universal object path.
3. Read UPower properties in a bounded operation. Handle daemon missing, access denied, service restart, missing properties, stale/inconsistent values and device disappearing.
4. Require a coherent state/percentage/rate snapshot before replacing the presentation's sysfs source. If UPower is clearly unusable, fallback to the existing sysfs snapshot. Do not merge independently timed state/power measurements into a misleading combined reading. Evaluate an appropriate `UpdateTime` age policy empirically; fully charged batteries may update infrequently, so a short fixed expiry should not be imposed without evidence.
5. Zero watts is valid. Preserve unavailable versus unknown versus zero. Validate numeric units, infinity/NaN and percentages outside 0–100.
6. Normalize UPower states and kernel fallback statuses through one formatter; retain `Fully charged`, `Pending charge` and `Pending discharge` when available. If the kernel reports `Discharging` and AC is online, show its literal state when fallback is active; do not invent a power threshold to override it.
7. Battery presentation must never change CPU governor, policy selection, AC detection, daemon decisions, or the existing charge threshold writer.

## Read-only UI behavior

Keep the existing GTK fields and names: `Status`, `Charge`, `AC power`, `Start threshold`, `Stop threshold`, and battery power label. Retain terminal `--stats` names unless a precise correction is necessary.

- With UPower 1.90.3: UPower-derived status/percentage/rate may improve values, but no threshold capability block appears. The layout stays the same, even if `Status` changes from `Not charging` to `Fully charged`.
- With newer UPower and `ChargeThresholdSupported = true`: conditionally add concise read-only information, for example `Charge protection: Enabled`, `Supported controls: End threshold`, and the configured levels when meaningful. Make the same information available in `--stats`.
- With `ChargeThresholdSupported = false`, missing property, or no matching device: show the legacy fields without an additional block. Existing numeric sysfs thresholds remain visible when present; never imply a missing UPower API means no sysfs threshold.
- Values are **configured limits**, not a list of permissible values. No slider, toggle, edit field or control button is part of this phase.
- Avoid duplicate threshold rows and avoid changing current labels in anticipation of later controls.

## Code integration inventory

- `auto_cpufreq/modules/system_info.py`: `BatteryInfo`, `battery_info()`, `battery_power_label()`, `get_battery_path()`, `external_power_state()`.
- `auto_cpufreq/modules/system_monitor.py`: battery section in `--stats`.
- `auto_cpufreq/gui/app.py`: `SystemReportView`, `_battery_status()`, live battery labels.
- `auto_cpufreq/gui/objects.py`: legacy `BatteryInfoBox.refresh()`; keep status semantics consistent.
- `auto_cpufreq/battery_scripts/{battery.py,shared.py}`: existing charge threshold writer; review for conflicts, do not modify in the telemetry PR.
- `README.md`: make a short public note only after code and tests pass, explaining optional UPower and preserved sysfs fallback. Do not include this internal plan or test harness in the final PR.

## Phased delivery

1. **Transport spike** (temporary only): identify D-Bus client, test physical-battery mapping and bounded failure paths, compare packaging across installers.
2. **Shared data model**: UPower-first battery snapshot, robust sysfs fallback, shared formatters, no power-policy changes.
3. **Frontends**: update GTK, legacy GTK battery box, and `--stats`, with optional read-only UPower threshold metadata.
4. **Verification and audit**: tests in temporary branches; Linux/Nix CI; headless CLI; GTK on real hardware; assert the final contribution branch contains no tests, plan documents, workflows or experimental scripts unless explicitly approved as production tests.
5. **Separate later proposal**, not in this PR: evaluate controlling preconfigured UPower thresholds. Any write-path would require a dedicated design, privilege review and conflict analysis; no direct editing in GTK is currently planned.

## Acceptance criteria

- UPower unavailable, old, incompatible, or failing: original sysfs reporting remains functional; program startup and periodic updates continue.
- Compatible UPower: coherent state, percentage and power in GTK and `--stats`, including fully charged at 99% and zero watts.
- A physical battery is matched correctly; peripheral, UPS and aggregate display device are not chosen silently.
- Capability presentation is absent when unknown, and truthful when available. A supported-control bitmask is never described as selectable percentages.
- No battery threshold writes, config edits, privileged calls, new system services, or battery-policy conflicts.
- CPU policy and AC detection regressions: zero behavioral change.
- Temporary plan/test files are absent from the final contribution diff.

## Official source material

- UPower service and EnumerateDevices: https://upower.freedesktop.org/docs/UPower.html
- UPower device D-Bus API: https://upower.freedesktop.org/docs/Device.html
- UPower state enumeration: https://upower.freedesktop.org/docs/UPower-up-types.html
- Linux power supply class: https://docs.kernel.org/power/power_supply_class.html
- Linux sysfs charge threshold ABI: https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-power
- dbus-next transport reference (candidate only): https://python-dbus-next.readthedocs.io/en/latest/index.html
