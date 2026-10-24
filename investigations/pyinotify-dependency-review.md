# pyinotify dependency review — October 2026 (investigation only)

Scope: `test/issue-860-runtime-20261023`; **no library migration or policy changes in this phase**.

## Actual call sites and lifecycle

- `auto_cpufreq/config/config.py` imports `pyinotify` and creates the global `WatchManager` and `ThreadedNotifier` during import of the config singleton. Consequently even a GTK-only application constructs an inotify file descriptor and the notifier's wakeup pipe before using them.
- The GTK entrypoint calls `config.set_path(...)` but **does not call `config.notifier.start()`**. This significantly weakens the hypothesis that the notifier's polling thread directly causes 100% CPU in the GTK process. A thread should only be attributed to pyinotify if the live process confirms it exists.
- The CLI `--daemon`, `--monitor`, and `--live` paths explicitly start `conf.notifier` and arrange stop/cleanup in their exit paths. `--stats` does **not** start it, although all CLI paths initialize `_Config` and add a watch. Its optional warning-prompt branch calls `conf.notifier.stop()` on Ctrl+C even when the notifier was not started — this deserves a separate lifecycle regression test.
- `config.set_path` watches only the configuration file's **parent directory**, with CREATE, DELETE, MODIFY, MOVED_FROM and MOVED_TO. It does not watch recursive trees.

## Concrete integration correctness risks (not necessarily pyinotify bugs)

1. `_Config.update_config` assigns `self._config = ConfigParser()` *before* the new file is read or validated. A watcher callback may therefore expose an empty or partially parsed configuration to another thread. Parse into a local object and atomically publish only a valid snapshot; define explicitly what happens on file deletion and invalid replacements.
2. Watching `IN_MODIFY` may parse mid-write. Use CLOSE_WRITE and MOVED_TO plus backup handling or implement a short, bounded stability/debounce policy. Atomic editor replacements need to remain supported.
3. `ConfigEventHandler._process_update` compares `event.pathname.rstrip("~")` with the configured path. This also matches backup names ending in repeated tildes and is not a precise file-name policy.
4. `set_path` does not check `WatchManager.add_watch` failures (for example a missing user configuration directory) or reconcile watches if the selected config location changes.
5. The fork's `ThreadedNotifier.stop()` writes to a pipe, unconditionally joins the thread, and closes descriptors afterward. Failure paths and repeat stop/start attempts need separate tests. The actual fork `check_events` filters `POLLIN`; a persistent poll error could conceivably cause a loop, but this has **not** been observed or established as the issue #860 cause.

## Dependency and alternative assessment

- Current dependency: git source `shadeyg56/pyinotify-3.12`, last code push observed 2024-04-29. It is a Python 3.12 compatible fork of pyinotify. A Git dependency without an explicitly pinned `rev` deserves reproducibility review.
- Minimal Linux-only replacement: `inotify-simple` (PyPI 2.0.1 released 2025-08-25). It wraps Linux inotify with a small API. The application must implement its own bounded thread/selector lifecycle and event filtering.
- Higher-level maintained option: `watchdog` (supports Linux inotify, observer lifecycle, file events and cross-platform APIs). It introduces additional abstraction/dependency overhead for watching a single configuration file; a lower CPU cost is not established.
- No watcher/polling: verify file inode/mtime/size on the daemon's normal 2-second iteration. Could remove the notifier thread and dependency for the daemon, but Monitor and Live need matching reload paths and detection latency, and atomic replacements require careful semantics.
- `Gio.FileMonitor` only for GTK is **not** a common backend for the GTK-free daemon/CLI.

Official references: https://man7.org/linux/man-pages/man7/inotify.7.html ; https://pypi.org/project/inotify-simple/ ; https://python-watchdog.readthedocs.io/en/latest/api/observers.html ; https://github.com/shadeyg56/pyinotify-3.12/blob/master/python3/pyinotify.py

## Recommended evaluation gate before selecting a replacement

1. Construct a deterministic temp-directory test suite (create/modify, in-place partial write, atomic rename, backup edits, deletion, unreadable file, missing parent directory, repeated path assignments, rapid events, event queue overflow).
2. Establish a failure-safe publish contract: previous complete config remains active on parse failure, explicit policy for missing/deleted config, correct reloading on atomic replace. Track thread and FD count and avoid busy spins/duplicate watches.
3. Run real read-only A/B comparisons of the current fork and one candidate (prefer `inotify-simple` first) with a synthetic, CPU-limited test process. Do **not** run two governors/services competing on the host or deploy the candidate as the active daemon before tests pass.
4. Check package/install implications (Poetry/git lock, Debian/Mint build path, Fedora/Nix, Python 3.12+) and distinguish failures in the watcher library from bugs in application configuration state handling.
5. Only migrate if the candidate offers measurable simplicity, reproducible packaging, reliable event delivery, or better shutdown — not on the unconfirmed hypothesis that pyinotify is the source of #860.

This note is an investigation, not a decision to replace the dependency.
