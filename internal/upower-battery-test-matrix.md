# Battery UPower integration — temporary validation matrix

Baseline: `5a1683037a583785f802b5d039022127b9b154cd`
Purpose: test planning and fixtures for an eventual UPower-first reporting contribution.
Disposition: temporary branch only. No production changes or battery writes are authorized by this file.

## Unit test matrix

| ID | Fixture / condition | Expected behavior |
|---|---|---|
| U01 | UPower absent, sysfs BAT1 present | Fallback sysfs; GTK and stats still render |
| U02 | UPower installed but service unreachable | Bounded failure and fallback, no CLI hang |
| U03 | D-Bus permission denied | Bounded failure and fallback |
| U04 | UPower device is mouse/headset/UPS | Do not select it as system battery |
| U05 | Multi-battery system BAT0/BAT1 | Match configured/selected sysfs battery by NativePath |
| U06 | NativePath missing or no physical match | Fallback without selecting aggregate DisplayDevice |
| U07 | UPower charging, valid rate | Charging label, correct watts and percent |
| U08 | UPower fully charged, 99%, 0 W, AC online | Fully charged; 0 W preserved |
| U09 | UPower pending charge/pending discharge | Exact states displayed in both views |
| U10 | UPower invalid state or percentage | Reject invalid snapshot; fallback |
| U11 | EnergyRate NaN/infinite, negative/positive | Validate and normalize according to documented units, not AC status |
| U12 | UPower has no EnergyRate | Reject partial invalid presentation snapshot or implement documented all-sysfs fallback |
| U13 | UPower UpdateTime old | Verify conservative stale policy; no short arbitrary 30-s cutoff |
| U14 | UPower service restarts between requests | Retry policy bounded, no permanent disablement |
| U15 | UPower threshold API missing (1.90.3 example) | No extra capability block; legacy fields remain |
| U16 | ChargeThresholdSupported=false | No extra capability block |
| U17 | ChargeThresholdSupported=true, start/end values | Report configured limits; read-only |
| U18 | ChargeThresholdSettingsSupported=1 | Start supported, not end |
| U19 | ChargeThresholdSettingsSupported=2 | End supported, not start |
| U20 | ChargeThresholdSettingsSupported=4 | Firmware-managed behavior, not assumed selectable level |
| U21 | ChargeThresholdSettingsSupported=3 or 6 | Correct bitmask decomposition |
| U22 | ChargeThresholdSettingsSupported absent | Unsupported metadata / no false claim |
| U23 | ChargeStartThreshold or ChargeEndThreshold == 4294967295 | Unavailable, not 4294967295% |
| U24 | UPower and sysfs threshold values disagree | No assertion of effective applied threshold |
| U25 | Kernel reports Discharging while AC is connected, UPower absent | Display discharge faithfully without changing CPU policy |
| U26 | current_now=0, voltage_now valid, power_now absent | Valid sysfs fallback power 0 W |
| U27 | power_now=0 | Preserve 0 W as valid |
| U28 | battery_device explicitly configured | Respect selection in UPower mapping |
| U29 | Empty battery list on desktop | No exception or spurious threshold controls |
| U30 | Existing enable_thresholds=true config | New read-only feature never writes or reapplies limits |
| U31 | Missing attributes on old kernels | No crash, legacy status/threshold behavior |
| U32 | GUI and terminal refresh repeatedly | Bounded latency; no unbounded subprocess/thread growth |
| U33 | pyproject installation without GTK optional extra | CLI works and fallback does not import gi |
| U34 | UPower absent on non-systemd init | No assumptions about systemd or D-Bus availability |

## Applet-derived read-only checks

These add to U01–U34 and are scoped to the first reporting PR:

