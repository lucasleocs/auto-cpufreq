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
    5: "Not charging",
    6: "Pending discharge",
}
CONTROLS = ((1, "Start threshold"), (2, "End threshold"), (4, "Firmware managed"))


@dataclass(frozen=True)
class ChargeCapabilities:
    """Decoded UPower policy, independent of the telemetry sampling path."""
    charge_protection_supported: bool
    charge_protection_enabled: bool | None = None
    charge_control_types: tuple[str, ...] = ()
    configured_start_threshold: int | None = None
    configured_end_threshold: int | None = None


def decode_charge_capabilities(properties: dict) -> ChargeCapabilities | None:
    """Decode the charge-limit API shared by telemetry and write arbitration.

    None means invalid/ambiguous advertised capability; a missing support
    property is a valid older UPower API. Neither condition authorizes writes.
    """
    supported = properties.get("ChargeThresholdSupported")
    if supported is None:
        return ChargeCapabilities(charge_protection_supported=False)
    if type(supported) is not bool:
        return None
    if supported is False:
        return ChargeCapabilities(charge_protection_supported=False)

    enabled = properties.get("ChargeThresholdEnabled")
    if type(enabled) is not bool:
        enabled = None

    mask = properties.get("ChargeThresholdSettingsSupported")
    if mask is None:
        # This bitmask is newer than the basic charging-threshold API.
        mask = 0
    elif type(mask) is not int or mask < 0:
        return None

    controls = tuple(label for bit, label in CONTROLS if mask & bit)

    def configured(prop: str, flag: int) -> int | None:
        value = properties.get(prop)
        return (
            value if mask & flag and type(value) is int and 0 <= value <= 100
            else None
        )

    return ChargeCapabilities(
        charge_protection_supported=True,
        charge_protection_enabled=enabled,
        charge_control_types=controls,
        configured_start_threshold=configured("ChargeStartThreshold", 1),
        configured_end_threshold=configured("ChargeEndThreshold", 2),
    )


@dataclass(frozen=True)
class UPowerBattery:
    status: str
    percentage: int
    power_watts: float | None
    charge_protection_enabled: bool | None = None
    charge_control_types: tuple[str, ...] = ()
    charge_protection_supported: bool | None = False
    configured_start_threshold: int | None = None
    configured_end_threshold: int | None = None


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
    # Older UPower versions and transient providers may omit the energy
    # rate. Keep the valid battery identity/state/percentage in that case,
    # allowing the caller to retain a kernel power reading.
    rate_watts = abs(float(rate)) if (
        type(rate) in (int, float) and math.isfinite(rate)
    ) else None

    capabilities = decode_charge_capabilities(properties)
    # Preserve useful telemetry even if charge-control properties are invalid.
    # The ownership probe independently fails closed on the same condition.
    return UPowerBattery(
        status=STATES[state],
        percentage=int(round(percentage)),
        power_watts=rate_watts,
        charge_protection_enabled=(
            capabilities.charge_protection_enabled if capabilities else None
        ),
        charge_control_types=capabilities.charge_control_types if capabilities else (),
        charge_protection_supported=(
            capabilities.charge_protection_supported if capabilities else None
        ),
        configured_start_threshold=(
            capabilities.configured_start_threshold if capabilities else None
        ),
        configured_end_threshold=(
            capabilities.configured_end_threshold if capabilities else None
        ),
    )


async def _read_bus(bus, sysfs_path, message_cls, message_type):
    # The monitor is an optional observer: never activate a stopped UPower.
    from dbus_next import MessageFlag

    response = await bus.call(message_cls(
        destination=UPOWER_NAME, path=UPOWER_PATH,
        interface=UPOWER_NAME, member="EnumerateDevices",
        flags=MessageFlag.NO_AUTOSTART,
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
            flags=MessageFlag.NO_AUTOSTART,
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
