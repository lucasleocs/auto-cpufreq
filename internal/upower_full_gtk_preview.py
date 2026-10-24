"""Temporary full auto-cpufreq GTK preview using a private fake UPower bus.

This script never invokes a real charging control, a root helper, or a daemon
installation. The normal system and session services are not replaced.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
from unittest.mock import patch

import upower_modern_lab as lab

ROOT = Path(__file__).resolve().parent.parent
BATTERY = "/sys/class/power_supply/BAT1"


def _start_bus(stack, allow_headless=False):
    if not shutil.which("dbus-daemon"):
        raise RuntimeError("dbus-daemon is required")
    original_session = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not original_session and not allow_headless:
        raise RuntimeError("No desktop session D-Bus detected")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    stack.callback(_stop_process, proc)
    ready, _, _ = select.select([proc.stdout], [], [], 6)
    if not ready:
        raise RuntimeError("Private test bus did not start")
    address = proc.stdout.readline().strip()
    if not address.startswith("unix:") or proc.poll() is not None:
        raise RuntimeError("Private test bus address invalid")
    if address == original_session or address == os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
        raise RuntimeError("Refusing a nonisolated bus address")
    return address


def _stop_process(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for stream in (proc.stdout, proc.stderr):
        if stream:
            stream.close()


def _start_mock(stack, scenario, address):
    env = os.environ.copy()
    env.update({
        "UPOWER_LAB_PRIVATE": "yes",
        "DBUS_SESSION_BUS_ADDRESS": address,
        "DBUS_SYSTEM_BUS_ADDRESS": address,
    })
    cmd = [sys.executable, str(ROOT / "internal/upower_modern_lab.py"),
           "serve", "--scenario", scenario]
    mock = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env,
    )
    stack.callback(_stop_process, mock)
    ready, _, _ = select.select([mock.stdout], [], [], 8)
    if not ready:
        raise RuntimeError("Private UPower mock did not start")
    line = mock.stdout.readline().strip()
    if line != "PRIVATE MOCK READY: " + scenario:
        raise RuntimeError("Private UPower startup failed: " + line)


def _mock_thresholds(stack, mock_battery, stop):
    from auto_cpufreq.modules.system_info import SystemInfo

    original_read = SystemInfo.read_file
    fake = {}
    if mock_battery:
        stack.enter_context(patch.object(SystemInfo, "get_battery_path", return_value=BATTERY))
        stack.enter_context(patch.object(SystemInfo, "external_power_state", return_value=True))
        fake.update({
            BATTERY + "/status": "Not charging",
            BATTERY + "/capacity": "99",
            BATTERY + "/charge_control_end_threshold": "100",
        })
        stack.enter_context(patch.object(SystemInfo, "_battery_power_watts", return_value=0.0))
    if stop is not None:
        fake[BATTERY + "/charge_stop_threshold"] = str(stop)
        fake[BATTERY + "/charge_control_end_threshold"] = str(stop)

    def read(path):
        if path in fake:
            return fake[path]
        return original_read(path)

    stack.enter_context(patch.object(SystemInfo, "read_file", side_effect=read))


def _disable_privileged_actions(stack):
    from auto_cpufreq.gui import objects
    from gi.repository import GLib

    def refuse(arguments, callback):
        print("SIMULATION: blocked privileged action", arguments, flush=True)
        GLib.idle_add(callback, None, RuntimeError("Read-only simulation"))

    stack.enter_context(patch.object(objects, "_run_privileged_async", refuse))


def _preview(args, stack):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from auto_cpufreq.config.config import config, find_config_file
    from auto_cpufreq.core import daemon_is_running
    from auto_cpufreq.gui import app
    from auto_cpufreq.modules.system_info import system_info

    config.set_path(find_config_file(None))
    stack.enter_context(patch.object(app, "CSS_FILE", str(ROOT / "scripts/style.css")))
    stack.enter_context(patch.object(app, "ICON_FILE", str(ROOT / "images/icon.png")))

    daemon_active = daemon_is_running()
    print("===== FULL GTK / PRIVATE UPOWER SIMULATION =====", flush=True)
    print("Scenario:", args.scenario, flush=True)
    print("Installed daemon active:", daemon_active, flush=True)
    print("Physical charge thresholds: never written", flush=True)
    print("Kernel stop simulated:", args.mock_kernel_stop, flush=True)

    # GitHub-hosted virtual machines have no auto-cpufreq daemon or BAT1.
    if args.mock_battery:
        stack.enter_context(patch.object(app, "daemon_is_running", return_value=True))
    elif not daemon_active:
        raise RuntimeError("Existing auto-cpufreq daemon not active; full Controls preview requires it")

    _disable_privileged_actions(stack)

    # No call to app.auto_cpufreq_gtk.main(): it invokes an updater check.
    window = app.ToolWindow()
    window.set_title("SIMULATED UPower — full auto-cpufreq GTK (read-only)")
    window.connect("destroy", Gtk.main_quit)
    window.show_all()

    report = system_info.generate_system_report()
    if report.battery_info.source != "upower":
        window.destroy()
        raise RuntimeError("Simulated UPower was not used; refusing fallback-only preview")
    window.report_view.apply_report(report)
    view = window.report_view

    print("Source:", report.battery_info.source, flush=True)
    print("Status:", view.battery_values["status"].get_text(), flush=True)
    print("Charge:", view.battery_values["charge"].get_text(), flush=True)
    print("Start:", view.battery_values["start"].get_text(), flush=True)
    print("Stop:", view.battery_values["stop"].get_text(), flush=True)
    print("Charge limit visible:", view.charge_limit_value.get_visible(), flush=True)
    if view.charge_limit_value.get_visible():
        print("Charge limit:", view.charge_limit_value.get_text(), flush=True)
    print("Controls present:", hasattr(window, "governor_control"), flush=True)

    if args.check:
        assert hasattr(window, "governor_control")
        assert hasattr(window, "menu")
        expected = {
            "charging-55": ("55%", "Charging", "80% (UPower)"),
            "reached-80": ("80%", "Pending charge", "80% (UPower)"),
            "full-100": ("100%", "Fully charged", "80% (UPower)"),
            "start-end": ("99%", "Fully charged", "80% (UPower)"),
            "disabled": ("55%", "Charging", "100%"),
            "firmware": ("99%", "Fully charged", "100%"),
            "firmware-disabled": ("55%", "Charging", "100%"),
            "legacy": ("99%", "Fully charged", "100%"),
        }
        actual = (view.battery_values["charge"].get_text(),
                  view.battery_values["status"].get_text(),
                  view.battery_values["stop"].get_text())
        if args.scenario in expected:
            expected_values = expected[args.scenario]
            if args.scenario in ("charging-55", "reached-80", "full-100", "start-end"):
                assert args.mock_kernel_stop == 80
            assert actual == expected_values, (actual, expected_values)
        if args.scenario in ("firmware", "firmware-disabled"):
            assert view.charge_limit_value.get_visible()
            assert not view.battery_names["stop"].get_visible()
        window.destroy()
    else:
        print("Close this window to end the private test.", flush=True)
        Gtk.main()


def main():
    parser = argparse.ArgumentParser(description="Preview actual full GTK with newer UPower properties using private D-Bus; never writes battery limits.")
    parser.add_argument("--scenario", choices=lab.SCENARIOS, default="charging-55")
    parser.add_argument("--mock-kernel-stop", type=int, choices=range(1,101), metavar="PERCENT",
                        help="Simulate only the kernel's charge-stop readback in Python (no writes)")
    parser.add_argument("--mock-battery", action="store_true", help="CI host with no physical battery")
    parser.add_argument("--check", action="store_true", help="CI assertions; exit without opening a persistent window")
    args = parser.parse_args()

    with ExitStack() as stack:
        address = _start_bus(stack, allow_headless=args.check and args.mock_battery)
        _start_mock(stack, args.scenario, address)
        stack.enter_context(patch.dict(os.environ, {"DBUS_SYSTEM_BUS_ADDRESS": address}))
        _mock_thresholds(stack, args.mock_battery, args.mock_kernel_stop)
        _preview(args, stack)


if __name__ == "__main__":
    main()
