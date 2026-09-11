# Modern Intel RAPL systemd Failsafe Hardware Gate

Date: 2026-09-11

Branch: `modern-intel-rapl-envelopes`

Validated production code: `941ee08604dcfbb02d1623cc6873bfe6ce9a95e9`

Hardware:
- Samsung Galaxy Book 4 NP750XGJ-KG5BR
- Intel Core i3-1315U (Raptor Lake-U)
- Linux Mint 22.3
- Kernel 7.0.0-31-generic

This gate follows the four isolated PL1/PL2 writes and the ownership-drift gate.

## Purpose

Validate the production daemon integration under a real systemd lifecycle: explicit RAPL opt-in, failsafe environment, runtime ownership state, conservative handling of independent MSR/MMIO constraints, abrupt daemon death, and `ExecStopPost` restoration.

## Test setup

A temporary systemd unit was used so the existing installed daemon could be stopped and restored without replacing the user's installation. The unit used the Stage 5 checkout through `PYTHONPATH`, the installed virtual environment, `AUTO_CPUFREQ_RAPL_FAILSAFE=1`, `RuntimeDirectory=auto-cpufreq`, `RuntimeDirectoryMode=0700`, `ExecStopPost` invoking the Stage 5 restore helper, `Restart=no`, and `WatchdogSec=30s`.

The initial test harness incorrectly executed `auto_cpufreq/bin/auto_cpufreq.py` directly. That caused Python module shadowing (`'auto_cpufreq' is not a package`) before any RAPL write. The hardware baseline remained unchanged and `ExecStopPost` exited successfully. The harness was corrected to use the installed Poetry console entry point while keeping `PYTHONPATH` pointed at the Stage 5 checkout. No production code was changed for this harness error.

## Production-path RAPL application

Temporary charger configuration enabled RAPL envelopes and requested only:

```text
rapl_package_long_term_w = 27.5
```

The daemon started successfully. Observed package constraints while active:

```text
intel-rapl       package-0 long_term   27.500 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

This proves the production path lowered `intel-rapl` PL1 from 28.000 W to 27.500 W while refusing to raise the stricter external MMIO PL1 from 15.000 W to the 27.500 W target.

Runtime ownership contained exactly one record:

```text
control_type=intel-rapl
zone=package-0
constraint=long_term
original_power_limit_uw=28000000
last_written_power_limit_uw=27500000
```

## Abrupt daemon termination

The systemd MainPID was sent `SIGKILL` using `systemctl kill --kill-whom=main --signal=KILL`.

Observed systemd state:

```text
Result=signal
ExecMainCode=2
ExecMainStatus=9
ActiveState=failed
SubState=failed
```

The journal then reported:

```text
Original RAPL limit restored and ownership retired
```

The failed service result is expected because the main daemon was deliberately killed. The important lifecycle behavior is that `ExecStopPost` still ran after the abrupt termination.

## Post-crash verification

Final package constraints:

```text
intel-rapl       package-0 long_term   28.000 W
intel-rapl       package-0 short_term  41.000 W
intel-rapl-mmio  package-0 long_term   15.000 W
intel-rapl-mmio  package-0 short_term  41.000 W
```

`/run/auto-cpufreq/power-state.json` was absent after recovery.

Results:
- real systemd daemon start: PASS
- explicit failsafe environment: PASS
- production RAPL opt-in path: PASS
- first-acquisition no-raise rule across MSR/MMIO: PASS
- runtime ownership persistence: PASS
- abrupt `SIGKILL` lifecycle: PASS
- `ExecStopPost` after abrupt daemon death: PASS
- restoration to the 28 W external baseline: PASS
- ownership retirement: PASS
- final hardware state equals initial state: PASS
- original installed auto-cpufreq daemon restarted after test cleanup: PASS

## Follow-up observation

The journal also contained one notification warning immediately after startup:

```text
Got notification message from PID 164748, but reception only permitted for main PID 164633
```

This did not affect the RAPL failsafe result. Because `WatchdogSec=30s` had not yet reached its first expected half-interval heartbeat before the deliberate crash, watchdog acceptance by the MainPID was not proven by this gate. A separate read-only/no-RAPL watchdog lifecycle check should verify that the MainPID heartbeat keeps the service alive across multiple watchdog intervals and determine whether the child notification is merely an inherited `NOTIFY_SOCKET` side effect or evidence requiring a Stage 4 follow-up.