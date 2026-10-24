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

## Read-only UI behavior — approved design direction, pending code (2026-10-23)

The GTK and terminal must present **what the battery is doing**, not how to edit charging settings. This compact layout supersedes earlier mockups showing three separate `Charge protection`, `Supported controls`, and `UPower configured limits` rows. The older mock lab already passed, but **that success does not validate this revised UI**.

### Stable semantics

- `Charge` = current battery percentage (e.g. `55%`, `80%`, `100%`). `Stop threshold` = charge-stop target, not current charge. They remain separate.
- `Status` = actual UPower battery state, with sysfs fallback. NEVER calculate a state from the percentage or the configured threshold. Reaching 80% might yield `Fully charged`, `Pending charge`, or another reported state; do not hardcode.
- `Battery power` measures energy flow **through the battery**, not the laptop's total power consumption. A valid `0.00 W` while AC-connected must remain zero. This addresses the misleading `Power draw` presentation discussed in upstream issue #974 and PR #964; CPU/package power is separate.
- The standard/legacy layout keeps `Status`, `Charge`, `AC power`, `Start threshold`, `Stop threshold`, `Battery power`. With old UPower (e.g. 1.90.3), leave layout unchanged. Normalize missing threshold values to `Unavailable` instead of the literal `None`.
- Do not append `(kernel)` to a normal `Stop threshold: 100%` (or any ordinary sysfs value) without a conflict; the provider source is not useful there.
- For a **valid enabled numeric** UPower stop/start threshold, present its numeric level inline, e.g. `Stop threshold: 80% (UPower)`, `Start threshold: 75% (UPower)`, **provided the other observed numeric source does not contradict it**. The suffix names the data provider, not an editable option. Do not add a redundant `Charge protection` or `Supported controls` row.
- When the UPower **configured** threshold disagrees with the kernel **observed** threshold, preserve both explicitly on the threshold row (wrapping if necessary): `Stop threshold: 100% (kernel) · 80% configured (UPower)`. This is an exceptional diagnostic, not a statement of verified effective charge stopping, and avoids silently masking a possible mismatch.
- When the UPower support API is present but protection is **disabled**, do not display a saved 80% preset as an active 80% stop. Show the observed kernel threshold normally (e.g. `Stop threshold: 100%`) plus one additional read-only explanatory line `Charge limit: Supported (disabled)`. If a dormant configured preset matters, allow `80% saved (inactive)` as *optional explanatory text*, not as the effective stop.
- When supported control is **firmware-only** (settings mask 4, no valid numeric start/end limits), use one descriptive line: `Charge limit: Firmware managed (active)` or `Charge limit: Firmware managed (disabled)`, according to UPower's explicit enabled property. If enabled is absent/unknown, use `Firmware managed (state unknown)`; do not guess. Do not label the mode `Available (Firmware)` or imply the user can edit the firmware in GTK. UPower is the reporting service; firmware describes the mechanism, not a competing backend.
- When a firmware mode and numeric type coexist (mask e.g. 6), prefer a known active numeric threshold and show firmware-specific context only if it adds important information. Never invent percentages for firmware-only mode.
- If UPower threshold support is false or the property is absent, do not show `Charge limit: Supported`, even when a standard kernel sysfs threshold exists; the old legacy fields still show kernel values.
- Settings mask reports control *types*, not enumerated values or editable presets. All GTK and `--stats` paths stay read-only: no buttons, toggles, sliders, helper privilege calls, sysfs writes, or UPower `EnableChargeThreshold()` invocation.

### Physical behavior at and above the stop threshold

An **80% charging limit does not promise active discharge to 80%**. When a user activates 80% while the battery is already 100% and AC remains plugged in, some machines keep 100%, while others/firmware may naturally discharge depending on platform behavior. Do not infer that 100% means the limit is inactive. Changing the stop target is not the same as discharging the battery. ASUS's official Battery Health Charging documentation specifically describes retaining charge above the new target while still plugged in: https://www.asus.com/br/support/faq/1032726/.

Examples below are UI fixtures, not a real claim about Samsung UPower 1.90.3; battery power is illustrative and charging/status follow actual reports:

| Configuration/scenario | Charge | Status | Threshold / charge-limit presentation |
|---|---|---|---|
| Numeric UPower enabled, 55%, charging | 55% | Charging (if reported) | Stop threshold 80% (UPower) |
| Numeric UPower enabled, 80%, stopped | 80% | Actual UPower state | Stop threshold 80% (UPower) |
| Numeric UPower enabled after charge reached 100%, AC still connected | 100% | Actual UPower state | Stop threshold 80% (UPower), *even though charge remains 100%*, when kernel confirms |
| Numeric support available but disabled | 55% | Actual UPower state | Stop threshold 100% (if kernel reports); Charge limit Supported (disabled) |
| Firmware-only enabled, battery 55% | 55% | Actual UPower state | Charge limit Firmware managed (active); no invented stop 80% |
| Firmware-only enabled, battery 80% | 80% | Actual UPower state | Charge limit Firmware managed (active); 80% is not evidence of fixed firmware target |
| Firmware-only enabled after battery reached 100% | 100% | Actual UPower state | Charge limit Firmware managed (active); no forced discharge claim |
| Firmware-only supported but disabled | 55% | Actual UPower state | Charge limit Firmware managed (disabled) |
| Old/no UPower charging-limit API | 99% | Fully charged (when reported) | Existing start/stop sysfs fields with no extra controls |
| Numeric UPower stop 80 configured but kernel reports 100 | Any | Actual UPower state | 100% (kernel) · 80% configured (UPower), not a silent override |

If start hysteresis is supported (e.g. start 75/stop 80), a machine at 79% on AC can legitimately remain **not charging** until charge falls below the start point. Always use its actual reported state. If the charger is disconnected, retain the known configured limit read-only but reflect real discharge.

### Known technical corrections before final PR

1. **Signed EnergyRate:** UPower's official D-Bus Device docs state positive discharge and negative charging; the experimental `battery_upower._decode()` currently rejects all negative rates. Validate finite signed rates, preserve status, and display magnitude in W with tests for charging negative, discharging positive, zero, unexpected sign/state and fallback. UPower's other internal GLib property documentation may constrain internal values differently; test the external D-Bus contract rather than assuming.
2. **`--stats` null formatting:** use `Unavailable` instead of `None` for missing thresholds, consistently with GTK.
3. **Shared read-only formatter:** one source-sensitive presentation function for GTK, legacy battery box (if still used) and stats; avoid frontend-specific duplication, retain transparent conflict display and no unverified "applied" claims.
4. **Private-bus scenario tests:** extend temporary real-D-Bus and Xvfb lab to active/disabled numeric, 100%-before-enable, firmware active/disabled, missing enabled status, contradictory kernel readback, 79%-within-hysteresis and no threshold API.
5. No fan-speed fix: Fn+F11 switched GalaxyMint to low-power, producing 0 RPM; restored other profiles report RPM. That previous investigation is closed.

