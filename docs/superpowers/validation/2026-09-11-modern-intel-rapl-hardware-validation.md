# Modern Intel RAPL Hardware Validation

Date: 2026-09-11

Branch: `modern-intel-rapl-envelopes`

Validated production HEAD before hardware write: `941ee08604dcfbb02d1623cc6873bfe6ce9a95e9`

Hardware:
- Samsung Galaxy Book 4 NP750XGJ-KG5BR
- Intel Core i3-1315U (Raptor Lake-U)
- Linux Mint 22.3
- Kernel 7.0.0-31-generic
- Modern Intel backend selected: `ModernIntelHwpPolicy`

## Read-only preflight

The real Powercap layout exposed four controllable package constraints:

| Control type | Zone | Constraint | Current | Reported max |
| --- | --- | --- | ---: | ---: |
| `intel-rapl` | `package-0` | `long_term` | 28.000 W | 15.000 W |
| `intel-rapl` | `package-0` | `short_term` | 41.000 W | 0 W |
| `intel-rapl-mmio` | `package-0` | `long_term` | 15.000 W | 15.000 W |
| `intel-rapl-mmio` | `package-0` | `short_term` | 41.000 W | 0 W |

Other preflight results:
- RAPL envelopes disabled in the user's current configuration.
- `/run/auto-cpufreq/power-state.json` absent before testing.
- All four package constraint paths readable and reported writable as root.
- `intel-rapl` and `intel-rapl-mmio` remained distinct control types.

The `intel-rapl` PL1 observation confirms the design requirement that `constraint_0_max_power_uw` must not be treated as an unconditional write ceiling when the currently accepted external limit is already above it. It must also never be used as permission to raise a limit.

## Controlled hardware write gate 1: MMIO PL1

Authorized test scope:
- Control type: `intel-rapl-mmio`
- Zone: `package-0`
- Constraint: `long_term`
- Initial value: 15.000 W
- Requested value: 14.500 W
- `short_term`: omitted
- `intel-rapl`: hidden from the test controller and never targeted

Initial snapshot:

```text
intel-rapl       package-0 long_term   28.000 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

Apply result:

```text
action=acquired
before=15.000 W
requested=14.500 W
effective=14.500 W
```

The controller established ownership only after verified readback. Persisted ownership state contained exactly one record:

```text
control_type=intel-rapl-mmio
zone=package-0
constraint=long_term
original_power_limit_uw=15000000
last_written_power_limit_uw=14500000
```

Snapshot while owned:

```text
intel-rapl       package-0 long_term   28.000 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   14.500 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

Result: changing MMIO PL1 did not alter the other three observed package constraints.

Restore result:

```text
action=restored
before=14.500 W
requested=15.000 W
effective=15.000 W
```

Final snapshot matched the initial snapshot exactly, and the ownership state file was removed after successful restoration.

## Controlled hardware write gate 2: intel-rapl PL1 above reported max metadata

Authorized test scope:
- Control type: `intel-rapl`
- Zone: `package-0`
- Constraint: `long_term`
- Initial value: 28.000 W
- Requested value: 27.500 W
- Reported `constraint_0_max_power_uw`: 15.000 W
- `short_term`: omitted
- `intel-rapl-mmio`: hidden from the test controller and never targeted

Apply result:

```text
action=acquired
before=28.000 W
requested=27.500 W
effective=27.500 W
```

The controller correctly allowed a conservative reduction from the externally accepted 28 W baseline despite the reported 15 W max metadata. It did not use that metadata as permission to raise any limit.

Persisted ownership state contained exactly one record:

```text
control_type=intel-rapl
zone=package-0
constraint=long_term
original_power_limit_uw=28000000
last_written_power_limit_uw=27500000
```

Snapshot while owned:

```text
intel-rapl       package-0 long_term   27.500 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

Result: changing `intel-rapl` PL1 did not alter the other three observed package constraints, including MMIO PL1.

Restore result:

```text
action=restored
before=27.500 W
requested=28.000 W
effective=28.000 W
```

Final snapshot matched the initial snapshot exactly, and the ownership state file was removed after successful restoration.

## Controlled hardware write gate 3: MMIO PL2

Authorized test scope:
- Control type: `intel-rapl-mmio`
- Zone: `package-0`
- Constraint: `short_term`
- Initial value: 41.000 W
- Requested value: 40.500 W
- `long_term`: omitted
- `intel-rapl`: hidden from the test controller and never targeted

Apply result:

```text
action=acquired
before=41.000 W
requested=40.500 W
effective=40.500 W
```

Persisted ownership state contained exactly one record:

```text
control_type=intel-rapl-mmio
zone=package-0
constraint=short_term
original_power_limit_uw=41000000
last_written_power_limit_uw=40500000
```

Snapshot while owned:

```text
intel-rapl       package-0 long_term   28.000 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  40.500 W
```

Result: changing MMIO PL2 did not alter either PL1 constraint or `intel-rapl` PL2.

Restore result:

```text
action=restored
before=40.500 W
requested=41.000 W
effective=41.000 W
```

Final snapshot matched the initial snapshot exactly, and the ownership state file was removed after successful restoration.

## Gate conclusions so far

Hardware gates 1 through 3 passed:
- real Powercap discovery: PASS
- conservative lowering: PASS
- exact readback verification: PASS
- ownership persistence: PASS
- MMIO PL1 write leaves `intel-rapl` unchanged: PASS
- `intel-rapl` PL1 write leaves MMIO unchanged: PASS
- MMIO PL2 write leaves all other observed constraints unchanged: PASS
- accepted-current-above-reported-max handling: PASS
- restoration to external baseline: PASS
- ownership retirement after restore: PASS
- final state equals initial state after each gate: PASS

No unexpected firmware quantization or cross-interface change has been observed in the three isolated hardware writes.

## Next hardware gate

Test `intel-rapl` `package-0` `short_term` independently using a minimal reduction from 41.000 W to 40.500 W. Both long-term constraints and MMIO short-term must remain untargeted and be checked for unintended changes. Restoration must return `intel-rapl` PL2 to 41.000 W and retire ownership. After that isolated constraint gate, move to ownership-drift behavior and then the real systemd daemon/failsafe lifecycle.
