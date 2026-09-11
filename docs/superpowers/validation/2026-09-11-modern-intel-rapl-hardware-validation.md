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

## Gate conclusion

Hardware gate 1 passed:
- real Powercap discovery: PASS
- conservative lowering: PASS
- exact readback verification: PASS
- ownership persistence: PASS
- independent MSR/MMIO observation during MMIO write: PASS
- restoration to external baseline: PASS
- ownership retirement after restore: PASS
- final state equals initial state: PASS

No unexpected firmware quantization or cross-interface change was observed in this first write test.

## Next hardware gate

The next isolated write should test `intel-rapl` `package-0` `long_term` separately, using a minimal reduction from 28.000 W to 27.500 W. This specifically validates the real case where current PL1 (28 W) is above the reported max metadata (15 W) while preserving the rule that a first acquisition may only lower the externally observed current limit. MMIO and both short-term constraints must remain untargeted and be checked for unintended changes. Restoration must return `intel-rapl` PL1 to 28.000 W and retire ownership.