## GalaxyMint final compact GTK check and production-only staging branch (2026-10-23)

The user executed the compact-version preview on actual Samsung GalaxyMint:
- `Fonte: upower`; `Status: Fully charged`; `Start: Unavailable`; `Stop: 100%`; `Charge limit: None` (diagnostic-only field, correctly **hidden** from GTK).
- GTK Battery section showed Status Fully charged, Charge 99%, AC power Connected, Start threshold Unavailable, Stop threshold 100%, Battery power 0.00 W. No extra capability rows, no layout regression.
- The fan displayed **3000 RPM** while using the performance profile. The prior 0 RPM observation in low-power is resolved and remains out of the UPower work.

Created a clean review branch **directly from the unchanged fork/upstream master** `5a1683037a583785f802b5d039022127b9b154cd`:

- Branch: `feature/upower-read-only-battery-20261023`
- Commit: `b53750a7f5515800846d2884f7ba3f7854fccb8b`
- Files: exactly ten production, packaging and README files: `auto_cpufreq/modules/battery_upower.py`, `system_info.py`, `system_monitor.py`, `gui/app.py`, `gui/objects.py`, `pyproject.toml`, `poetry.lock`, `nix/default.nix`, `snap/snapcraft.yaml`, `README.md`.
- Excluded: all `internal/` plans/tests/mock services and temporary `.github/workflows/upower-*` CI fixtures. No changes or merge to `master`, and no PR opened.

Temporary mock/action results remain accessible in their respective branches. The staged branch's ordinary Linux Build and Nix checks run separately. End-to-end Snap installation under confinement and real newer UPower daemon hardware behavior remain outside the tested coverage; `upower-observe` is a documented auto-connected read interface.

## Compact UPower implementation — verified in temporary CI (2026-10-23)

**Temporary implementation only:** `tmp/upower-compact-validation-20261023` commit `35de5dad73dfd37b44c3deab07bb8785bba1d65d`. This revision implements the compact presentation described above; no production/final PR has been prepared or merged.

- Added `BatteryThresholdDisplay` and `battery_threshold_display()` in `modules/system_info.py`, reused by the GTK `SystemReportView`, legacy `BatteryInfoBox` and terminal `--stats`.
- Numeric UPower limits are embedded in the corresponding start/stop threshold rows. The extra `Supported controls` and `UPower configured limits` rows are removed. Normal kernel 100% readings have no needless source suffix. Conflicting sysfs/UPower values are both retained on the same threshold row and GTK wraps long text.
- UPower-capable but disabled: `Charge limit: Supported (disabled)`; firmware-only: `Charge limit: Firmware managed (active/disabled/state unknown)`, hiding meaningless numeric start/stop rows. UPower 1.90.3 retains the regular layout. GTK has no write controls.
- `EnergyRate` accepts finite positive/negative D-Bus watt readings and normalizes to a nonnegative displayed magnitude while keeping the UPower state authoritative. This addresses the signed-rate API and retains 0.00 W as valid battery-flow data rather than system power.
- Missing start/stop thresholds render `Unavailable` in terminal and GTK; no change to the CPU or AC policy and no writes to threshold attributes.

**Passed GitHub Actions at the reviewed revision:**
- Python 3.9/3.12 unit tests: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40046018302
- Installed-package smoke and real GTK under Xvfb: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40046018280
- Private-D-Bus mock UPower + end-to-end GTK/terminal, nine cases: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40046018274
- Linux Build: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40046018291
- Nix Flake: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40046018276

The tests ran on GitHub-hosted Ubuntu virtual machines, using a private D-Bus and Xvfb for GTK. These **do not** prove operation with an actual newer system UPower daemon on physical hardware, and they do not validate Snap confinement end-to-end.

**Still before final PR:** review dependency and packaging diff; revisit source formatting/line lengths; consider a fresh actual-GalaxyMint GTK and stats preview for the compact version, including fallback; analyze existing UPower 1.90.3 behavior, CPU-policy nonregression and multi-battery selection. Production-only changes will be copied to a separately reviewed contribution branch; all internal docs, private-bus mock, test fixtures and temporary CI workflows must be excluded.

## Code integration inventory

- `auto_cpufreq/modules/system_info.py`: `BatteryInfo`, `battery_info()`, `battery_power_label()`, `get_battery_path()`, `external_power_state()`.
- `auto_cpufreq/modules/system_monitor.py`: battery section in `--stats`.
- `auto_cpufreq/gui/app.py`: `SystemReportView`, `_battery_status()`, live battery labels.
- `auto_cpufreq/gui/objects.py`: legacy `BatteryInfoBox.refresh()`; keep status semantics consistent.
- `auto_cpufreq/battery_scripts/{battery.py,shared.py}`: existing charge threshold writer; review for conflicts, do not modify in the telemetry PR.
- `README.md`: make a short public note only after code and tests pass, explaining optional UPower and preserved sysfs fallback. Do not include this internal plan or test harness in the final PR.

## Reference implementation reviewed: Battery Health Cinnamon applet

Reference: https://github.com/lucasleocs/cinnamon-spices-applets/tree/battery-health-applet/battery-health@lucasleocs (branch commit `ac4fbdd385ceb8adf6e341c5dbbb8bd6e53eaac3`).

Useful design patterns in `6.7/applet.js`:

- Discover UPower's physical system batteries using `Type == Battery`, `PowerSupply == true`, and `IsPresent == true`. auto-cpufreq additionally **must** match the configured physical battery by `NativePath` instead of aggregating all system batteries.
- Distinguish a missing optional D-Bus property from a supported property with value `false`. Do not reject general UPower telemetry merely because the threshold API is old or absent.
- Decode `ChargeThresholdSettingsSupported` bitwise (start=1, end=2, firmware=4); do not present that mask as a list of permitted percentages. Treat 4294967295 as unavailable rather than a valid threshold.
- Describe end-only, start-and-end, firmware-managed, and differing multiple-battery limits without guessing values.
- Detect UPower service disappearance/reappearance and invalidated device state; a stale proxy must not block sysfs fallback.
- For future event-driven updates, consider the applet's D-Bus owner-generation approach, but do not port its full asynchronous lifecycle or event system into the initial periodic `--stats` path.

Do **not** port the applet's `EnableChargeThreshold()` writes, Cinnamon-only UI code, permissions workflow, or its minimum Cinnamon version. The applet is a reference, not a runtime dependency or a guarantee that custom percent configuration is available.

