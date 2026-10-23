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

## Real hardware inspection — no writes

Reference host: GalaxyMint (Samsung BAT1, UPower daemon 1.90.3).

Known baseline:
- sysfs BAT1 reported `Not charging` and sometimes `Discharging` near 99%.
- UPower reported `fully-charged`, `energy-rate: 0 W`, `percentage: 99%`.
- `ADP1 online: 1` and `ucsi-source-psy-USBC000:002 online: 1`.
- `BAT1/charge_control_end_threshold` exists and read `100`.
- UPower 1.90.3 did not expose charge threshold D-Bus properties in introspection.

Validate provider selection with `NativePath`, compare snapshot `UpdateTime`, run GTK and `--stats` while near-full and while genuinely discharging away from AC, and observe behavior after suspend/resume. Do not use sudo, edit charge thresholds, invoke EnableChargeThreshold, or install services as part of this plan.

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
