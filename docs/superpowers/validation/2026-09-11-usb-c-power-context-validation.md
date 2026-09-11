# USB-C and USB-PD Power Context Validation

Date: 2026-09-11

Validated production branch: `usb-c-power-context`

Validated production HEAD: `19250e70414896628e8a72f7073773a0fbcd32a4`

Validated production tree: `87d4f34ec4eda64d4fbbad5943b4b59430b0a08d`

Validation evidence branch: `usb-c-power-context-validation`

## Hardware

- Samsung Galaxy Book 4 NP750XGJ-KG5BR
- Intel Core i3-1315U
- Linux Mint 22.3
- Kernel `7.0.0-31-generic`

## Implementation topology and scope

The validated production HEAD is exactly five implementation commits ahead of `usb-c-power-context-design` (`51fbf364df7364f577fe0e9dc4a8f747041192e2`):

1. `99b3471` — `refactor: share generic sysfs readers`
2. `1eaf279` — `feat: discover system power context`
3. `caf41b2` — `feat: discover USB Type-C context`
4. `c714679` — `feat: discover USB Power Delivery capabilities`
5. `19250e7` — `feat: report power context diagnostics`

Production changes are limited to the approved reporting/discovery files:

- `auto_cpufreq/modules/sysfs.py`
- `auto_cpufreq/modules/intel_power.py`
- `auto_cpufreq/modules/power_context.py`
- `auto_cpufreq/modules/system_info.py`
- `auto_cpufreq/modules/system_monitor.py`
- `auto_cpufreq/bin/auto_cpufreq.py`

No policy, daemon-scheduler, RAPL-controller, service, configuration, installer, or power-helper production file is changed by this stage.

## Automated verification

Final synthetic Task 6 gate:

- Run: `34656108133`
- Workflow: `Temporary USB-C Power Context Task 6 Final Gate`
- Result: `success`

Existing repository CI was also run against commit `db460010057e41b9764cc1fbd2c26bc42c986293`, whose tree is exactly `87d4f34ec4eda64d4fbbad5943b4b59430b0a08d`, byte-identical to validated production HEAD `19250e70414896628e8a72f7073773a0fbcd32a4`:

- Linux Build run `34656364834`: `success`
- Nix Flake run `34656364837`: `success`

The temporary test workflow and CI-trigger-only commits are not part of the production implementation branch.

## Hardware ABI presence

Observed `power_supply` objects:

- `ADP1`
- `BAT1`
- `ucsi-source-psy-USBC000:001`
- `ucsi-source-psy-USBC000:002`

Observed Type-C objects:

- `port0`
- `port1`
- `port1-partner` while the charger was connected

Observed USB Power Delivery objects:

- `pd0`
- `pd1`

`/sys/class/power_supply`, `/sys/class/typec`, and `/sys/class/usb_power_delivery` were all present on this machine.

Both USB-PD objects exposed `revision=3.0`; neither exposed `version`, `source-capabilities`, or `sink-capabilities` on this kernel/hardware combination.

## Connected raw sysfs versus snapshot

With the USB-C charger connected, raw sysfs reported:

- `ADP1`: `type=Mains`, `online=1`
- `BAT1`: system battery, initially `status=Not charging`, `capacity=100`
- `ucsi-source-psy-USBC000:001`: `scope=Device`, `online=0`, active USB type `C`
- `ucsi-source-psy-USBC000:002`: `scope=Device`, `online=1`, active USB type `PD`, `current_now=2250000`
- `port0`: `power_role=[sink]`, `power_operation_mode=default`, no partner
- `port1`: `power_role=[sink]`, `power_operation_mode=usb_power_delivery`, partner present
- `port1-partner/supports_usb_power_delivery=no`
- `pd0` and `pd1`: revision `3.0`, no capability groups exposed

`PowerContextDiscovery().discover()` agreed with those observations:

- `power_supply_status=AVAILABLE`
- `typec_status=AVAILABLE`
- `usb_pd_status=AVAILABLE`
- `external_power=ONLINE`
- `battery_flow=NOT_CHARGING`
- only `BAT1` was included in the system `batteries` aggregate
- both UCSI supplies remained observable in `power_supplies` but were excluded from the battery aggregate because they have `scope=Device`
- active bracketed USB types were parsed as `C` and `PD`
- `port1` preserved the kernel's independent facts `power_operation_mode=usb_power_delivery` and `partner_usb_pd_capable=False` without cross-subsystem inference
- `pd0`/`pd1` were retained with empty source/sink capability tuples rather than inventing PDOs

The observed `BAT1/voltage_now` changed slightly between sequential reads, as expected for instantaneous telemetry; structural fields and aggregate state agreed.

## `--debug` reporting gate