## Modern UPower simulation via an isolated private D-Bus — verified (2026-10-23)

Purpose: validate *the full applet-independent Python D-Bus reader*, `SystemInfo.battery_info()`, the `SystemMonitor.format_system_info()` output and the real GTK `SystemReportView` against newer UPower **property shapes**, without changing the installed UPower or charging hardware.

Temporary test branch: `tmp/upower-modern-privatebus-20261023` at `dba4fe0b21ea4ac63c892ff7fd441e2efe383853`.
Temporary lab: `internal/upower_modern_lab.py`; CI workflow: `.github/workflows/upower-modern-privatebus.yml`.
CI: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40042055797 — PASS, all four scenarios in GTK+stats under Xvfb. Linux Build and Nix Flake passed for the same temporary revision.

The lab starts its own `dbus-run-session` and sets `DBUS_SYSTEM_BUS_ADDRESS` *inside that child process* to the new private bus address. A read-only mock service owns `org.freedesktop.UPower` on the private bus, implements `EnumerateDevices` and device `Properties.GetAll`, and denies all charge-limit writes. It does **not** upgrade, replace, restart, mask or write to the host's UPower system daemon, kernel attributes, or charging policy.

Tested scenarios (mock values are not hardware-supported preset lists):

| Scenario | UPower properties | GTK + stats confirmed |
|---|---|---|
| `end-only` | Supported=true; mask=2; configured stop 80%; enabled=true | Show End threshold and UPower configured Stop: 80% |
| `start-end` | Supported=true; mask=3; configured start 75%, end 80%; enabled=true | Show Start/End and configured 75–80% |
| `firmware` | Supported=true; mask=4; percentages unavailable | Show Firmware managed; no invented percentages |
| `legacy` | Charge-limit properties absent | Existing display only; optional rows hidden |

Crucial honesty: this is **not an execution of UPower 1.91.3**. It verifies the standard D-Bus interface and frontend against valid simulated new properties. Running a newer actual UPower daemon on real hardware requires a separate isolated service/umockdev integration lab and cannot be inferred from these mocks. UPower 1.91.2 introduced end-only kernel support, but whether a specific laptop's threshold properties are available also depends on detection and configuration. Do not update or restart the host UPower merely to make its version number newer.

Observation: `--stats` currently prints `Charging start threshold: None` where GTK says `Unavailable`. This is a pre-existing presentation inconsistency and should be corrected carefully as a small targeted UI follow-up. The mock also intentionally shows **sysfs observed stop=100** separately from **UPower configured stop=80**; simulated configured values are not claims about the real applied battery hardware.

Decision: keep the mock/CI workflow on disposable branches. Neither the private-bus lab nor this internal record belongs in the final contribution PR.

## Observed GalaxyMint GTK results and resolved fan-speed observation (2026-10-23)

Reference: GalaxyMint (Samsung Galaxy Book, Linux Mint 22.3, kernel 7.2.12-x64v3-xanmod1).

- **UPower/Battery verified:** BAT1 99%, AC connected, start threshold unavailable, kernel stop 100, battery power 0.00 W. Installed GTK showed `Not charging`; UPower experimental GTK correctly showed `Fully charged`. UPower 1.90.3 provided no charge-capability API, so optional labels were hidden.
- **Performance measured on the actual notebook:** 20 successive `SystemInfo.battery_info()` reads, 20/20 sourced from UPower, median **5.5 ms**, maximum **122.5 ms**. One earlier integrated call measured 23.9 ms. This is a limited sample, not a hard latency guarantee.
- **Prototype layout:** the experimental window intentionally rendered `SystemReportView` alone, without the production control panel, CSS and full shell. Header spacing and styling differences are expected.
- **Fan/RPM concern resolved for now:** the user identified a prior Fn+F11 switch from `performance` to `low-power`; `0 RPM` appears in `low-power`, while other profiles show RPM. This is consistent with a profile-dependent fan state and **is no longer an open suspected auto-cpufreq bug**. Remove the fan/sensor investigation and any associated proposed correction PR from scope. Reopen only if contradictory evidence arises independently.
- **Temperatures:** earlier screenshots near 90–93 °C were load-dependent snapshots; a later capture around 58 °C at low CPU usage does not support a battery-feature regression. No temperature fix is requested in this PR.
- **Minor presentation inconsistency:** legacy `BatteryInfoBox.refresh()` and the stats formatter show missing thresholds differently from the main GTK. Normalize to `Unavailable` as a small shared output correction if it is done without changing threshold semantics.

## Future workstream — declarative charge limits in auto-cpufreq.conf (NOT part of this reporting PR)

Goal: allow explicit administrator configuration such as an end limit of `80%` using **existing** auto-cpufreq configuration conventions. The GTK remains read-only; `--stats` reports both requested configuration and observed effective limit, when determinable. No interactive GTK editor, no universal list of accepted percentages, and no new vendor commands.

### Existing implementation and migration constraints

Current code: `auto_cpufreq/battery_scripts/battery.py`, `shared.py`, `ideapad_laptop.py`, `asus.py`.

- `get_battery_device()` detects particular kernel modules (`ideapad_acpi`, `ideapad_laptop`, `thinkpad_acpi`, `asus_wmi`). Samsung BAT1 can expose a standard end-threshold sysfs attribute while not matching those branches. Existing vendor-specific conservation mode must be preserved, not silently replaced.
- `get_parsed_config()` requires both `start_threshold` and `stop_threshold` when `enable_thresholds=true`. `set_battery_thresholds()` also requires both sysfs attributes and temporarily writes stop=100 while setting the pair. This prevents a *stop-only* device from using the current generic path; it also makes a write more invasive than a simple end-threshold request.
- Existing daemon periodically reapplies user threshold configuration (currently once per hour). There must be **one owner / one write path**, not a new independent UPower controller competing with the existing writer.
- The first version of the new read-only UPower provider must not touch those writers or change the semantics of an existing configuration. Any refactor comes in a separate PR with an explicit compatibility migration and tests.

### Proposed future administrator experience (illustrative, not enabled)

```ini
[battery]
enable_thresholds = true
stop_threshold = 80
```

This is a **design target** for stop-only devices and is **not currently valid** in the old implementation without `start_threshold`. Existing two-threshold configurations must keep working unchanged:

```ini
[battery]
enable_thresholds = true
start_threshold = 75
stop_threshold = 80
```

An omitted start threshold must mean **do not touch or fabricate a start threshold** when the backend supports stop-only operation. Conversely, when the hardware requires a paired start/end setting, the program must validate that requirement and refuse an incomplete configuration with a clear error; it must not assume all devices support end-only operation.

### Future provider strategy to be decided after a read-only capability audit