| ID | Condition | Expected behavior |
|---|---|---|
| A01 | Physical BAT1 and aggregate DisplayDevice present | Match BAT1 through NativePath, never aggregate silently |
| A02 | UPower threshold property missing while telemetry is valid | Status and rate still use UPower; no extra threshold capability block |
| A03 | UPower threshold property explicitly false | Distinguish unsupported from unavailable API |
| A04 | Start-only, end-only, and firmware-managed flags | Correct read-only capability descriptions, without invented percentages |
| A05 | UPower service owner changes during refresh | Discard stale response; bounded fallback |
| A06 | Multiple physical batteries with different threshold metadata | No fictitious common configured threshold |
| A07 | Charge control support=true but threshold=G_MAXUINT | Show configured threshold as unavailable |
| A08 | `ChargeThresholdSettingsSupported` unknown mask bits | Ignore unknown capability bits, avoid false availability claims |

## Separate future configuration/write-feature test design (not part of this reporting PR)

The following scenarios describe a *future* opt-in declarative config feature. They must **not** be executed against a real battery during reporting implementation.

| ID | Scenario | Expected future behavior |
|---|---|---|
| W01 | Existing `enable_thresholds=true`, start=75, stop=80 | Backward-compatible interpretation; no silent change in ownership |
| W02 | Stop-only standard kernel attribute, config requests stop=80 | No invented start threshold; only intentional end attribute written |
| W03 | Start/end hardware, config omits start | Explicit supported policy or descriptive refusal, never silent guessing |
| W04 | Legacy vendor-specific conservation mode | Unchanged behavior; not overridden by generic backend |
| W05 | `enable_thresholds=false` with stop=80 present | No writes |
| W06 | Absent/read-only attribute | Feature unavailable or clear failure; no write attempt |
| W07 | Invalid 0–100 input or start>=stop | Reject before attempting any write |
| W08 | Driver rounds requested 75 to 80 | Readback shows actual 80, requested 75 not falsely confirmed |
| W09 | Driver rejects stop=80 | Fail without retrying values or alternate uncontrolled provider |
| W10 | UPower charge-threshold mode enabled and managing same device | Detect potential competing owner; no silent direct sysfs write |
| W11 | UPower unavailable but sysfs standard attribute exists | Future sysfs support independent of UPower version, under approved control policy |
| W12 | Two batteries, only one selected | Target only selected physical battery |
| W13 | Privilege denied | No partial configuration claimed as successful |
| W14 | Failure after one write in paired operation | Documented safe partial-write recovery and original-value reporting |
| W15 | Hourly existing threshold daemon active | No duplicate writer or racing reapplications |
| W16 | Interrupted installation, restart, suspend/resume | No automatic reconfiguration outside established opt-in policy |
| W17 | Battery at 99%, requested stop=80 | Do not claim immediate discharge to 80% |
| W18 | GTK/terminal query with writable hardware | No sysfs writes, no UPower mutating method calls |
| W19 | UPower settings read-only and requested custom 80 | Never call `EnableChargeThreshold` as if it sets a numeric value |
| W20 | End limit actual value differs from config due to rounding | Show requested and actual separately, or explicit failure state |

## Real hardware inspection — no writes

Reference host: GalaxyMint (Samsung BAT1, UPower daemon 1.90.3).

Known baseline:
- sysfs BAT1 reported `Not charging` and sometimes `Discharging` near 99%.
- UPower reported `fully-charged`, `energy-rate: 0 W`, `percentage: 99%`.
- `ADP1 online: 1` and `ucsi-source-psy-USBC000:002 online: 1`.
- `BAT1/charge_control_end_threshold` exists and read `100`.
- UPower 1.90.3 did not expose charge threshold D-Bus properties in introspection.

Validate provider selection with `NativePath`, compare snapshot `UpdateTime`, run GTK and `--stats` while near-full and while genuinely discharging away from AC, and observe behavior after suspend/resume. Do not use sudo, edit charge thresholds, invoke EnableChargeThreshold, or install services as part of this plan.

## Verified private-D-Bus end-to-end frontend test

Source revision: `dba4fe0b21ea4ac63c892ff7fd441e2efe383853` (temporary branch `tmp/upower-modern-privatebus-20261023`).
Run: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40042055797 (PASS).

