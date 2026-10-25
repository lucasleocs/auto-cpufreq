"""Optional UPower behavior: read-only bus, no activation, safe policy fallback.

All buses and power_supply data are simulated; the real host is untouched.
"""
import asyncio
import configparser
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dbus_next import MessageFlag
from auto_cpufreq.battery_scripts import probe, priority
from auto_cpufreq.battery_scripts.battery import BatteryDaemonController
from auto_cpufreq.battery_scripts.conflicts import ChargeConflict
from auto_cpufreq.modules.battery_upower import _decode, _read_bus, read_upower_battery
from auto_cpufreq.modules.system_info import SystemInfo


PATH = "/sys/class/power_supply/BAT1"


def attributes(**updates):
    values = {
        "Type": 2, "IsPresent": True, "PowerSupply": True,
        "NativePath": "BAT1", "State": 2, "Percentage": 61.0,
        "EnergyRate": 9.5, "ChargeThresholdSupported": True,
        "ChargeThresholdEnabled": False,
        "ChargeThresholdSettingsSupported": 2, "ChargeEndThreshold": 80,
    }
    values.update(updates)
    return values


class Message:
    def __init__(self, *, destination=None, member, flags=None, **kwargs):
        self.destination = destination
        self.member = member
        self.flags = flags


class MockBus:
    def __init__(self, *, owner=True, activatable=True, properties=None,
                 disconnect_after_owner=False, bus_error=False):
        self.owner = owner
        self.activatable = activatable
        self.properties = attributes() if properties is None else properties
        self.disconnect_after_owner = disconnect_after_owner
        self.bus_error = bus_error
        self.requests = []

    async def call(self, message):
        self.requests.append(message)
        if self.bus_error:
            raise ConnectionError("no system bus")
        if message.member == "NameHasOwner":
            return SimpleNamespace(message_type="ok", body=[self.owner])
        if message.member == "ListActivatableNames":
            return SimpleNamespace(message_type="ok", body=[
                ["org.freedesktop.UPower"] if self.activatable else []
            ])
        if message.member == "EnumerateDevices":
            if self.disconnect_after_owner:
                return SimpleNamespace(message_type="error", body=[])
            return SimpleNamespace(message_type="ok", body=[
                ["/org/freedesktop/UPower/devices/battery_BAT1"]
            ])
        if message.member == "GetAll":
            return SimpleNamespace(message_type="ok", body=[
                {k: SimpleNamespace(value=v) for k,v in self.properties.items()}
            ])
        raise AssertionError(message.member)


def wrapped(bus):
    return asyncio.run(probe._probe_bus(
        bus, PATH, Message, SimpleNamespace(METHOD_RETURN="ok")
    ))


class OptionalUPowerTests(unittest.TestCase):
    def test_telemetry_never_activates_upower(self):
        bus = MockBus()
        value = asyncio.run(_read_bus(
            bus, PATH, Message, SimpleNamespace(METHOD_RETURN="ok")
        ))
        self.assertIsNotNone(value)
        self.assertEqual([m.member for m in bus.requests],
                         ["EnumerateDevices", "GetAll"])
        self.assertTrue(all(
            m.flags == MessageFlag.NO_AUTOSTART for m in bus.requests
        ))

    def test_telemetry_failure_and_absence_fall_back_to_sysfs(self):
        observed = {
            "status": "Discharging", "capacity": "61",
            "charge_control_end_threshold": "80",
        }
        with patch.object(SystemInfo, "get_battery_path", return_value=PATH), \
             patch.object(SystemInfo, "read_file",
                          side_effect=lambda p: observed.get(os.path.basename(p))), \
             patch.object(SystemInfo, "external_power_state", return_value=False), \
             patch.object(SystemInfo, "_battery_power_watts", return_value=9.0), \
             patch("auto_cpufreq.modules.system_info.read_upower_battery",
                   return_value=None):
            info = SystemInfo.battery_info()
        self.assertEqual(info.source, "sysfs")
        self.assertEqual(info.battery_level, 61)
        self.assertEqual(info.power_watts, 9.0)
        self.assertEqual(info.charging_stop_threshold, 80)

    def test_healthy_modern_upower_is_optional_telemetry_overlay(self):
        output = _decode(attributes(), PATH)
        self.assertIsNotNone(output)
        self.assertEqual(output.percentage, 61)
        self.assertFalse(output.charge_protection_enabled)

    def test_older_upower_without_charge_api_preserves_telemetry(self):
        old = attributes()
        for name in tuple(old):
            if name.startswith("Charge"):
                old.pop(name)
        result = _decode(old, PATH)
        self.assertIsNotNone(result)
        self.assertFalse(result.charge_protection_supported)

    def test_bus_confirms_upower_absent_allows_kernel_ownership(self):
        bus = MockBus(owner=False, activatable=False)
        state = wrapped(bus)
        self.assertEqual(state.status, "absent")
        with patch.object(priority, "probe_charge_service", return_value=state):
            decision = priority.resolve_charge_control(PATH, 80)
        self.assertEqual(decision.action, "apply")
        self.assertEqual([x.member for x in bus.requests],
                         ["NameHasOwner", "ListActivatableNames"])

    def test_activatable_but_stopped_upower_blocks_policy(self):
        bus = MockBus(owner=False, activatable=True)
        state = wrapped(bus)
        self.assertEqual(state.status, "inactive")
        with patch.object(priority, "probe_charge_service", return_value=state):
            decision = priority.resolve_charge_control(PATH, 80)
        self.assertEqual(decision.action, "blocked")
        self.assertFalse(decision.retryable)

    def test_missing_system_bus_fails_closed(self):
        bus = MockBus(bus_error=True)
        state = wrapped(bus)
        self.assertEqual(state.status, "unavailable")
        with patch.object(priority, "probe_charge_service", return_value=state):
            decision = priority.resolve_charge_control(PATH, 80)
        self.assertEqual(decision.action, "blocked")
        self.assertTrue(decision.retryable)

    def test_probe_must_not_restart_upower_after_owner_race(self):
        bus = MockBus(owner=True, disconnect_after_owner=True)
        state = wrapped(bus)
        self.assertEqual(state.status, "unavailable")
        self.assertEqual([m.member for m in bus.requests],
                         ["NameHasOwner", "EnumerateDevices"])
        self.assertEqual(bus.requests[1].flags, MessageFlag.NO_AUTOSTART)

    def test_probe_registry_operations_do_not_target_upower(self):
        bus = MockBus(owner=True)
        state = wrapped(bus)
        self.assertEqual(state.status, "ready")
        for msg in bus.requests:
            if msg.destination == "org.freedesktop.UPower":
                self.assertEqual(msg.flags, MessageFlag.NO_AUTOSTART)

    def test_synchronous_failed_bus_is_nonfatal_to_telemetry(self):
        from dbus_next.aio import MessageBus
        with patch.object(MessageBus, "connect",
                          side_effect=ConnectionError("system bus unavailable")):
            reading = read_upower_battery(PATH, timeout=.1)
            safety = probe.probe_charge_service(PATH, timeout=.1)
        self.assertIsNone(reading)
        self.assertEqual(safety.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