- For UPower's preconfigured preservation mode, `EnableChargeThreshold(bool)` may be a future opt-in feature, but cannot set an arbitrary `80%`. UPower `ChargeStartThreshold` and `ChargeEndThreshold` are read-only D-Bus properties. Do not repurpose `EnableChargeThreshold(true)` as if it accepted `stop_threshold=80`.
- For a **custom** requested limit, a standard kernel `charge_control_end_threshold` writer may be necessary. This is **not vendor-specific code**: kernel drivers own the hardware-specific mapping. Only support documented `charge_control_*_threshold` ABI for a generic path. No EC commands, driver-private paths, model allowlists, arbitrary firmware pokes, or blind sysfs probing.
- Do not automatically fall back from one failed write backend to another. First establish control ownership and conflicts with UPower, TLP, desktop settings and auto-cpufreq's current hourly writer. Refuse or clearly require resolution when ownership is ambiguous; never silently override another manager.
- If UPower's enabled preservation mode is active, do not directly overwrite its threshold under the hood. Decide on an explicit single-controller policy in the separate write-feature design. Merely reporting that UPower is installed does not imply it owns all charge settings.
- The write operation must be explicit opt-in, executed only by an authorized code path (for daemon config, existing controlled root service as applicable); never from a GTK refresh, stats display, or device detection.
- Resolve the selected physical battery using the existing `battery_device` and established safety checks. Do not write to all BAT* implicitly when one battery is selected.
- Take pre-change readings, check declared sysfs capability, validate integer percentage and start<stop when both configured; write only the attributes that are actually present and intended. Reread each attribute after writing; report the actual driver value. A rounded or rejected request is **not** proof that 80% was applied. Define explicit error, no-retry and recovery behavior for partial writes and service interruptions. Never automatically probe a range of unknown values.
- Persist only via the existing configuration mechanism; no additional background service or new write-loop. Audit startup, resume, multiple batteries and possible existing periodic reapplication before exposing this to users.
- No promise that selecting 80% immediately discharges a battery currently at 99%.

### Release gates for the future write feature

Gate W1: full read-only UPower integration and fallback already verified.
Gate W2: generic standard-kernel capability handling is proven with simulated end-only, paired, read-only, and absent attributes, while preserving vendor conservation modes.
Gate W3: existing config compatibility and opt-in semantics documented, including start-omitted handling and conflict policies.
Gate W4: simulated write/readback, rounding, permission-denied, partial-write, UPower-managed and concurrent-controller cases pass without changes to real hardware.
Gate W5: real hardware trial on an explicitly opted-in machine, with original values recorded and a documented restoration procedure; no automatic writes as a detection test.
Gate W6: review release packaging, README, service lifecycle, `--stats` presentation and final diff. Keep all internal plans, temporary tests and experimental workflows out of the final contribution.

## Phased delivery

1. **Transport spike** (temporary only): identify D-Bus client, test physical-battery mapping and bounded failure paths, compare packaging across installers.
2. **Shared data model**: UPower-first battery snapshot, robust sysfs fallback, shared formatters, no power-policy changes.
3. **Frontends**: update GTK, legacy GTK battery box, and `--stats`, with optional read-only UPower threshold metadata.
4. **Verification and audit**: tests in temporary branches; Linux/Nix CI; headless CLI; GTK on real hardware; assert the final contribution branch contains no tests, plan documents, workflows or experimental scripts unless explicitly approved as production tests.
5. **Separate future configuration PR**, not in this reporting PR: modernize the existing threshold code toward standard-kernel stop-only/paired capabilities and declarative `stop_threshold = 80` where supported, with strict single-writer ownership, permission, rollback/recovery and readback safeguards as specified above. No GTK editor.

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

## Full native GTK with private UPower D-Bus — validated (2026-10-24)

- Branch `tmp/upower-full-gtk-privatebus-20261024`, commit `fe404680328dfb56adaae14da02287105f9b944a`. Only disposable fixture `internal/upower_full_gtk_preview.py` and dedicated temporary GitHub Actions workflow `.github/workflows/upower-full-gtk-privatebus.yml` were added to the previous mock branch; the clean contribution and upstream-synced `master` were unchanged.
- The full `ToolWindow` (not just `SystemReportView`) was created, with Controls, menu, ordinary stylesheet and icon, and the real CPU/system data; installed daemon detection is preserved on the actual notebook, but CI with no daemon uses an explicit `--mock-battery` fake to select the full panel. No autoupdate startup check is invoked.
- A private, separately launched `dbus-daemon` supplies `org.freedesktop.UPower` D-Bus replies as the client's **system bus override within this test process**, without replacing the desktop session D-Bus or the host's system UPower service. Privileged GUI actions are intercepted in the test. The optional `--mock-kernel-stop 80` intercepts only Python reads of BAT1 threshold attributes to show a matching readback; it does not write to sysfs or configure hardware.
- CI https://github.com/lucasleocs/auto-cpufreq/actions/runs/40049136671: PASS with a complete GTK under Xvfb for `charging-55`, `reached-80`, `full-100`, `start-end`, `firmware`, `firmware-disabled`, `disabled`, and `legacy`. It checked the required Controls panel and correct battery status/percent/threshold/charge-limit labels. Linux Build and Nix Flake for same commit also passed (runs 40049136447, 40049136544).
- This tests modern **UPower properties** and app rendering; it does not install, update, or execute a real UPower 1.91.x service. Real newer-daemon physical hardware tests and strict Snap-confinement runtime remain outside this coverage.
- The user also ran a complete GTK preview against their actual installed daemon, without privilege changes, and supplied a screenshot showing the full Controls panel plus BAT1 99%, Fully charged, AC connected, start Unavailable, stop 100%, battery power 0.00 W. The production layout was preserved.


## GalaxyMint full GTK visual acceptance — five simulated battery states (2026-10-24)

The user executed `tmp/upower-full-gtk-privatebus-20261024` at `fe404680328dfb56adaae14da02287105f9b944a` directly on the GalaxyMint, with the normal auto-cpufreq daemon still running. Provided **five screenshots plus terminal logs**, all with `Source: upower`, `Installed daemon active: True`, `Controls present: True`, and `Physical charge thresholds: never written`.

| Scenario | Shown by full GTK | Visual check |
|---|---|---|
| `charging-55` | Charging; Charge 55%; Start Unavailable; Stop 80% (UPower); Charging power 12.50 W | PASS, clean layout, no redundant protection row |
| `reached-80` | Pending charge; 80%; Stop 80% (UPower); Battery power 0.00 W | PASS, genuine UPower state preserved |
| `full-100` | Fully charged; 100%; Stop 80% (UPower); Battery power 0.00 W | PASS, threshold is not the current charge or automatic discharge |
| `start-end` | Fully charged; 99%; Start 75% (UPower); Stop 80% (UPower) | PASS, both numeric limits inline; 99% above 80% is a simulated state and can correspond to changing a limit after a fuller charge |
| `firmware` | Fully charged; 99%; Charge limit Firmware managed (active); no start/end rows | PASS, one concise firmware line and no invented percentage |

