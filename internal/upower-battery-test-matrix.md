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

### Additional read-only battery release checks — compact UI still pending implementation

Previous PASS results for verbose labels and private-bus scenarios do **not** validate the final compact presentation yet.

- R01: UPower 1.90.3 / charge API absent keeps legacy GTK and stats layout; no added supported/firmware row.
- R02: Stop-only enabled UPower at current 55%, observed charging, effective/observed stop 80 → one `Stop threshold: 80% (UPower)`; Charge stays 55%; power is battery flow.
- R03: Reached 80% with active stop 80 → do not infer `Fully charged` or `Not charging`; show actual UPower state.
- R04: Active stop 80 enabled **after** battery reached 100%, still AC connected → current `Charge: 100%`, limit `80% (UPower)` (if verified), no forced discharging assertion or automatic conversion of 100 to 80.
- R05: Numeric UPower support true but enabled=false → kernel stop 100 shown simply as `100%`, plus `Charge limit: Supported (disabled)`; no misleading active 80% preset.
- R06: Firmware-only enabled with current charge 55%, 80%, 100% → `Charge limit: Firmware managed (active)`, no fictitious numeric firmware target.
- R07: Firmware-only supported but enabled=false → `Charge limit: Firmware managed (disabled)`; no GTK editor or enabling affordance.
- R08: Firmware-only supported but enabled unknown → `Firmware managed (state unknown)`, never assume active.
- R09: Start+stop numeric active 75/80 → start 75% and stop 80% inline; no separate `Charge protection` or `Supported controls` row.
- R10: UPower stop configured 80 while kernel shows 100 → both sources on same stop row or wrapped continuation; never claim confirmed application.
- R11: Normal kernel stop 100, no conflict → display `100%`, without redundant `(kernel)` suffix.
- R12: Disabled numeric preset known at 80 → if shown, explicitly marked saved/inactive; observed stop remains primary.
- R13: At 79% within hysteresis start=75/stop=80 with AC connected → respect UPower state if no charge; no fabricated automatic charging.
- R14: Simulated firmware+numeric mask 6 → no unsupported/invented percentage; show only relevant verified numeric and firmware context.
- R15: UPower EnergyRate negative while charging → accepted as finite signed D-Bus rate and displayed as a magnitude, with correct status and no synthetic sysfs fallback; positive discharge, zero, and invalid NaN/inf covered.
- R16: `--stats` and GTK show missing threshold as `Unavailable`, not literal `None`; legacy BatteryInfoBox remains consistent.
- R17: Repeated UPower reports have bounded latency and no spurious daemon writes; real machine previously 20/20 UPower, 5.5 ms median, 122.5 ms max.
- R18: Supported→unsupported service transitions hide extra rows even through `Gtk.show_all()`.
- R19: Battery wattage is labeled as **battery power**, not total CPU/system power; 0.00 W with AC is valid, per issue #974.
- R20: No CPU governor/AC detection change; no modification to sysfs charge limits or UPower `EnableChargeThreshold()` in telemetry.

## Actual GalaxyMint GTK and clean-branch verification

- G01: Real compact GTK screenshot + diagnostic confirmed status Fully charged, charge 99%, AC connected, no start limit, stop 100%, battery flow 0.00 W; optional unsupported `Charge limit` field hidden. PASS.
- G02: Fan speed appeared at 3000 RPM in the screenshot. Earlier 0 RPM was attributable to Fn+F11 low-power selection, not an established reporting defect. CLOSED; do not reintroduce into PR.
- G03: Clean feature branch created from upstream-synced `master` @ `5a168303...` with exactly ten production/packaging/README changes. PASS.
- G04: Internal test suites, mock private bus, test-only workflows and planning documentation excluded from clean branch. PASS.
- G05: No PR created, no master merge, no threshold-writing feature added. PASS.
- G06: Snap full confinement runtime and physically upgraded UPower still untested; do not label either as PASS based on mock or Nix results.

## Experimental compact implementation CI verification

Temporary implementation: `tmp/upower-compact-validation-20261023` @ `35de5dad73dfd37b44c3deab07bb8785bba1d65d`.

- Python 3.9/3.12 tests: PASS (run 40046018302).
- Isolated pip installation plus GTK on Ubuntu Xvfb: PASS (run 40046018280).
- Private D-Bus with `EnumerateDevices`/`GetAll` and GTK/Stats: PASS for all nine scenarios (run 40046018274): `end-only`, `start-end`, `firmware`, `legacy`, `charging-55`, `reached-80`, `full-100`, `disabled`, `firmware-disabled`. These mock scenarios test the external D-Bus behavior; no actual newer UPower service/hardware threshold writing was used.
- Linux Build: PASS (run 40046018291).
- Nix Flake: PASS (run 40046018276).
- GTK long-source-conflict text wrapping and `EnergyRate` negative-to-magnitude normalization covered.
- Remaining: physical GTK test for new compact layout; Snap confinement E2E not exercised; final production-only diff audit.

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

