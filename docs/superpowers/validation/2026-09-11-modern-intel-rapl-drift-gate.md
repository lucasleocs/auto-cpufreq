# Modern Intel RAPL Hardware Drift Gate

Date: 2026-09-11

Branch: `modern-intel-rapl-envelopes`

Validated production code: `941ee08604dcfbb02d1623cc6873bfe6ce9a95e9`

Hardware:
- Samsung Galaxy Book 4 NP750XGJ-KG5BR
- Intel Core i3-1315U (Raptor Lake-U)
- Linux Mint 22.3
- Kernel 7.0.0-31-generic

This gate follows the four isolated PL1/PL2 hardware writes documented in `2026-09-11-modern-intel-rapl-hardware-validation.md`.

## Purpose

Prove on real hardware that an external change while auto-cpufreq owns a RAPL constraint invalidates ownership without causing a write war. The controller must not restore the old external baseline and must not reassert its previous value when `current != last_written_by_us`.

## Test

Constraint:
- control type: `intel-rapl-mmio`
- zone: `package-0`
- constraint: `long_term`
- external baseline: 15.000 W
- auto-cpufreq target: 14.500 W
- simulated external drift: 14.250 W

Initial acquisition result:

```text
action=acquired
before=15.000 W
requested=14.500 W
effective=14.500 W
```

Persisted ownership recorded:

```text
original_power_limit_uw=15000000
last_written_power_limit_uw=14500000
```

A deliberate test-only direct Powercap write then changed the same constraint from 14.500 W to 14.250 W while the ownership file still recorded 14.500 W as the last value written by auto-cpufreq. The other three package constraints remained unchanged.

Calling production `restore_owned()` returned:

```text
action=retired-drift
before=14.250 W
requested=15.000 W
effective=14.250 W
message=Ownership lost because the current value drifted
```

Observed behavior:
- the externally changed 14.250 W value was not overwritten;
- 14.500 W was not reasserted;
- 15.000 W was not restored by the controller;
- ownership state was retired and `/run/auto-cpufreq/power-state.json` was removed.

The test harness then restored the deliberately generated external test state from 14.250 W to the original 15.000 W baseline. Final hardware state matched the initial snapshot exactly:

```text
intel-rapl       package-0 long_term   28.000 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

## Conclusion

Hardware drift gate: PASS.

The ownership model behaved conservatively on real Powercap sysfs and did not enter a write war with a simulated external actor. The next remaining structural gate is the real systemd daemon lifecycle, including the failsafe environment, runtime ownership state, unexpected daemon termination, and `ExecStopPost` restoration.
