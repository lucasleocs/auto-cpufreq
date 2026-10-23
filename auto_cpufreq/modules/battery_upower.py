"""Optional, read-only UPower battery information through the system D-Bus."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import os

UPOWER_NAME = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
DEVICE_INTERFACE = "org.freedesktop.UPower.Device"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

STATES = {
    1: "Charging",
    2: "Discharging",
    3: "Empty",
    4: "Fully charged",
    5: "Pending charge",
    6: "Pending discharge",
}
CONTROLS = ((1, "Start threshold"), (2, "End threshold"), (4, "Firmware managed"))


@dataclass(frozen=True)
class UPowerBattery:
    status: str
    percentage: int
    power_watts: float
    charge_protection_enabled: bool | None = None
    charge_control_types: tuple[str, ...] = ()
    charge_protection_supported: bool = False


def _match_battery(native_path: object, sysfs_path: str) -> bool:
    if not isinstance(native_path, str) or not native_path:
        return False
    if "/" not in native_path:
        return native_path == os.path.basename(sysfs_path)
    return os.path.realpath(native_path) == os.path.realpath(sysfs_path)


def _valid_percent(value: object) -> bool:
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 100


def _decode(properties: dict, sysfs_path: str) -> UPowerBattery | None:
    if type(properties.get("Type")) is not int or properties["Type"] != 2:
        return None
    if properties.get("PowerSupply") is not True or properties.get("IsPresent") is not True:
        return None
    if not _match_battery(properties.get("NativePath"), sysfs_path):
        return None

    state = properties.get("State")
    percentage = properties.get("Percentage")
    rate = properties.get("EnergyRate")
    if type(state) is not int or state not in STATES:
        return None
    if not _valid_percent(percentage):
        return None
    if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
        return None

    supported = properties.get("ChargeThresholdSupported") is True
    enabled = properties.get("ChargeThresholdEnabled") if supported else None
    if type(enabled) is not bool:
        enabled = None

    mask = properties.get("ChargeThresholdSettingsSupported")
    control_types = (
        tuple(label for flag, label in CONTROLS if mask & flag)
        if supported and type(mask) is int and mask >= 0
        else ()
    )
    return UPowerBattery(
        status=STATES[state],
        percentage=int(round(percentage)),
        power_watts=float(rate),
        charge_protection_enabled=enabled,
        charge_control_types=control_types,
        charge_protection_supported=supported,
    )


async def _read_bus(bus, sysfs_path, message_cls, message_type):
    response = await bus.call(message_cls(
        destination=UPOWER_NAME, path=UPOWER_PATH,
        interface=UPOWER_NAME, member="EnumerateDevices",
    ))
    if not response or response.message_type != message_type.METHOD_RETURN:
        return None
    if not response.body or not isinstance(response.body[0], list):
        return None

    for device_path in response.body[0]:
        if not isinstance(device_path, str) or device_path == UPOWER_PATH + "/devices/DisplayDevice":
            continue
        reply = await bus.call(message_cls(
            destination=UPOWER_NAME, path=device_path,
            interface=PROPERTIES_INTERFACE, member="GetAll",
            signature="s", body=[DEVICE_INTERFACE],
        ))
        if not reply or reply.message_type != message_type.METHOD_RETURN:
            continue
        if not reply.body or not isinstance(reply.body[0], dict):
            continue
        props = {name: variant.value for name, variant in reply.body[0].items()}
        snapshot = _decode(props, sysfs_path)
        if snapshot is not None:
            return snapshot
    return None


async def _read_upower(sysfs_path: str, timeout: float) -> UPowerBattery | None:
    from dbus_next import BusType, Message, MessageType
    from dbus_next.aio import MessageBus

    async def read():
        bus = MessageBus(bus_type=BusType.SYSTEM)
        try:
            await bus.connect()
            return await _read_bus(bus, sysfs_path, Message, MessageType)
        finally:
            bus.disconnect()

    return await asyncio.wait_for(read(), timeout=timeout)


def read_upower_battery(sysfs_path: str, timeout: float = 1.5) -> UPowerBattery | None:
    """Return an optional physical battery snapshot, never modifying power policy."""
    try:
        return asyncio.run(_read_upower(sysfs_path, timeout))
    except Exception:
        return None
