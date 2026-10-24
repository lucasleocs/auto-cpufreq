"""Read-only UPower charge-control discovery independent of telemetry.

The probe consults the system bus registry before deciding the service is
truly absent. No D-Bus activation or charge-changing methods are issued.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os

from auto_cpufreq.modules.battery_upower import (
    UPOWER_NAME, UPOWER_PATH, DEVICE_INTERFACE, PROPERTIES_INTERFACE,
    _match_battery,
)


@dataclass(frozen=True)
class ChargeCapabilities:
    charge_protection_supported: bool
    charge_protection_enabled: bool | None = None
    charge_control_types: tuple[str, ...] = ()
    configured_start_threshold: int | None = None
    configured_end_threshold: int | None = None


@dataclass(frozen=True)
class ChargeServiceProbe:
    status: str  # "absent", "ready", "unavailable"
    capabilities: ChargeCapabilities | None = None
    reason: str = ""


CONTROLS = ((1, "Start threshold"), (2, "End threshold"),
            (4, "Firmware managed"))


def _charge_capabilities(properties: dict) -> ChargeCapabilities | None:
    # Identify a real system battery independently of percentage/energy rate.
    supported = properties.get("ChargeThresholdSupported")
    if supported is None:
        # Valid older UPower API that did not expose charge-limit control.
        return ChargeCapabilities(charge_protection_supported=False)
    if type(supported) is not bool:
        return None
    if not supported:
        return ChargeCapabilities(charge_protection_supported=False)

    enabled = properties.get("ChargeThresholdEnabled")
    if type(enabled) is not bool:
        enabled = None

    mask = properties.get("ChargeThresholdSettingsSupported")
    if type(mask) is not int or mask < 0:
        mask = 0

    controls = tuple(name for bit, name in CONTROLS if mask & bit)

    def percent(prop, bit):
        val = properties.get(prop)
        if mask & bit and type(val) is int and 0 <= val <= 100:
            return val
        return None

    return ChargeCapabilities(
        charge_protection_supported=True,
        charge_protection_enabled=enabled,
        charge_control_types=controls,
        configured_start_threshold=percent("ChargeStartThreshold", 1),
        configured_end_threshold=percent("ChargeEndThreshold", 2),
    )


async def _probe_bus(bus, battery_path, message, message_type) -> ChargeServiceProbe:
    async def call(destination, path, interface, member, signature="", body=None):
        result = await bus.call(message(
            destination=destination, path=path, interface=interface,
            member=member, signature=signature, body=body or [],
        ))
        if (result is None or result.message_type != message_type.METHOD_RETURN
                or not isinstance(result.body, list)):
            raise ValueError(f"Invalid D-Bus reply for {member}")
        return result.body

    try:
        owner = await call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", "s", [UPOWER_NAME])
        if len(owner) != 1 or type(owner[0]) is not bool:
            raise ValueError("Invalid NameHasOwner response")
        if not owner[0]:
            activatable = await call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "ListActivatableNames")
            if (len(activatable) != 1 or not isinstance(activatable[0], list)
                    or not all(isinstance(name, str) for name in activatable[0])):
                raise ValueError("Invalid ListActivatableNames response")
            if UPOWER_NAME not in activatable[0]:
                return ChargeServiceProbe("absent", reason="UPower not owned or activatable")
            return ChargeServiceProbe("unavailable", reason="UPower is activatable but not running")

        devices = await call(UPOWER_NAME, UPOWER_PATH, UPOWER_NAME, "EnumerateDevices")
        if len(devices) != 1 or not isinstance(devices[0], list):
            raise ValueError("Invalid EnumerateDevices response")
        for path in devices[0]:
            if not isinstance(path, str):
                continue
            props = await call(
                UPOWER_NAME, path, PROPERTIES_INTERFACE, "GetAll",
                "s", [DEVICE_INTERFACE],
            )
            if len(props) != 1 or not isinstance(props[0], dict):
                continue
            properties = {name: val.value for name, val in props[0].items()
                          if isinstance(name, str) and hasattr(val, "value")}
            if (type(properties.get("Type")) is not int
                    or properties.get("Type") != 2
                    or properties.get("PowerSupply") is not True
                    or properties.get("IsPresent") is not True
                    or not _match_battery(properties.get("NativePath"), battery_path)):
                continue
            capabilities = _charge_capabilities(properties)
            if capabilities is None:
                raise ValueError("Invalid UPower charging capability property")
            return ChargeServiceProbe("ready", capabilities=capabilities)

        return ChargeServiceProbe("unavailable", reason="UPower did not identify the selected system battery")
    except Exception as exc:
        return ChargeServiceProbe("unavailable", reason=type(exc).__name__)


async def _probe_system(battery_path: str, timeout: float) -> ChargeServiceProbe:
    from dbus_next import BusType, Message, MessageType
    from dbus_next.aio import MessageBus

    async def read():
        bus = MessageBus(bus_type=BusType.SYSTEM)
        try:
            await bus.connect()
            return await _probe_bus(bus, battery_path, Message, MessageType)
        finally:
            bus.disconnect()

    return await asyncio.wait_for(read(), timeout)


def probe_charge_service(
    battery_path: str, timeout: float = 1.5,
) -> ChargeServiceProbe:
    """Probe charge capabilities; any connectivity failure is NOT absence."""
    try:
        return asyncio.run(_probe_system(battery_path, timeout))
    except Exception as exc:
        return ChargeServiceProbe("unavailable", reason=type(exc).__name__)