The validated checkout was executed directly with:

```bash
sudo env PYTHONPATH="$PWD" python3 -m auto_cpufreq.bin.auto_cpufreq --debug
```

The `Power Context Diagnostics` section agreed with raw sysfs and the direct snapshot:

- `External power: Online`
- `Battery flow: Not charging`
- all four power-supply objects were shown
- `17098000` microvolts was formatted as `17.098 V`
- `2250000` microamps was formatted as `2.250 A`
- the UCSI USB types were shown as `C` and `PD`
- both Type-C ports and the `port1` partner state were reported
- `pd0` and `pd1` were reported with revision `3.0` and `None exposed` capabilities
- missing `power_now` remained `Unavailable`
- no negotiated charger wattage was synthesized from voltage/current or advertised capability data

The checkout-only invocation also printed `auto-cpufreq version: IndexError('list index out of range')`. This is unrelated to PowerContext discovery/reporting and was not treated as a Stage-5 regression because the PowerContext output itself completed correctly and the issue appears in version resolution when executing this source checkout directly rather than the installed package.

## Disconnect and reconnect gate

After physically disconnecting the charger, raw sysfs reported:

- `ADP1 online=0`
- both UCSI sources `online=0`
- `BAT1 status=Discharging`
- `port1-partner` absent

The direct snapshot correctly changed to:

- `external_power=OFFLINE`
- `battery_flow=DISCHARGING`
- `port1.partner_present=False`

`pd0` and `pd1` remained present with revision `3.0` and no exposed capability groups; the implementation preserved this instead of assuming they must disappear with the Type-C partner.

After reconnecting the same charger, raw sysfs reported:

- `ADP1 online=1`
- `BAT1 status=Charging`, `capacity=96`
- `ucsi-source-psy-USBC000:002 online=1`, active USB type `PD`
- `port1-partner` present again

The direct snapshot correctly changed to:

- `external_power=ONLINE`
- `battery_flow=CHARGING`
- `port1.partner_present=True`

This validates factual transition handling without a time debounce, active-source election, or cross-subsystem identity inference.

## No-write verification

Before PowerContext hardware validation, the following control state was captured with the charger connected:

- `intel_pstate/no_turbo=0`
- `intel_pstate/hwp_dynamic_boost=1`
- policies `0-3`: governor `powersave`, EPP `balance_performance`, min `400000`, max `4500000`
- policies `4-7`: governor `powersave`, EPP `balance_performance`, min `400000`, max `3300000`

After connected discovery, raw-sysfs comparison, direct snapshot, `--debug`, physical disconnect, physical reconnect, and final discovery, all of those values were re-read and were identical to the baseline.

Static inspection also confirms the new discovery path is read-only:

- `auto_cpufreq/modules/sysfs.py` exposes only read helpers based on `Path.read_text()` plus parsing
- `auto_cpufreq/modules/power_context.py` enumerates and reads sysfs; it contains no hardware write path
- the implementation diff does not modify `policy.py`, `daemon_scheduler.py`, `intel_rapl.py`, or `power_helper.py`

### RAPL baseline note

The planned baseline command used `find /sys/class/powercap -name 'constraint_*_power_limit_uw'` without following class symlinks. On this host that produced an empty `=== RAPL limits ===` section even though Intel RAPL zones are present and were visible in the existing Intel debug report. Therefore the before/after file did not directly compare RAPL constraint files on this machine.

This is recorded as a validation-procedure limitation rather than hidden as a pass. It does not expose a PowerContext write path: the new modules are read-only, the reporting integration has no control linkage, and the production diff does not touch the existing RAPL controller or policy paths.

## Completion criteria

Validated by synthetic tests and/or real hardware as applicable:

- local attribute error degradation without aborting the subsystem
- independent availability of `power_supply`, Type-C, and USB-PD
- preservation of multiple power supplies and batteries
- battery `MIXED` support in synthetic coverage
- Device-scope exclusion from system battery aggregation
- advertised-capability versus current-power separation
- no heuristic cross-subsystem correlation
- unknown/malformed PDO tolerance
- detailed `--debug` PowerContext reporting
- compact STATS reporting in synthetic coverage
- shared sysfs-reader refactor without Intel semantic change
- unchanged daemon and policy paths
- no PowerContext hardware-write primitive
- real-hardware agreement for every ABI field exposed by the Samsung during the connected/disconnected/reconnected gate

## Result

The USB-C / USB-PD Power Context implementation at production SHA `19250e70414896628e8a72f7073773a0fbcd32a4` passes the final synthetic, CI, and Samsung read-only hardware validation gates, with the RAPL before/after command limitation documented above. The validated production code remains observational only and does not introduce policy or hardware-control behavior.