**Presentation observations:** no overflows, clipped text, missing Controls, extra protected rows, or unintended edit controls. In the firmware-only scenario, `Charge limit` appears **after** `Battery power` because it is the final GTK grid row while start/stop are hidden. A small **optional** formatting improvement before the final PR is to keep charging-limit information before Battery power consistently, without rebuilding the GTK or changing its data semantics; discuss before altering the clean PR branch. This is cosmetic, not a functional failure.

All five executions emitted `Failed to retrieve unit state: Process org.freedesktop.systemd1 exited with status 1` while the private mock overrode the system-bus address in its own process. The errors were **nonfatal in the laboratory** (full GTK and daemon detection proceeded normally) and do not establish a failure of the installed systemd service or UPower. Avoid confusing the experimental message with a production error. Later improvement could isolate host `systemctl` queries from that temporary D-Bus override, strictly within the lab.

This user hardware run supplements, but does not replace, CI https://github.com/lucasleocs/auto-cpufreq/actions/runs/40049136671, which already passed eight scenarios headlessly with full GTK. No actual newer daemon (UPower 1.91.x) was installed: only its D-Bus property *shape* was simulated. No system threshold was changed.


## Second contribution — optional generic kernel charge-stop configuration (separate, not merged)

The existing auto-cpufreq `[battery]` settings support `enable_thresholds`, `start_threshold`, `stop_threshold` since v2.2.0, but vendor-driver selection and the parent `BatteryDevice` start/end prerequisite prevent Samsung BAT1 from applying stop-only using standard Linux `charge_control_end_threshold`.

The user approved beginning this phase as a **second PR**, preserving read-only GTK and the previously isolated first UPower PR.

- First read-only UPower branch `feature/upower-read-only-battery-20261023`, now @ `26bc01dd3745405961cb069b02d00125a90e28fd`. UPower State 5 (`Pending charge`) now reports `Not charging`, in both D-Bus mapping and the user-facing status helper. No other status changed.
- Temporary implementation branch: `tmp/generic-kernel-charge-threshold-20261024` @ `b6e2e42082fdebac19c4d6b6757ad8067c1613cc`. Test fixture `internal/test_generic_kernel_threshold.py` and private workflow only in this branch.
- Clean second-stage branch from first feature's head: `feature/generic-kernel-charge-threshold-20261024` @ `299876536e33f8f22bbe58669457fc297f7b1f1c`. Exactly three changed files relative to the first feature: `battery_scripts/generic.py`, `battery_scripts/battery.py`, `README.md`. No temporary tests/workflows.
- Syntax supported by user's desired config: `[battery]`, `enable_thresholds = true`, `stop_threshold = 80`; `start_threshold` may be omitted when no start attribute exists. Default remains no write and no GTK editor.
- Generic strategy: select a single physical battery using existing `SystemInfo.get_battery_path()` (respecting `battery_device` and ignored peripherals), require standard kernel end attribute and root-writable stop file, preserve Lenovo/ASUS vendor paths first, apply only in existing battery-daemon loop after opt-in, re-read sysfs after writes. If the requested value is rounded by the driver, surface actual readback and cache it to avoid unnecessary periodic rewrites; reject unchanged or invalid readback. When UPower explicitly reports charging protection supported **and active**, refuse competing writes.
- A configured start on a battery with no start attribute explicitly fails rather than creating fake support. Existing start+stop vendor behavior is preserved. Explicitly note that `enable_thresholds = true` can affect physical charging **only after a future installation of the control-feature branch and root daemon restart**. No physical Samsung write was made during development.
- Temporary CI run https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050271658: 12 fixture-only safety tests PASS (Python 3.12). Linux Build and Nix Flake also PASS on the temporary branch (runs 40050271568 and 40050271631). Earlier failed test runs found and corrected the unchanged-kernel-readback path. Full program imports of some *pre-existing* battery modules remain incompatible with Python 3.9 type evaluation; do not claim the new writer validated on Python 3.9 or broaden this PR solely for unrelated legacy compatibility.
- **Still required:** optional physical BAT1 opt-in/write validation only with explicit user agreement; full daemon/Snap runtime of new writing feature untested. Separate feature branch is not yet an upstream PR or merged to `master`. Do not silently change user's `/etc/auto-cpufreq.conf`, daemon, or hardware thresholds.


## Audited priority: UPower capability vs writable backend (2026-10-24)

**Current state: UPower-first reading is implemented but UPower-first charge-limit *writing* is NOT.** Separate telemetry priority from charging policy.

Read-only telemetry implementation `SystemInfo.battery_info()`: sysfs values are collected and UPower, when healthy and matching the physical battery, overrides battery status/percentage/rate. AC external-power policy and observed sysfs threshold remain separate. On UPower failure, sysfs telemetry is retained.

The second charge-controller branch `feature/generic-kernel-charge-threshold-20261024` currently selects existing manufacturer drivers (`ideapad_acpi`, `ideapad_laptop`, `thinkpad_acpi`, `asus_wmi`) before its generic kernel fallback. Only the generic fallback calls `read_upower_battery()`, and that call checks whether `ChargeThresholdSupported` and `ChargeThresholdEnabled` are both true: if yes, it refuses sysfs writing; otherwise it attempts sysfs.

**The official UPower D-Bus `EnableChargeThreshold(bool)` toggles the *previously configured* numeric/firmware mode, not a user-defined arbitrary percent.** `ChargeThresholdSettingsSupported` encodes start/end/firmware control types, not accepted percentage steps. Never treat UPower metadata presence as ability to apply `stop_threshold=80` by D-Bus. Privileged UPower toggling is *not* part of either current PR. Kernel `charge_control_end_threshold` is writable when exported, but drivers can clamp or round and the returned value must be checked.

On Samsung `samsung-galaxybook` there is **already a firmware adapter**: its standard BAT1 `charge_control_end_threshold` kernel sysfs attribute goes through the driver into Samsung ACPI/firmware. The Samsung kernel documentation confirms stop-only, no start, 100=off, and values 50/60/70/80/90 recommended for Windows Settings interoperability. Values below 30 can disable protection on some machines. This does not warrant creating brand-specific writer code.

