"""Private D-Bus simulation of newer UPower battery properties.

EXPERIMENT ONLY: no real UPower daemon, no write methods, no sudo, no
changes to the system bus, and no changes to charge thresholds.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
import os
import select
import shutil
import subprocess
import sys
from unittest.mock import patch

SCENARIOS = ("end-only", "start-end", "firmware", "legacy")
BATTERY = "/sys/class/power_supply/BAT1"
UPOWER_PATH = "/org/freedesktop/UPower"
DEVICE_PATH = UPOWER_PATH + "/devices/battery_BAT1"


def properties(scenario):
    from dbus_next import Variant

    vals = {
        "NativePath": Variant("s", "BAT1"),
        "Type": Variant("u", 2),
        "PowerSupply": Variant("b", True),
        "IsPresent": Variant("b", True),
        "State": Variant("u", 4),
        "Percentage": Variant("d", 99.0),
        "EnergyRate": Variant("d", 0.0),
    }
    if scenario != "legacy":
        vals.update({
            "ChargeThresholdSupported": Variant("b", True),
            "ChargeThresholdEnabled": Variant("b", True),
            "ChargeStartThreshold": Variant("u", 4294967295),
            "ChargeEndThreshold": Variant("u", 4294967295),
        })
        if scenario == "end-only":
            vals["ChargeThresholdSettingsSupported"] = Variant("u", 2)
            vals["ChargeEndThreshold"] = Variant("u", 80)
        elif scenario == "start-end":
            vals["ChargeThresholdSettingsSupported"] = Variant("u", 3)
            vals["ChargeStartThreshold"] = Variant("u", 75)
            vals["ChargeEndThreshold"] = Variant("u", 80)
        else:
            vals["ChargeThresholdSettingsSupported"] = Variant("u", 4)
    return vals


def insist_on_private_bus():
    session = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    system = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
    if (os.environ.get("UPOWER_LAB_PRIVATE") != "yes"
            or not session or system != session or not session.startswith("unix:")):
        raise RuntimeError("Private D-Bus protection failed; not proceeding")


async def serve(scenario):
    from dbus_next import BusType, Message, MessageType
    from dbus_next.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SESSION).connect()

    def handle(message):
        if message.message_type != MessageType.METHOD_CALL:
            return None
        if (message.path == UPOWER_PATH
                and message.interface == "org.freedesktop.UPower"
                and message.member == "EnumerateDevices"):
            return Message.new_method_return(
                message, signature="ao", body=[[DEVICE_PATH]])
        if (message.path == DEVICE_PATH
                and message.interface == "org.freedesktop.DBus.Properties"
                and message.member == "GetAll"):
            if message.body != ["org.freedesktop.UPower.Device"]:
                return Message.new_error(
                    message, "org.freedesktop.DBus.Error.UnknownInterface",
                    "Unsupported interface")
            return Message.new_method_return(
                message, signature="a{sv}", body=[properties(scenario)])
        if message.member in ("Set", "EnableChargeThreshold"):
            return Message.new_error(
                message, "org.freedesktop.DBus.Error.AccessDenied",
                "Simulation never permits writes")
        return None

    bus.add_message_handler(handle)
    await bus.request_name("org.freedesktop.UPower")
    print("PRIVATE MOCK READY: " + scenario, flush=True)
    await bus.wait_for_disconnect()


def report_for_view(mock_battery):
    from auto_cpufreq.modules.system_info import SystemInfo, system_info

    with ExitStack() as stack:
        if mock_battery:
            stack.enter_context(patch.object(
                SystemInfo, "get_battery_path", return_value=BATTERY))
            stack.enter_context(patch.object(
                SystemInfo, "external_power_state", return_value=True))
            data = {
                BATTERY + "/status": "Not charging",
                BATTERY + "/capacity": "99",
                BATTERY + "/charge_control_end_threshold": "100",
            }
            stack.enter_context(patch.object(
                SystemInfo, "read_file", side_effect=lambda path: data.get(path)))
            stack.enter_context(patch.object(
                SystemInfo, "_battery_power_watts", return_value=0.0))
        report = system_info.generate_system_report()
        if report.battery_info.source != "upower":
            raise RuntimeError(
                "Not using mock private UPower bus; got "
                + report.battery_info.source)
        return report


def show_stats(report, scenario, check):
    import urwid
    from auto_cpufreq.modules.system_monitor import SystemMonitor, ViewType

    monitor = SystemMonitor(type=ViewType.STATS)
    monitor.format_system_info(report)
    print("\n===== --stats: SIMULATED UPOWER =====", flush=True)
    selected = []
    for item in monitor.right_content:
        widget = item.original_widget if isinstance(item, urwid.AttrMap) else item
        line = widget.get_text()[0]
        if line.startswith(("Battery", "AC plugged", "Charging start",
                            "Charging stop", "Charge protection",
                            "Supported controls", "UPower configured")) or "power:" in line.lower():
            print(line, flush=True)
            selected.append(line)
    result = "\n".join(selected)
    if check:
        expected = {
            "end-only": ("Supported controls: End threshold", "UPower configured limits: Stop: 80%"),
            "start-end": ("Supported controls: Start threshold, End threshold",
                          "UPower configured limits: Start: 75%, Stop: 80%"),
            "firmware": ("Supported controls: Firmware managed",),
            "legacy": ("Battery status: Fully charged",),
        }
        for needle in expected[scenario]:
            assert needle in result, (scenario, needle, result)
        if scenario in ("firmware", "legacy"):
            assert "UPower configured limits:" not in result
        if scenario == "legacy":
            assert "Charge protection:" not in result
        assert "Battery power: 0.00 W" in result


def show_gtk(report, scenario, check):
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    from auto_cpufreq.gui.app import SystemReportView

    view = SystemReportView()
    view.apply_report(report)
    window = Gtk.Window(title="SIMULATED UPower — read-only auto-cpufreq")
    window.set_default_size(1000, 680)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    note = Gtk.Label(label="SIMULATION — modern UPower responses, NOT real battery thresholds")
    outer.pack_start(note, False, False, 6)
    scroller = Gtk.ScrolledWindow()
    scroller.add(view)
    outer.pack_start(scroller, True, True, 0)
    window.add(outer)
    window.show_all()
    print("\n===== GTK: SIMULATED UPOWER =====", flush=True)
    for key in ("status", "charge", "ac", "start", "stop", "power"):
        print(key + ": " + view.battery_values[key].get_text(), flush=True)
    for key, value_label in view.battery_capability_values.items():
        print(f"{key}: visible={value_label.get_visible()}, "
              f"value={value_label.get_text()}", flush=True)

    if check:
        expected_visible = {
            "end-only": ("protection", "controls", "limits"),
            "start-end": ("protection", "controls", "limits"),
            "firmware": ("protection", "controls"),
            "legacy": (),
        }[scenario]
        for key in view.battery_capability_values:
            visible = view.battery_capability_values[key].get_visible()
            assert visible == (key in expected_visible), (scenario, key, visible)
        if scenario in ("end-only", "start-end"):
            assert "80%" in view.battery_capability_values["limits"].get_text()
        window.destroy()
    else:
        window.connect("destroy", Gtk.main_quit)
        Gtk.main()


def inside(scenario, mock_battery, gtk, check):
    insist_on_private_bus()
    command = [sys.executable, os.path.abspath(__file__), "serve", "--scenario", scenario]
    server = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    try:
        readable, _, _ = select.select([server.stdout], [], [], 8)
        if not readable:
            raise RuntimeError("Private mock service did not start within 8 seconds")
        ready = server.stdout.readline().strip()
        if ready != "PRIVATE MOCK READY: " + scenario:
            raise RuntimeError("Private mock failed: " + ready)
        print("READ-ONLY PRIVATE UPOWER LAB:", scenario, flush=True)
        args = [sys.executable, os.path.abspath(__file__), "inspect",
                "--scenario", scenario]
        if mock_battery:
            args.append("--mock-battery")
        if check:
            args.append("--check")
        subprocess.run(args, check=True)
        if gtk:
            args[2] = "gtk"
            subprocess.run(args, check=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def run_in_private_bus(scenario, mock_battery, gtk, check):
    if shutil.which("dbus-run-session") is None:
        raise RuntimeError("dbus-run-session not installed")
    env = os.environ.copy()
    env.pop("DBUS_SYSTEM_BUS_ADDRESS", None)
    env["UPOWER_LAB_PRIVATE"] = "yes"
    args = ["dbus-run-session", "--", sys.executable,
            os.path.abspath(__file__), "inside", "--scenario", scenario]
    if mock_battery:
        args.append("--mock-battery")
    if gtk:
        args.append("--gtk")
    if check:
        args.append("--check")
    subprocess.run(args, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(
        description="Private simulated UPower server for auto-cpufreq. Never writes hardware.")
    parser.add_argument("mode", choices=("run", "inside", "serve", "inspect", "gtk"))
    parser.add_argument("--scenario", choices=SCENARIOS, default="end-only")
    parser.add_argument("--mock-battery", action="store_true",
                        help="CI fixture for host without BAT1")
    parser.add_argument("--gtk", action="store_true", help="Open GTK read-only preview")
    parser.add_argument("--check", action="store_true", help="Automated assertions; no GTK loop")
    args = parser.parse_args()

    if args.mode == "run":
        run_in_private_bus(args.scenario, args.mock_battery, args.gtk, args.check)
        return
    if args.mode == "inside":
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
        inside(args.scenario, args.mock_battery, args.gtk, args.check)
        return
    insist_on_private_bus()
    if args.mode == "serve":
        asyncio.run(serve(args.scenario))
        return
    report = report_for_view(args.mock_battery)
    print("Data source:", report.battery_info.source, flush=True)
    if args.mode == "inspect":
        show_stats(report, args.scenario, args.check)
    else:
        show_gtk(report, args.scenario, args.check)


if __name__ == "__main__":
    main()