## End-to-end full GTK + isolated UPower service

- FG01: Full GTK retains Controls, Governor Override, Turbo Override, config label and menu; not just SystemReportView. PASS (run 40049136671).
- FG02: Physical system daemon remains unmodified and is detected normally in user's full GTK experiment. PASS by user's terminal output and screenshot.
- FG03: Simulated 55% Charging, -12.50 W UPower EnergyRate, stop 80% confirmed in mocked sysfs readback: PASS.
- FG04: Simulated 80% Pending charge with 80% stop, 100% Fully charged with 80% stop: PASS. Does not infer discharge or charging from threshold alone.
- FG05: Simulated start+end control, firmware-only enabled/disabled, numerical control disabled, old no-capability API: PASS.
- FG06: Private D-Bus uses client-side DBUS_SYSTEM_BUS_ADDRESS override only; mock threshold is Python-only read interception and privileged GUI actions are rejected. PASS by fixture design; no actual battery threshold write.
- FG07: Full GTK real updated daemon UPower 1.91.x and confined Snap deployment: NOT TESTED; do not conflate with mock success.

## Real GalaxyMint complete GTK / modern UPower simulation screenshots

- VG01: 55% charging while 80% stop active — `Charging power: 12.50 W` and correct inline stop, with no control duplication. PASS (user screenshot).
- VG02: 80% Pending charge / 80% stop — no inferred charging, 0.00 W. PASS.
- VG03: 100% Fully charged / new 80% stop — no simulated forced discharge or incorrect percentage substitution. PASS.
- VG04: 99% Fully charged / 75% start and 80% stop — fields rendered correctly as independent settings; simulated non-steady-state battery percentage is not itself a regression. PASS.
- VG05: Firmware-only active at 99% — numeric start/stop rows hidden; `Charge limit: Firmware managed (active)` visible. PASS.
- VG06: Full Controls, real system info, real daemon recognized; private lab intercepts privileged GUI actions and never changes sysfs thresholds. PASS.
- VG07: Nonfatal `org.freedesktop.systemd1` warnings attributable to test bus redirection; do not modify CPU or daemon production code to work around laboratory warning. RECORDED.
- VG08: Cosmetic item — in firmware-only mode, consider displaying Charge limit *before* Battery power, matching numeric threshold order. OPTIONAL, not a blocker.
- VG09: Actual upgraded UPower daemon and snap jail-mode runtime: NOT VERIFIED.


## Optional kernel stop-only controller: temporary CI results

- K01: If `enable_thresholds` unset or false, not a single write even with stop configured. PASS.
- K02: `stop_threshold = 80` applied to a simulated `BAT1/charge_control_end_threshold` with no start attribute. PASS.
- K03: Out-of-range, malformed or noninteger values rejected (0, 101, negative, float, NaN, missing). PASS.
- K04: Explicit start setting when start attribute missing rejected; never fabricates a start percentage. PASS.
- K05: Supported and active UPower protection prevents direct sysfs control conflict. PASS.
- K06: Kernel rounded 79 to 80; readback reported, cached for periodic idempotence; changed external state detected. PASS.
- K07: Already-correct 80 read without rewriting. PASS.
- K08: Driver reports successful write but readback unchanged at 100: reject, don't claim new setting effective. PASS.
- K09: Peripheral and non-power_supply paths refused; existing vendor drivers selected first. PASS.
- K10: Linux Build and Nix Flake passed in temporary branch. Full Python 3.9 runtime not verified due pre-existing type annotation incompatibility.



## Charge backend precedence audit — observed vs intended (2026-10-24)

Branch `tmp/charge-priority-audit-20261024` commit `f4ad47b84f171e3574699171b68fc8a13e83069a` adds ten tests only and one temporary workflow on top of virtual-sysfs suite. Run: https://github.com/lucasleocs/auto-cpufreq/actions/runs/40050734230. 12 existing + 10 new PASS. Linux Build and Nix Flake also pass for same temporary branch.

Observed (passing assertions): no UPower -> kernel; UPower explicitly unsupported -> kernel; UPower supported but disabled -> **still kernel**; UPower supported with enabled unknown -> **still kernel**; UPower active and matching target -> operation refused even when already satisfied; firmware-only active -> refuses kernel; UPower supported without any kernel end attribute -> no generic controller; vendor module chosen before generic, without cross-backend UPower conflict check; exactly one selected system battery; failed kernel write does not retry an alternative backend.