**Actual current-behavior audit**: `tmp/charge-priority-audit-20261024` commit `f4ad47b84f171e3574699171b68fc8a13e83069a`, all ten audit tests plus twelve existing generic safety tests passed: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050734230. Linux Build https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050734123 and Nix Flake https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050734093 also passed. **PASS means the tests accurately captured present behavior, including the unsafe gaps below, not that the desired backend priority is implemented.** All sysfs writes were directed only to temp files.

| Inputs | Current behavior | Desired policy |
|---|---|---|
| UPower healthy for telemetry | UPower status/%, power override sysfs | Keep |
| UPower charge API absent/unsupported; standard kernel writable; opt-in 80 | Kernel writes 80 and checks readback | Keep |
| UPower charge supported+enabled, configured end 80, kernel already 80 | Generic writer refuses and logs warning | Recognize already satisfied, do not write or warn |
| UPower charge supported+enabled, configured end differs requested | Generic writer refuses | Conflict: do not override or silently call firmware; report why |
| UPower charge supported+disabled, preset 80 | Generic writer currently directly writes sysfs | Decide ownership first; future D-Bus enable could apply matching preset, but not arbitrary percentages. Until supported, a sysfs fallback requires no conflicting active owner and explicit user opt-in |
| UPower charge supported, enabled state **unknown** | Generic writer currently attempts sysfs | **Fail closed**: do not write without determining whether protection is active |
| UPower unavailable due timeout vs old UPower without threshold API | Both currently lead to sysfs fallback | Distinguish unavailable/ambiguous from explicitly unsupported; avoid forcing a write when active owner status may be unknown |
| Firmware-only UPower active | Generic writer refuses sysfs | Keep block; no numeric percentage may be presumed |
| Existing vendor driver identified, generic kernel writable, UPower active | Existing vendor device is selected before generic, potentially skipping UPower guard | **Cross-backend ownership check** before every writer, retain vendor compatibility but do not let vendor selection silently bypass an active UPower controller |
| UPower has threshold support, but kernel provides no writable attribute | No generic controller selected | Correct unless a separately implemented authorized D-Bus operation can satisfy exactly the requested preset; do not invent a kernel attribute |

### Desired conservative priority (future implementation)

1. Parse explicit `[battery] enable_thresholds=true` and valid target; without opt-in no control action.
2. Select exactly one system battery and inspect UPower charge capability/active/unknown, configured preset and standard kernel/sysfs attributes. Distinguish `supported`, `enabled`, `known preset matches requested`, `unavailable` and `unsupported`.
3. If existing UPower-managed state **already satisfies** the desired threshold and observed readback is consistent, report `already applied` without writes.
4. If UPower could actually activate a *matching existing preset* through its officially documented toggle, support that only in an explicitly authorized, separately tested implementation; it does **not** support arbitrary stop percentages. Do not add a D-Bus write to the read-only PR.
5. Otherwise use writable standard kernel thresholds only when ownership is safe, no active/unknown UPower conflict exists, and the device can accept the requested operation; read back and report effective value. Old UPower with missing API + Samsung BAT1 stop-only should normally use this route.
6. Preserve older vendor-specific features as compatibility fallback when standard interface is not suitable; do not let a vendor module silently take precedence over an already active UPower controller. Firmware-only mode with no numeric thresholds must not be mistaken for `stop=80`.
7. If no safe controller exists or the readback cannot validate success, fail transparently without trying a riskier backend, an alternative percentage, or automatically writing to firmware.

No change was made to the clean `feature/upower-read-only-battery-20261023` or `feature/generic-kernel-charge-threshold-20261024` during this audit. Do not present second PR as priority-complete until an explicit resolver/ownership policy and simulated edge-case tests are implemented.

Official references:
- https://upower.freedesktop.org/docs/Device.html
- https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-power
- https://dri.freedesktop.org/docs/drm/admin-guide/laptops/samsung-galaxybook.html


## First centralized charge backend resolver — experimental only (2026-10-24)

New temporary implementation `tmp/charge-priority-resolver-20261024` (latest inspected commit `82b4d5158d8be2e7bcbdc8f364e7d312c402cad6`). Neither `master` nor the clean read-only UPower and generic kernel-threshold contribution branches was changed.

The shared `BatteryDevice.apply_threshold_settings()` daemon path calls `battery_scripts/priority.py:resolve_charge_control` after opt-in config parsing and **before** invoking any concrete writer (generic kernel, legacy Asus/ThinkPad, or explicit IdeaPad conservation mode). Three possible read-only decisions:
- `satisfied` — UPower actively reports the requested numeric stop and (when configured) start, and sysfs readbacks match. No writing and no misleading conflict warning.
- `apply` — healthy UPower reports threshold API unsupported, or protection explicitly disabled. A suitable generic kernel or vendor writer can act; validation and readback remain writer responsibilities.
- `blocked` — UPower temporarily unavailable/missing, enabled state unknown, active firmware-managed mode without numerical certainty, active configured target differs, or kernel readback conflicts. No fallback to potentially unsafe writes.

Importantly **no arbitrary percentage is written through UPower**; its documented `EnableChargeThreshold()` is only a toggle for already-configured settings, so a genuinely UPower-first writer would require its own authorized transaction + verified readback, beyond this stage. Current resolution is UPower-first *inspection*, then kernel or existing compatibility writer.

Generic standard `charge_control_end_threshold` is preferred when writable. Explicit `ideapad_laptop_conservation_mode` retains IdeaPad-specialized routing (including when both `ideapad_acpi` and `ideapad_laptop` modules appear). Without a standard writable stop attribute, old vendor paths are kept as compatibility fallback, while the shared daemon preflight still checks ownership.

**Cautions:** The direct internal `apply_threshold_settings_to_bat` helpers are not public entry points; preflight is guaranteed by the existing daemon calling `apply_threshold_settings()`, not by invoking low-level direct methods from other code. The code does not detect conflicts with every possible external daemon such as TLP, and has not been used to write a physical Samsung threshold. A missing UPower service is intentionally treated as *unknown* and fail-closed; this prioritizes safety but prevents generic writes on machines with no usable UPower service until absence vs transient failure can be distinguished. The existing multi-write vendor start/end path still has its original rollback/readback limitations. These restrictions should be evaluated before promoting the branch to a clean PR.

