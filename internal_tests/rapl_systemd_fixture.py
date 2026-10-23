"""Ephemeral real-systemd laboratory fixture. NEVER access real /sys Powercap.

The GitHub Actions VM installs a uniquely named unit in /run/systemd/system;
all fake Powercap and journal paths stay under its dedicated /run directory.
This file is intentionally absent from the upstream contribution branch.
"""
import os
import signal
import sys
import time
from pathlib import Path
from unittest.mock import patch

from auto_cpufreq.bin import auto_cpufreq_restore_rapl as restore_entry
from auto_cpufreq.modules import intel_rapl as rapl


BASELINE = 15_000_000
TARGET = 12_000_000


class FakeDiscovery:
    def __init__(self, root):
        self.powercap_root = root / "fake-powercap"
        self.zone = self.powercap_root / "intel-rapl" / "intel-rapl:0"

    def powercap_zone_paths(self):
        if self.zone.is_dir():
            return ((self.zone, "intel-rapl"),)
        return ()


def paths(root):
    discovery = FakeDiscovery(root)
    return discovery, discovery.zone / "constraint_0_power_limit_uw"


def controller(root):
    return rapl.IntelRaplController(
        discovery=FakeDiscovery(root),
        state_store=rapl.RaplStateStore(root / "power-state.json"),
    )


def event(root, label):
    with (root / "events.log").open("a", encoding="utf-8") as handle:
        handle.write(label + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def daemon(root):
    engine = controller(root)
    version = Path(__file__).resolve().parent.name
    journal = engine.state_store.load()
    if journal:
        # Deliberately model the fail-closed startup contract. Starting a
        # replacement process must not overwrite unresolved old ownership.
        event(root, "daemon-blocked:" + version)
    else:
        results = engine.apply(rapl.RaplEnvelopeTargets(long_term_uw=TARGET))
        if len(results) != 1 or results[0].action != "acquired":
            raise RuntimeError("Fake RAPL acquisition failed: " + repr(results))
        event(root, "daemon-applied:" + version)

    def stop(_sig, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while True:
        time.sleep(0.5)


def restore(root, failure_mode):
    engine = controller(root)
    if failure_mode == "fail":
        real_write = rapl._write_int

        def fail_restore(path, value):
            if value == BASELINE:
                raise OSError("injected restore failure (fake sysfs only)")
            return real_write(path, value)

        write_context = patch.object(rapl, "_write_int", side_effect=fail_restore)
    else:
        write_context = patch.object(rapl, "_write_int", wraps=rapl._write_int)

    with write_context, patch.object(
        restore_entry, "IntelRaplController", return_value=engine
    ):
        code = restore_entry.main()

    event(root, "restore:" + str(code) + ":" + Path(__file__).resolve().parent.name)
    return code


def main():
    if len(sys.argv) < 3:
        raise SystemExit("usage: fixture.py daemon|restore|initialize PATH [normal|fail]")
    command, root = sys.argv[1], Path(sys.argv[2])
    if root != root.resolve() or root.parent != Path("/run"):
        raise SystemExit("Laboratory fixture must use its own direct /run directory")

    if command == "initialize":
        root.mkdir(mode=0o700, exist_ok=True)
        root.chmod(0o700)
        discovery, power = paths(root)
        discovery.zone.mkdir(parents=True)
        (discovery.zone / "name").write_text("package-0\n")
        (discovery.zone / "enabled").write_text("1\n")
        (discovery.zone / "constraint_0_name").write_text("long_term\n")
        power.write_text(str(BASELINE) + "\n")
        return 0
    if command == "daemon":
        return daemon(root)
    if command == "restore":
        return restore(root, sys.argv[3] if len(sys.argv) > 3 else "normal")
    raise SystemExit("Unsupported lab fixture mode")


if __name__ == "__main__":
    raise SystemExit(main())