**Findings to fix before second contribution:**
- P-A: Treat unknown UPower active state as ambiguous/unsafe, not permission to write.
- P-B: Distinguish UPower unreachable from explicitly lacking charge-control API; do not silently race an active controller after a transient outage.
- P-C: Recognize already-satisfied matching UPower end limit + kernel readback, instead of false conflict warning.
- P-D: Apply shared conflict/ownership preflight to existing manufacturer paths, not just GenericKernelBatteryDevice.
- P-E: Only propose UPower as a writer for already-configured numeric/firmware presets that can be activated via the documented toggle, and never as an arbitrary-percent setter.
- P-F: Add desired-policy tests for UPower status toggling, same/mismatched preset, firmware-only, unsupported, unknown/timeout, kernel rounding and multi-battery ambiguity. Existing tests describe present behavior and do not prove these corrections yet.
- P-G: Confirm `enable_thresholds` config and daemon workflow do not attempt writes when disabled; no physical Samsung testing until explicit authorization.



## Centralized backend arbitration — implementation validation

- AR01: User opted out: no UPower preflight or sysfs writing. PASS.
- AR02: Healthy UPower old/no charging control + BAT1 standard stop: apply 80%. PASS (mock filesystem).
- AR03: UPower explicitly disabled + configured numeric stop: kernel apply permitted. PASS.
- AR04: UPower missing/timeout/unhealthy: **blocked** (rather than confusing with "unsupported"). PASS.
- AR05: UPower charge-control enabled state absent/unknown: **blocked**. PASS.
- AR06: UPower active 80 with kernel 100: **blocked**, no silent replacement. PASS.
- AR07: UPower active 80 with kernel 80: **satisfied**, no write or false conflict warning. PASS.
- AR08: UPower active but unknown kernel readback: **blocked**. PASS.
- AR09: Firmware-only active and numeric request: **blocked**. PASS.
- AR10: Firmware disabled with numeric target: sysfs apply permitted. PASS.
- AR11: Requested start+stop require start/end configured and observed agreement to consider `satisfied`. PASS.
- AR12: Conflicting UPower preset while kernel coincident: **blocked**. PASS.
- AR13: Standard kernel attribute preferred over Asus legacy module; vendor fallback when standard attribute missing. PASS.
- AR14: Explicit IdeaPad conservation chooses the correct specialized writer (also with both ideapad modules loaded). PASS at latest revision (re-run pending).
- AR15: If an actual UPower owner exists, legacy vendor application is blocked via shared daemon preflight. PASS.
- AR16: Firmware operation with UPower already active is not considered satisfied merely by coincident numeric thresholds. PASS.
- AR17: No supported writer and a failed kernel write cause no automatic fallback to another backend. PASS.
- AR18: A genuine newer UPower daemon/firmware transaction, absence of UPower service vs timeout, conflict with TLP, existing vendor start/end partial writes and physical Samsung charge effects remain **unverified**.



## Official-doc remediation acceptance criteria — researched, not yet implemented

- DOC01: healthy system D-Bus + UPower name absent **and non-activatable** → classify service absent, allow an explicitly opted-in, validated kernel backend after manager conflict preflight.
- DOC02: UPower no owner but name *activatable*, server crash/timeout/permission error → classify transient or ambiguous, no write; verify later. NameHasOwner=false alone is insufficient.
- DOC03: UPower healthy but old API / threshold explicitly unsupported → sysfs opt-in fallback, without assuming any hidden configured threshold.
- DOC04: UPower valid telemetry but missing/invalid threshold property → no inference from overall battery snapshot; typed capability result reflects unknown safely. Bad telemetry alone must not collapse all capability discovery to `None`.
- DOC05: UPower enabled and kernel matches target → already satisfied, no write, no false warning.
- DOC06: UPower enabled but configured preset conflicts → do not call UPower toggle or overwrite threshold; show conflict. UPower method cannot select an arbitrary 80%.
- DOC07: TLP package merely installed but service inactive and no battery control configured → no false active-manager claim. Active configured charge manager → log and suspend writes rather than stop/uninstall services.
- DOC08: After successful kernel write 80/readback 80, external app changes to 90 before next daemon poll → detect drift, suspend automatic rewrites, warn once. Explicit user configuration change requires safe reevaluation.
- DOC09: Rounding 79→80 is displayed as actual=80; do not silently count as exact 79 or retry each hour. Exercise restart/re-read.
- DOC10: On standard start+end old S=80 E=90 to desired 50/70 → write start first then end, verifying after each.
- DOC11: Old S=60 E=80 to desired 90/95 → write end first then start, verifying after each.
- DOC12: Same valid old/new pair → zero writes (the legacy method currently writes 100 anyway).
- DOC13: Simulate second write rejection or readback mismatch → bounded safe restoration attempt, clear partial-failure result, never promise atomicity.
- DOC14: End-only Samsung BAT1 requires no fake start threshold, no direct Samsung ACPI actions, 80% valid and 100% off, but *actual hardware changes require separate approval*.
- DOC15: Multi-battery selected identity, manufacturer compatibility fallback, missing standard attributes, unavailable permissions and bus state racing across calls → no cross-device write or risky automatic retry.
- DOC16: Strictly no config opt-in → no UPower arbitration/manager probe on write path, no write, no runtime policy change.