Tests: 12 original generic safety cases plus 18 desired-policy priority cases PASSED at `0265271cf92cb0b17a67bb1e774edf79978e6053` (https://github.com/lucasleocs/auto-cpufreq/actions/runs/40051189084). Additional Lenovo dual-driver and unknown-firmware test added in `82b4d515...`, bringing total expected to 32; see latest CI https://github.com/lucasleocs/auto-cpufreq/actions/runs/40051320251. Linux Build and Nix Flake also passed at the earlier 30-test revision (runs 40051189152 and 40051189061); check the newest runs for final status. All writes in tests went to TemporaryDirectory fixture files, never physical hardware.


## Official-source remediation design: service discovery, competing controllers, legacy writes (2026-10-24)

**Research and architecture review only:** no firmware, local machine, daemon, clean contribution branch or experimental resolver code changed during this step. Based on official UPower D-Bus Device API, D-Bus specification, kernel power-supply sysfs ABI and Samsung Galaxy Book admin guide, and upstream TLP conflict/battery-care documentation. The proposed fixes below supersede the blanket 'missing UPower = always block' policy and avoid mislabeling UPower as a sole writer/owner.

### A. Distinguish service unavailability from not installed

Current `read_upower_battery()` collapses any exception, bus failure, unrecognized battery, missing properties or a timeout to `None`. In charging-control arbitration, the absence of a telemetry snapshot **is not sufficient evidence of an exclusive active owner** or of complete service absence.

Plan: add a **separate typed read-only capability/probe result**, rather than changing the stable GUI/telemetry interface or making the full `_decode()` status/percentage/rate requirements necessary for charging-policy discovery.

Suggested statuses:
- `ready_no_charge_api`: matching physical battery found and UPower positively reports no charge-control support or an older API lacks the new fields. Eligible for standard-kernel controller with explicit user opt-in.
- `ready_charge_api`: matching physical battery charge capabilities, enable state and configured numeric/firmware mode are available. Distinguish enabled, disabled, and unknown.
- `bus_confirmed_no_service`: connected to a healthy system bus; UPower name has **no owner**, and `ListActivatableNames` does not list `org.freedesktop.UPower`. Eligible for kernel fallback only after ordinary battery/competing-writer checks. Presence of a binary/package alone is not authoritative.
- `transient_failure`: system bus connection failed, a name is activatable but the daemon did not start, bus method timed out, invalid D-Bus payload, permission denied, matching device not established, or UPower restarted mid-query. **No new write**, retry on a later iteration.
- `unknown_active`: charge-control capability is known, but its enable state or expected effective numerical limits cannot be trusted. **No new write**.

Use `org.freedesktop.DBus.NameHasOwner` and `ListActivatableNames` (optionally `NameOwnerChanged`) before interpreting 'service absent'; do not assume NameHasOwner=false means absent because D-Bus supports activation. Keep probes short, deterministic and cached between periodic daemon checks where appropriate; the existing GUI read-only fallback must remain quick and independently reliable. Once the service is contacted, preserve the matched physical battery identification (NativePath, PowerSupply, IsPresent). D-Bus checks can race with service startup/shutdown, so always catch errors after discovery too. This scheme can safely allow Linux installations without UPower.

### B. Avoid false exclusive ownership; prevent configuration wars

UPower's documented `EnableChargeThreshold(bool)` turns **previously configured** charge control on or off, but has no arbitrary-percent input. Its `ChargeThresholdSettingsSupported` is a bitmask indicating start/end/firmware capabilities, not allowed percentages. On typical Linux laptops both UPower and another userspace writer access the **same kernel sysfs and EC/firmware behavior**. Therefore an UPower active flag describes *protection status*, NOT exclusive ownership. Do not build a backend 'lock' from UPower flags.

Recommended policy:
1. `enable_thresholds=false` (default) means zero writes, regardless of any detection results.
2. Select a specific system battery, validate requested start/stop and complete readbacks, then classify UPower read-only capabilities and competing managers.
3. If observed limits already satisfy the user request, report `already applied` without writes. If UPower also exposes consistent configured limits, report this consistency; distinguish configuration values from actual hardware state.
4. If UPower's active configured values disagree with an explicit new request, do not silently toggle/override. Show clear conflict/ownership warning and require separately designed authorized takeover if ever necessary; the current writer remains sysfs only.
5. With no conflicting active mode and a healthy confirmed kernel attribute, permit the standard kernel operation. Vendor backend remains a compatibility fallback if kernel-standard end attribute is absent; never automatically retry a different writer after a failed write.
6. Detect **known competing charge manager** signals (e.g. active `tlp.service` and battery-threshold configuration, or TLP's `tlp-stat -b` where installed), and **suspend automatic battery threshold writes** rather than disabling or modifying someone else's service. Detect 'installed' vs 'active' separately; service detection is a diagnostic, not proof of ownership. In general TLP itself explicitly discourages simultaneous TLP + auto-cpufreq.
7. Monitor readback over time. After *our* verified application, if a value changes externally while our requested value/config is unchanged, mark `external_change`, log a **single** conflict warning and **suspend** further automatic rewriting. The current generic controller's periodic retry when `_accepted_stops` changes could otherwise fight another manager hourly. Do not assume absence of active services implies exclusive control. Re-enable on an explicit new user action/config change or daemon restart only after rerunning safety checks.
8. Preserve initial readback when the driver rounds the requested level, report actual value without claiming exact target; kernel ABI explicitly permits rounding. Define an acceptance policy for rounded values (e.g. strict requested target vs user-accepted approximation) and avoid repeated write loops. Never silently select a *new* arbitrary preset.
9. Use run-wide/shared arbitration for all existing writers and preserve intentional Lenovo conservation mode, which is firmware Boolean mode, NOT numeric stop=80. Avoid unintended changes to unrelated CPU governor or platform_profile managers.

Note for Samsung BAT1: the official samsung-galaxybook driver already bridges standard `charge_control_end_threshold` writes into the firmware, provides no start threshold, treats 100 as disabled and documents 50/60/70/80/90 as interoperable presets; no vendor-specific ACPI writer should be added. Other values below 30 may disable the function on some Samsung hardware. The documented physical EC behavior also means activating 80% after charging to 100% does **not** actively force discharge while AC is attached.

### C. Replace risky legacy start+stop writes with validated ordering

The current inherited `BatteryDevice.set_battery_thresholds()` **always writes stop=100, then start, then stop** with `sleep(0.1)`; it does not read back after each write. A second/third write can fail after the effective upper limit was temporarily lifted to 100. The 100% intermediate value and unconditional hourly rewrites are unnecessarily intrusive.

For supported numeric start+stop batteries:
- Read/validate current pair `S_old < E_old` and target `S_new < E_new`. If source values are unknown or the requested operation cannot be validated, fail before writing.
- If both already match, do **nothing**.
- Determine a safe intermediate without the unconditional 100% detour:
  * If `S_new < E_old`, start can be written first; then write stop. Example 80/90 → 50/70: lower start first.
  * Otherwise, if `E_new > S_old`, stop can be written first, then write start. Example 60/80 → 90/95: raise stop first.
  * If neither safe intermediate exists, **abort without a write**; do not guess a value.
- Verify both values after **each** write, including any hardware-specific rounding. Abort if the readback breaks constraints or is unknown. Do not claim success based only on `open().write()` returning.
- On failure after the first write, attempt one carefully bounded **best-effort restoration of the saved old values**, only when safe based on fresh readbacks. The restore itself can fail; explicitly report `partial application` with the observed pair, not atomic success. Avoid writing `100` as a generic rollback.
- Never set a kernel 100% stop as a temporary escape hatch without a specifically documented driver requirement and a narrow protected code path.
- Preserve deprecated vendor attribute compatibility where required, but separate legacy behavior changes and their tests from the initial stop-only Samsung use case to avoid expanding the contribution unnecessarily.

This is an engineering transaction design, not a guarantee that two independent sysfs writes can become fully atomic. The kernel documents value ranges and actual-readback/rounding, but not a universal atomic multi-field update contract. Treat driver-specific EINVAL and quirks as legitimate failure modes, not as instructions to add brand tables.

### Release/testing sequence

1. First, add a typed D-Bus service probe (absent vs temporarily failed vs active but unknown), with offline private-bus cases.
2. Add a read-only ownership/conflict preflight for external battery managers and record already-satisfied behavior. Simulate an external change following our write and verify no fighting/rewrite loop.
3. Replace unconditional stop=100 multi-write sequence with validated minimum writes and readback/partial-rollback tests in **a separate safe legacy refactor** if needed; the standalone Samsung stop-only feature should not depend on it.
4. Add serial/headless VM tests for race conditions (UPower starts/stops between discovery and action, dual batteries, missing attributes, firmware-only, readback rounding, write success but unchanged, partial failure).
5. Finally, only after all passing in disposable branches and user permission, test *read-only* GalaxyMint preflight followed by a separately authorized 80% physical write, confirming readback and preserving original settings.

Sources (official):
- UPower API: https://upower.freedesktop.org/docs/Device.html
- D-Bus name ownership/activation: https://dbus.freedesktop.org/doc/dbus-specification.html
- Linux charge thresholds ABI: https://github.com/torvalds/linux/blob/master/Documentation/ABI/testing/sysfs-class-power
- Samsung Galaxy Book driver: https://docs.kernel.org/admin-guide/laptops/samsung-galaxybook.html
- TLP conflicts: https://linrunner.de/tlp/faq/conflicts.html
- TLP battery-care/EC persistence: https://linrunner.de/tlp/faq/battery.html

### Persistence and configuration switching — adjacent acceptance checks

- Threshold values can persist in EC/firmware across system restarts and even alternate operating systems. Disabling `enable_thresholds` means **stop writing**, not automatically resetting existing hardware values to 100%. A future reset-to-default operation must be a separate, explicit user-authorized action with driver-aware validation; for Samsung, 100 is documented as the 'off' value.
- In existing `start_battery_daemon()`, the battery backend object is selected **once** before the hourly loop, even though `get_parsed_config()` is called during each iteration. If a user changes the config from numeric thresholds to explicit IdeaPad conservation mode or vice versa, the cached backend may not match the new mode until the service restarts. Preserve current behavior for the first PR and require a daemon restart or add a tested backend-refresh mechanism in the future; never silently mix controllers on a live config change.
- The parent `BatteryDevice._get_batteries()` currently enumerates BAT-prefixed entries broadly, while the generic device restricts itself to one selected physical system battery. Any legacy refactor needs multi-battery and peripheral safeguards, without dropping dual-battery ThinkPad compatibility.

## UPower system-bus discovery and TLP/drift guard — experimental implementation (2026-10-24)

Temporary branch `tmp/charge-priority-service-conflict-20261024` commit `ae34d4e11125db5a4ac4d41f9802f567ef39c08c` extends the prior 32-test charge-priority resolver. It changes **only the laboratory branch**: new `battery_scripts/probe.py` and `battery_scripts/conflicts.py`, changes `priority.py` and `shared.py`, plus temporary fixtures/workflow.

- `probe_charge_service()`: read-only typed `ChargeServiceProbe(status='absent'|'ready'|'unavailable')`. Checks D-Bus `NameHasOwner`; if no owner, `ListActivatableNames`; absence is confirmed only when neither current owner nor activatable registered name exists on a healthy system bus. An activatable but unowned UPower is *unavailable* and blocked rather than assumed absent. Invalid D-Bus calls, timeouts, missing selected battery and connection errors are *unavailable* (fail closed); no call to `StartServiceByName`. With owner, enumerate and match the actual selected physical battery independently of `Percentage`, `EnergyRate` or `State`; decode charge settings if present. Explicitly unsupported/old API is safe for kernel fallback if other checks succeed. The GUI's existing fast `read_upower_battery()` continues unchanged.
- `resolve_charge_control()` consumes typed probe: `absent` => fallback allowed after other preflight; `unavailable` => blocked; `ready` => existing enabled/unknown/numeric/firmware checks. **No UPower write API was added**.
- `competing_charge_manager()`: `systemctl is-active --quiet tlp.service` with timeout; blocks only positively detected active TLP, without changing other services or treating package installation as proof. Probe execution failure cannot by itself establish TLP ownership; the drift guard still monitors external writes. This is not full discovery of all possible charge tools, only one known conflict.
- `ChargeDriftGuard` holds per-battery last confirmed numeric readback and config signature **in daemon memory only**. If a value changes unexpectedly while the requested config is unchanged, warn once and suspend further automatic writes for the daemon lifetime. A config signature change clears this suspension and reruns safety preflight. Driver rounding (e.g. request 79, effective 80) is tracked by actual readback and is not treated as drift. TLP becomes active => no writes; if it subsequently changes the threshold, drift also prevents automatic restoring. Other tools are never stopped.
- Test scripts use only tempdir power-supply files and shaped D-Bus response objects, not hardware or a running actual UPower 1.91.x. CI: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053704920 **52/52 PASS** (12 original generic tests, 21 priority tests, 11 D-Bus probe tests, 8 conflict tests). Nix Flake https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053704953 PASS. Linux Build https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053704959 (consult final status).
- **Known limitations requiring another review**: live D-Bus races cannot be eliminated by a preflight snapshot alone; only a real service/driver test can assess them. An inactive TLP service is not proof no other tool will change settings later, hence drift guard. The pre-existing multi-write start/end vendor algorithm, daemon cached backend selection, physical Samsung 80% test and full Snap runtime remain unmodified/unverified. The per-battery drift detection relies on valid readable sysfs attributes and is not proof of numeric firmware state on Lenovo conservation-only machines. Do not promote this branch into the second clean PR automatically.

Primary official sources: https://dbus.freedesktop.org/doc/dbus-specification.html ; https://upower.freedesktop.org/docs/Device.html ; https://linrunner.de/tlp/faq/conflicts.html .

