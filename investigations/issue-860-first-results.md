# Issue #860 — first regression results

Date: October 23, 2026 (America/Sao_Paulo)  
Upstream and fork base master: 5a1683037a583785f802b5d039022127b9b154cd  
Investigation branch: test/issue-860-runtime-20261023  
Production changes: none

## CI results

Experimental workflow:
https://github.com/lucasleocs/auto-cpufreq/actions/runs/40047272848

- 7/7 PTY/Urwid lifecycle scenarios passed.
- 3/3 read-only Linux process sampler checks passed.
- 3/3 GTK refresh lifecycle tests passed with a real GLib runtime under Xvfb.
- Python regression modules compiled successfully.
- Earlier baseline Linux Build and Nix Flake checks passed; these checks validate installation/build, not the reported CPU-load reproduction.

The PTY tests cover Q, Ctrl+C, explicit SIGHUP, PTY master closure, SIGHUP ignored, five seconds of normal refresh, and terminal closure after five seconds of refresh.

No sustained single-core load was observed in the isolated synthetic terminal lifecycle. The five-second idle sample reported 0.000 core-equivalent CPU usage at the workflow's measurement precision. The SIGHUP-ignored case exited with code 120; it did not spin, but its abnormal exit status needs separate diagnosis if reliable cleanup after nonstandard signal handling is required.

The GTK tests exercised the current ToolWindow scheduling methods with the actual GLib event context and simulated reports. They confirmed that concurrent requests coalesce, cleanup cancels a GLib timer during an active worker, and a subsequent refresh succeeds after a simulated collector exception.

These tests do not run the full installed GUI, use the original hardware-reporting backend, reproduce suspend/hibernate, or run the actual CLI through sudo. Passing them cannot justify closing #860 or #685.

## New diagnostic capability

A read-only sampler was added at:

tests/regression/issue_860/sample_process.py

It samples /proc process and thread CPU ticks, RSS, number of threads, file descriptors, state and context-switch counters. The CPU percentage is expressed relative to one logical CPU: 100% means approximately one fully utilized logical CPU, not 100% of all cores combined.

Example from a local checkout of this experimental branch:

~~~bash
pgrep -af 'auto-cpufreq-gtk'
python3 tests/regression/issue_860/sample_process.py \
  --pid 12345 \
  --interval 2 \
  --samples 60 \
  --output /tmp/auto-cpufreq-gtk-runtime.csv
~~~

Replace 12345 with the actual GTK process PID. Run the sampler as the regular user when possible. Fields that cannot be read because of /proc permissions remain blank. It neither writes to sysfs nor restarts or reconfigures the daemon.

To inspect an already running stats process, identify its PID separately and sample it the same way; do not use broad process-kill commands matching "auto-cpufreq", as those might also affect the production daemon.

## Next reproduction matrix

1. Record installed auto-cpufreq version, desktop session, kernel and the PID of the *installed* GTK process. Check that the installed version actually contains #964 before comparing behavior.
2. Measure GTK at idle and with its 1-, 2- and 5-second refresh settings, and inspect the highest-CPU thread if any sustained load appears.
3. Open and close the GUI repeatedly, including GTK Monitor Mode when available.
4. If suspend/resume is already supported and safe on the machine, compare several pre/post-resume traces. Do not configure hibernation specifically for this test.
5. Test terminal stats started normally and through sudo as distinct cases, checking Q/Ctrl+C and controlled terminal hangup. Identify any surviving monitor PID before attempting to stop it; do not terminate the daemon.
6. If a real process spins, capture bounded traces of only that PID to determine whether it is blocked in a terminal event loop, GTK/GLib callbacks, a sysfs read path, or a configuration watcher.
7. Only after isolating a reproducible defect should a minimal correction be developed on a clean production branch.

## Working interpretation

There is presently no reproduced sustained high-CPU loop on current master in the isolated tests. The GTK high-CPU reports that predate PR #964 do not establish that the old problem survives the redesigned refresh lifecycle. The later --stats hangup report is a separate candidate fault and has not yet been reproduced with the installed application and sudo.

The pyinotify object is initialized by the configuration module, but the current GTK and stats entry points do not start its ThreadedNotifier. Do not change that dependency on suspicion alone.

## Official references

- Urwid watch-pipe cleanup: https://urwid.readthedocs.io/en/latest/reference/main_loop.html
- GLib idle callbacks: https://docs.gtk.org/glib/func.idle_add.html
- GLib periodic timers: https://docs.gtk.org/glib/func.timeout_add_seconds.html
- Linux pseudoterminals: https://man7.org/linux/man-pages/man7/pty.7.html
- Linux signal semantics: https://man7.org/linux/man-pages/man7/signal.7.html

All investigation files, workflows and diagnostic utilities must remain outside the eventual upstream production patch.


## Physical Cinnamon baseline — complete 60-sample summary

The user confirmed that, near the last three samples, they opened **Advanced Settings** and **About** in the GTK application; prior to that they left the interface undisturbed.

Baseline, 60 samples over approximately 120 seconds, PID 207115:

| Measurement | Observation |
| --- | --- |
| One-core CPU minimum | 0.50% |
| One-core CPU mean | 1.76% |
| One-core CPU median | 1.50% |
| One-core CPU maximum | 6.99% |
| Samples >=50% of one core | 0/60 |
| RSS initial | 49.95 MiB |
| RSS final and maximum | 61.66 MiB |
| Threads | 6 throughout |
| File descriptors | 15 throughout |

The three highest CPU samples all occurred at the end, at 21:59:33–21:59:37 -03:00, during interaction with additional GTK windows. The approximately 11.71 MiB RSS increase coincided with those actions. This is consistent with normal UI allocations; without a later observation after closing dialogs and repeating the interactions, neither memory leak nor permanent retention can be established.

Interpretation: no reproduction of high CPU in a normal GTK idle baseline. Next distinguish normal UI allocations from retained growth with a post-dialog steady-state sample, then collect a fresh baseline after a normal suspend/resume cycle if safe. The collector should be restarted *after* waking, rather than leaving a sampling interval straddling sleep, and the correct GTK Python PID must be reidentified.

## Physical Cinnamon baseline — initial excerpt (October 23, 2026)

Host: GalaxyMint, Linux Mint Cinnamon. Installed GUI process:
- PID 207115: /opt/auto-cpufreq/current/venv/bin/auto-cpufreq-gtk
- Parent launcher PID 207113: /usr/local/bin/auto-cpufreq-gtk
- Installed release includes commit 35d8015afc1b, five commits behind investigated master 5a1683037a58; gui/app.py itself is identical at these revisions.

The user completed 60 two-second samples from approximately 21:57:37 to 21:59:37 local time. Only the first four and last ten records were shared, so the full-file minimum, mean, median and maximum cannot yet be reported.

Observed excerpt:
- CPU frequently 1.5–2.5% of one logical CPU; highest supplied sample 6.99%, nowhere near the reported sustained 100% case.
- Six threads and fifteen file descriptors throughout the displayed rows, with sleeping process state.
- RSS approximately 49.95–50.8 MiB for the supplied baseline segments, rising to 61.66 MiB at the last record.
- Last three samples: 6.49%, 6.49%, and 6.99% CPU. The leading activity was on the GTK main thread. Voluntary context switches increased substantially at the end.
- The observed late memory increase may be transient UI activity or another normal allocation pattern; the excerpt does not establish a leak or its trigger.

Required follow-up: analyze all 60 CSV rows, note any user interaction or view change around 21:59:33–21:59:37, and compare a longer idle capture before any suspend/resume trial. Do not modify runtime implementation based on this trace alone.


## Cinnamon suspend/resume — preserved partial sample

The user suspended the laptop for several seconds and resumed successfully with the GTK application still responsive. The GUI Python PID remained 207115. A complete post-resume capture was taken but then overwritten by a second invocation using the same CSV output filename; that second invocation was interrupted after two samples. The complete original post-resume data is therefore not available from this path.

The surviving two samples, 22:05:46 and 22:05:48 -03:00, both reported 1.00% CPU of one logical core, 41.27 MiB RSS, five threads, and 15 open FDs. This does not reproduce the high-CPU condition, but the sample is too short to characterize post-resume stability. The difference from the earlier 61.66 MiB / six-thread last sample is not evidence of a regression or leak.

Next: perform a new 60-sample capture while leaving the same resumed GTK process running, saving under a timestamped filename to prevent accidental overwrite. Repeating the physical suspension is not required for this immediate observation; a later multi-resume stress test may be useful if the symptom remains intermittent.


## Cinnamon post-resume — complete 60-sample capture

After resuming from a short system suspend, the original GTK process PID 207115 remained alive and responsive. The user collected a new complete two-minute reading, leaving the interface idle:

- CSV stored locally as ~/auto-cpufreq-issue860/gtk-pos-suspensao-20261023-220944.csv (not uploaded into this repository).
- Sample count: 60, one every 2 seconds.
- CPU mean: 1.32% of one logical core.
- CPU median: 1.50%.
- CPU maximum: 2.50%.
- Samples at or above 50%: 0/60.
- RSS: 40.91 MiB initially, 40.92 MiB finally and at maximum.
- Threads: 6 throughout.
- Open FDs: 15 throughout.

For comparison, pre-suspend CPU averaged 1.76% (maximum 6.99%, occurring during user interaction with Advanced Settings/About). The post-suspend run showed lower CPU usage and near-constant memory, with no high-CPU reproduction. Different GUI interaction and memory conditions prevent attributing the memory difference directly to suspend/resume.

Scope limitation: this was one short suspend/resume cycle on an installed build at 35d8015afc1b, not an extended multiple-cycle stress test, and does not confirm the upstream issue fixed. The user's local CSV is not present in the GitHub branch; values here are from the user-provided summary.

Next: consider several ordinary suspend/resume cycles with a fresh, timestamped process measurement after the last resume, and/or investigate the distinct real-terminal --stats hangup report. Avoid modifying sysfs, RAPL, daemon services or the production installation.