These are **requirements for future tests**, not PASS statuses. Current 32/32 offline priority tests at commit `82b4d515...` validate prior behavior and do not cover DOC01-DOC16 yet.

- DOC17: Disabling auto-cpufreq threshold control after a persisted hardware value of 80% causes **no write** and does not misleadingly claim hardware returned to 100%; an explicit restore operation is separate and tested.
- DOC18: Config changes that require switching backend (generic stop threshold ↔ Lenovo firmware conservation) do not use a stale cached backend; require controlled reload/restart or a tested new selection on config change.
- DOC19: Vendor fallback preserves intentional dual laptop batteries but never blindly touches BAT-prefixed peripheral or arbitrary filesystem paths.


## UPower D-Bus service discovery and competing-manager checks — tests

Temporary head `ae34d4e11125db5a4ac4d41f9802f567ef39c08c`; CI https://github.com/lucasleocs/auto-cpufreq/actions/runs/40053704920: **52/52 PASS**. All writes used temporary fixture files only.

- SC01: Healthy D-Bus: no `NameHasOwner` and no activatable service -> UPower absent; guarded sysfs fallback permitted. PASS.
- SC02: No owner but service is activatable -> unavailable, no sysfs writing. PASS.
- SC03: NameHasOwner true / valid physical BAT1 / charge fields -> ready, enabled/end capabilities. PASS.
- SC04: Older healthy UPower without ChargeThreshold fields -> ready but explicit unsupported; guarded fallback. PASS.
- SC05: Error from D-Bus owner query or race during EnumerateDevices -> unavailable; fail closed. PASS.
- SC06: UPower battery identity mismatch -> unavailable, not silent selection of another device. PASS.
- SC07: Capability probe succeeds without Percentage/EnergyRate/State telemetry fields. PASS.
- SC08: Supported but enabled unknown -> distinguish and block. PASS.
- SC09: TLP service positively active -> block without writing; inactive package does not block. PASS.
- SC10: TLP service probe executable unavailable -> no positive active claim; drift guard remains in place. PASS.
- SC11: Verified own 80 write followed by external 90 -> skip repeated reapplication; warn and suspend. PASS.
- SC12: New configuration signature clears previous suspension; rerun preflight. PASS.
- SC13: Driver rounds 79 to 80 -> unchanged observed value is not external drift. PASS.
- SC14: Opt-out config disables all checks and writes. PASS.
- SC15: A live UPower service name change occurring in between probe and actual write remains possible; this is a race, **not tested away**.
- SC16: Non-TLP managers, unknown writer ownership and old multi-write vendor atomicity remain for later work. NOT VERIFIED.



## Legacy start/end safe transaction — CI fixture results

Branch `tmp/charge-threshold-safe-transaction-20261024` @ `87cc1f5c956b66545cba621634b6ddeffcce64ce`. CI https://github.com/lucasleocs/auto-cpufreq/actions/runs/40054607440: **22 new transaction PASS, total 74 PASS**. Linux Build and Nix Flake also PASS.

- TX01: lower from 80/90 to 50/70 -> start then stop, never intermediate 100. PASS.
- TX02: raise from 60/80 to 90/95 -> stop then start. PASS.
- TX03: start-only or stop-only changes -> exactly one write. PASS.
- TX04: both already match -> zero writes. PASS.
- TX05: missing/invalid current readback or invalid target -> zero writes. PASS.
- TX06: first write rejected -> failed, no unnecessary restoration. PASS.
- TX07: second write rejected, start-first -> restore original start; failed outcome. PASS.
- TX08: second write rejected, stop-first -> restore original stop; failed outcome. PASS.
- TX09: kernel write reports success but does not change attribute (first or second) -> reject and restore modified first attr when safe. PASS.
- TX10: restoration itself rejected -> report PARTIAL with last observed values and no false success. PASS.
- TX11: coherent rounding on start or stop -> verified effective pair; warn about changed actual value. PASS.
- TX12: start rounding would break future planned stop -> abort and safely restore. PASS.
- TX13: rounded start makes no progress -> reject without second write. PASS.
- TX14: invalid/unknown readback after a write -> report unknown partial, no blind rollback. PASS.
- TX15: other attribute changes unexpectedly or concurrent external writer changes pair between steps -> stop, do not fight/restore over possible external owner. PASS.
- TX16: setting stop to literal 100% when explicitly requested is allowed and uses a single write; it is no longer an intermediate workaround. PASS.
- TX17: genuine hardware/firmware async behavior, vendor-specific minimum gaps and cross-file atomicity remain unverified. NOT TESTED.

