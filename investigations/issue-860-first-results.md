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
