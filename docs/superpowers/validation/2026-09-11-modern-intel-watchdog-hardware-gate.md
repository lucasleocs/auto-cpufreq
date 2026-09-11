# Modern Intel systemd Watchdog Hardware Gate

Date: 2026-09-11

Branch: `modern-intel-rapl-envelopes`

Validated production code: `941ee08604dcfbb02d1623cc6873bfe6ce9a95e9`

Hardware:
- Samsung Galaxy Book 4 NP750XGJ-KG5BR
- Intel Core i3-1315U (Raptor Lake-U)
- Linux Mint 22.3
- Kernel 7.0.0-31-generic

This gate follows the real systemd RAPL failsafe gate. It is intentionally RAPL-disabled so the systemd watchdog path can be validated independently of Powercap writes.

## Purpose

Verify on real hardware that the Modern Intel event-driven daemon sends systemd watchdog heartbeats from the permitted main process and remains alive across multiple watchdog windows. Also verify that the watchdog test does not create Intel RAPL ownership state.

A previous systemd failsafe run logged one incidental notification from a non-main PID. That run still restored RAPL correctly after `SIGKILL`, but the notification warning was not treated as sufficient evidence of watchdog correctness. This dedicated gate isolates watchdog behavior.

## Test configuration

The temporary service used:

```ini
[Service]
Type=simple
User=root
Environment=PYTHONPATH=/tmp/auto-cpufreq-rapl-stage5
Environment=AUTO_CPUFREQ_RAPL_FAILSAFE=1
RuntimeDirectory=auto-cpufreq
RuntimeDirectoryMode=0700
WatchdogSec=6s
```

RAPL envelopes were explicitly disabled:

```ini
[intel_power]
enable_rapl_envelopes = false
```

The installed auto-cpufreq console entry point was executed with the Stage 5 checkout selected through `PYTHONPATH`.

## Observed systemd state

Immediately after startup:

```text
MainPID: 174446
NotifyAccess: main
WatchdogUSec: 6s
Watchdog timestamp T0: 517344942243
```

After four seconds:

```text
Watchdog timestamp T1: 517348275564
PASS: service survived first watchdog interval.
```

After eight seconds:

```text
Watchdog timestamp T2: 517351278549
PASS: service remained active beyond two 6-second watchdog windows.
PASS: systemd watchdog timestamp advanced.
```

The advancing `WatchdogTimestampMonotonic` values provide direct systemd-side evidence that watchdog notifications from the daemon were accepted.

## RAPL safety

No RAPL ownership state was created during the watchdog-only test:

```text
PASS: no RAPL ownership state exists.
```

The journal for the dedicated run contained only the normal service start line and did not reproduce the earlier non-main-PID notification warning.

Cleanup stopped the temporary service and restored the originally installed auto-cpufreq daemon:

```text
PASS: original daemon active again.
```

## Conclusion

Systemd watchdog hardware gate: PASS.

Validated on real hardware:
- effective `NotifyAccess=main`;
- effective `WatchdogSec=6s`;
- daemon remains active across multiple watchdog windows;
- systemd watchdog timestamp advances repeatedly;
- no RAPL ownership state is created when RAPL envelopes are disabled;
- the earlier incidental non-main notification warning was not reproduced in the isolated watchdog test.

Together with the preceding systemd RAPL failsafe gate, this validates both sides of the service lifecycle on the tested machine: ongoing watchdog liveness and conservative RAPL recovery after abrupt daemon termination.