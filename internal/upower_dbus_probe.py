#!/usr/bin/env python3
"""Read-only UPower D-Bus transport experiment with a sysfs fallback."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path

POWER_SUPPLY_DIR = Path("/sys/class/power_supply")
UPOWER_NAME = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
UPOWER_DEVICE = "org.freedesktop.UPower.Device"
DBUS_PROPERTIES = "org.freedesktop.DBus.Properties"

STATES = {
    1: "Charging",
    2: "Discharging",
    3: "Empty",
    4: "Fully charged",
    5: "Pending charge",
    6: "Pending discharge",
}
THRESHOLD_FLAGS = {1: "Start threshold", 2: "End threshold", 4: "Firmware managed"}


def native_matches(native_path: object, selected_path: Path) -> bool:
    if not isinstance(native_path, str) or not native_path:
        return False
    if "/" not in native_path:
        return native_path == selected_path.name
    return os.path.realpath(native_path) == os.path.realpath(selected_path)


def percentage(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    if not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return float(value)


def threshold_metadata(properties: dict) -> dict | None:
    if properties.get("ChargeThresholdSupported") is not True:
        return None
    enabled = properties.get("ChargeThresholdEnabled")
    if type(enabled) is not bool:
        enabled = None
    mask = properties.get("ChargeThresholdSettingsSupported")
    if type(mask) is not int or mask < 0:
        mask = None
    start = percentage(properties.get("ChargeStartThreshold")) if mask is not None and mask & 1 else None
    end = percentage(properties.get("ChargeEndThreshold")) if mask is not None and mask & 2 else None
    return {
        "enabled": enabled,
        "control_types": [name for bit, name in THRESHOLD_FLAGS.items() if mask is not None and mask & bit],
        "start_threshold": int(start) if start is not None else None,
        "end_threshold": int(end) if end is not None else None,
    }


def decode_device(properties: dict, selected_path: Path) -> dict | None:
    if properties.get("Type") != 2:
        return None
    if properties.get("PowerSupply") is not True or properties.get("IsPresent") is not True:
        return None
    if not native_matches(properties.get("NativePath"), selected_path):
        return None

    state = properties.get("State")
    pct = percentage(properties.get("Percentage"))
    power = properties.get("EnergyRate")
    if type(state) is not int or state not in STATES:
        return None
    if pct is None or type(power) not in (int, float) or not math.isfinite(power) or power < 0:
        return None

    return {
        "source": "upower",
        "status": STATES[state],
        "percentage": pct,
        "power_watts": float(power),
        "battery": selected_path.name,
        "threshold_capabilities": threshold_metadata(properties),
        "update_time": properties.get("UpdateTime") if type(properties.get("UpdateTime")) is int else None,
    }


def battery_path(name: str | None, supply_dir: Path = POWER_SUPPLY_DIR) -> Path | None:
    if name:
        if name in (".", "..") or Path(name).name != name:
            return None
        path = supply_dir / name
        return path if _read(path / "type") == "Battery" else None
    try:
        for path in sorted(supply_dir.iterdir()):
            if _read(path / "type") == "Battery":
                return path
    except OSError:
        pass
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def sysfs_snapshot(path: Path | None) -> dict:
    if path is None:
        return {"source": "unavailable", "status": "Unavailable"}
    status = _read(path / "status") or "Unknown"
    capacity = _read(path / "capacity")
    power_now = _read(path / "power_now")
    current_now = _read(path / "current_now")
    voltage_now = _read(path / "voltage_now")
    try:
        if power_now is not None:
            watts = abs(int(power_now)) / 1_000_000
        elif current_now is not None and voltage_now is not None:
            watts = abs(int(current_now) * int(voltage_now)) / 1_000_000_000_000
        else:
            watts = None
    except ValueError:
        watts = None
    try:
        level = percentage(int(capacity)) if capacity is not None else None
    except ValueError:
        level = None
    return {
        "source": "sysfs",
        "status": status,
        "percentage": level,
        "power_watts": watts,
        "battery": path.name,
    }


async def read_from_bus(bus, selected: Path, message_cls, message_type) -> dict | None:
    enumerate_reply = await bus.call(message_cls(
        destination=UPOWER_NAME,
        path=UPOWER_PATH,
        interface=UPOWER_NAME,
        member="EnumerateDevices",
    ))
    if enumerate_reply is None or enumerate_reply.message_type != message_type.METHOD_RETURN:
        return None
    if not enumerate_reply.body or not isinstance(enumerate_reply.body[0], list):
        return None

    for device_path in enumerate_reply.body[0]:
        if not isinstance(device_path, str) or device_path == UPOWER_PATH + "/devices/DisplayDevice":
            continue
        reply = await bus.call(message_cls(
            destination=UPOWER_NAME,
            path=device_path,
            interface=DBUS_PROPERTIES,
            member="GetAll",
            signature="s",
            body=[UPOWER_DEVICE],
        ))
        if reply is None or reply.message_type != message_type.METHOD_RETURN:
            continue
        if not reply.body or not isinstance(reply.body[0], dict):
            continue
        properties = {name: item.value for name, item in reply.body[0].items()}
        result = decode_device(properties, selected)
        if result is not None:
            return result
    return None


async def upower_snapshot(selected: Path, timeout: float) -> dict | None:
    from dbus_next import BusType, Message, MessageType
    from dbus_next.aio import MessageBus

    async def collect():
        bus = MessageBus(bus_type=BusType.SYSTEM)
        try:
            await bus.connect()
            return await read_from_bus(bus, selected, Message, MessageType)
        finally:
            bus.disconnect()

    return await asyncio.wait_for(collect(), timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read UPower if available; otherwise read sysfs. Never writes settings.")
    parser.add_argument("--battery", help="Power supply battery name, e.g. BAT1")
    parser.add_argument("--timeout", type=float, default=2.0, help="Maximum seconds for the complete D-Bus read")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0.05 <= args.timeout <= 15:
        parser.error("--timeout must be between 0.05 and 15 seconds")
    selected = battery_path(args.battery)
    result = None
    if selected is not None:
        try:
            result = asyncio.run(upower_snapshot(selected, args.timeout))
        except (ImportError, OSError, Exception):
            result = None
    print(json.dumps(result or sysfs_snapshot(selected), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
