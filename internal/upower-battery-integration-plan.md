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