- P01: Synthetic modern UPower with end-only mask=2 and configured Stop=80 displayed in `--stats` and GTK. PASS
- P02: Synthetic start+end mask=3 and configured 75–80 displayed in both. PASS
- P03: Firmware-only mask=4 with unavailable percentages, no invented percentage. PASS
- P04: Legacy API without charge properties, no extra rows in GTK/stats. PASS
- P05: Private D-Bus `EnumerateDevices` / `GetAll` traversed through the real Python provider, not direct `BatteryInfo` injection. PASS
- P06: No service restart, config edit, privileged helper, threshold write or system bus override outside the private child process. Verified by test harness design; not a runtime observation of every host-side service.
- P07: Presentational mismatch `None` vs `Unavailable` for the start threshold remains open.
- P08: Actual newer UPower daemon/udev detection on physical Samsung was **not** tested. Do not conflate the mock with a real upgraded UPower service.

## Real GTK comparison and resolved fan-speed observation

Observed on GalaxyMint: UPower gave 99%, Fully charged, 0.00 W; baseline GTK gave Not charging, 99%, 0.00 W; kernel stop remained 100. Optional charge capability rows were hidden on the installed UPower 1.90.3. Repeated UPower readings: 20/20, median 5.5 ms, peak 122.5 ms.

The user subsequently traced 0 RPM to Fn+F11 changing the platform profile from `performance` to `low-power`; other profiles reported RPM. **Close and remove the fan-speed bug investigation/correction work item**. No fan change should be bundled with the UPower PR; reconsider only on new contradicting evidence. The earlier high temperature snapshots likewise do not establish a UPower-related regression.

### Additional read-only battery release checks

These tests refer to the **revised compact UI design**, which is still pending implementation. Earlier PASS results for the old verbose UI must not be treated as validation of this revised layout.

- R01: Legacy UPower/unknown API renders original GTK rows, with no charge-protection or supported-controls decoration.
- R02: Consistent, enabled UPower end-only 80% and observed kernel 80% renders one `Stop threshold: 80% (UPower)` row, no redundant separate `Charge protection` or `Supported controls` rows; terminal stats conveys the same information.
- R03: Consistent start/end 75/80 shows both values adjacent to corresponding threshold labels, not grouped into a third duplicate row.
- R04: Configured UPower stop=80 and kernel=100 retains both labeled values on the same threshold row or wrapped continuation, and never reports the configured value as confirmed applied.
- R05: UPower threshold property present but protection disabled does not imply 80% is active; if the preset is displayed, clearly mark it inactive.
- R06: Firmware-only mask=4 has one `Charge limit: Firmware managed (UPower)` description and no invented percentages or numeric end-threshold claim.
- R07: `Charge` remains actual battery level; do not misidentify it as the stopping threshold when e.g. Charge=99% and configured stop=80%.
- R08: After supported→unsupported transitions, optional labels disappear and don't reappear via Gtk.show_all().
- R09: Terminal stats uses `Unavailable` for unknown start/stop thresholds, not the literal `None`.
- R10: Repeated UPower queries do not cause a hung UI or persistent orphaned tasks. Real machine 20/20 UPower, 5.5 ms median and 122.5 ms max observed.
- R11: A failed UPower read falls back to sysfs and does not change CPU policy, AC interpretation or battery threshold writer.

## Build and CI validation

- Targeted data-model tests, with injected D-Bus and sysfs fakes.
- Existing Linux build and Nix Flake CI.
- CLI `--stats` in headless environment with and without UPower D-Bus.
- GUI and legacy BatteryInfoBox refresh smoke test.
- Source install, Snap and Nix dependency review before a D-Bus library is selected.
- Diff audit: only approved source, README, and packaging changes in the final branch; no temporary fixtures, internal docs, experimental workflows.

## Exit gates

Gate A: D-Bus client/packaging spike accepted.
Gate B: fallback unchanged under UPower absent/broken.
Gate C: selected-battery mapping and data semantics correct.
Gate D: GTK and stats matched.
Gate E: no write paths, no CPU policy changes, no vendor-specific code.
Gate F: temporary artifacts excluded from final contribution.

Sources: https://upower.freedesktop.org/docs/Device.html ; https://upower.freedesktop.org/docs/UPower.html ; https://docs.kernel.org/power/power_supply_class.html
