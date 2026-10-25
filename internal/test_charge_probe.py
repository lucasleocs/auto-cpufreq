"""UPower D-Bus registry/capability checks with shaped private-bus responses."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from auto_cpufreq.battery_scripts.probe import (
    _charge_capabilities, _probe_bus, ChargeServiceProbe,
)
from auto_cpufreq.modules.battery_upower import UPOWER_NAME


class FakeMessage:
    def __init__(self, *, destination, path, interface, member,
                 signature="", body=None, flags=None):
        self.flags=flags
        self.destination=destination
        self.path=path
        self.interface=interface
        self.member=member
        self.signature=signature
        self.body=body or []


@dataclass
class Reply:
    body: list
    message_type: str="method_return"


class FakeBus:
    def __init__(self, *, owner=True, activatable=True, properties=None, broken=None):
        self.owner=owner
        self.activatable=activatable
        self.properties=properties or base_props()
        self.broken=broken
        self.calls=[]

    async def call(self, req):
        self.calls.append(req.member)
        if req.member == self.broken:
            return Reply([], "error")
        if req.member == "NameHasOwner":
            return Reply([self.owner])
        if req.member == "ListActivatableNames":
            return Reply([[UPOWER_NAME] if self.activatable else []])
        if req.member == "EnumerateDevices":
            return Reply([["/org/freedesktop/UPower/devices/battery_BAT1"]])
        if req.member == "GetAll":
            return Reply([{name: SimpleNamespace(value=value)
                           for name,value in self.properties.items()}])
        raise AssertionError(req.member)


def base_props():
    return {
        "Type":2, "PowerSupply":True, "IsPresent":True,
        "NativePath":"BAT1", "ChargeThresholdSupported":True,
        "ChargeThresholdEnabled":True,
        "ChargeThresholdSettingsSupported":2,
        "ChargeEndThreshold":80,
    }


def probe(fake):
    return asyncio.run(_probe_bus(
        fake, "/sys/class/power_supply/BAT1", FakeMessage,
        SimpleNamespace(METHOD_RETURN="method_return"),
    ))


class ProbeTests(unittest.TestCase):
    def test_missing_and_nonactivatable_is_only_confirmed_absence(self):
        bus=FakeBus(owner=False,activatable=False)
        value=probe(bus)
        self.assertEqual(value.status,"absent")
        self.assertEqual(bus.calls,["NameHasOwner","ListActivatableNames"])

    def test_unowned_but_activatable_blocks_fallback(self):
        value=probe(FakeBus(owner=False,activatable=True))
        self.assertEqual(value.status,"unavailable")

    def test_owned_healthy_threshold_status(self):
        p=probe(FakeBus())
        self.assertEqual(p.status,"ready")
        self.assertTrue(p.capabilities.charge_protection_supported)
        self.assertEqual(p.capabilities.configured_end_threshold,80)

    def test_old_api_with_no_threshold_properties_is_unsupported(self):
        props=base_props()
        for k in list(props):
            if k.startswith("Charge"):
                props.pop(k)
        p=probe(FakeBus(properties=props))
        self.assertEqual(p.status,"ready")
        self.assertFalse(p.capabilities.charge_protection_supported)

    def test_dbus_error_is_unavailable_not_absence(self):
        p=probe(FakeBus(broken="NameHasOwner"))
        self.assertEqual(p.status,"unavailable")

    def test_name_owner_disappears_during_enumerate(self):
        p=probe(FakeBus(broken="EnumerateDevices"))
        self.assertEqual(p.status,"unavailable")

    def test_no_matching_system_battery_is_unavailable(self):
        props=base_props()
        props["NativePath"]="BAT0"
        p=probe(FakeBus(properties=props))
        self.assertEqual(p.status,"unavailable")

    def test_telemetry_not_required_for_capability_probe(self):
        p=probe(FakeBus(properties=base_props()))
        self.assertEqual(p.status,"ready")
        self.assertIsNotNone(p.capabilities)
        self.assertNotIn("Percentage",base_props())

    def test_supported_with_unknown_enabled_keeps_ambiguous_state(self):
        props=base_props()
        props.pop("ChargeThresholdEnabled")
        p=probe(FakeBus(properties=props))
        self.assertEqual(p.status,"ready")
        self.assertIsNone(p.capabilities.charge_protection_enabled)

    def test_invalid_supported_property_causes_unavailable(self):
        props=base_props()
        props["ChargeThresholdSupported"]="yes"
        p=probe(FakeBus(properties=props))
        self.assertEqual(p.status,"unavailable")

    def test_firmware_only_no_numeric_threshold_inferred(self):
        props=base_props()
        props["ChargeThresholdSettingsSupported"]=4
        props.pop("ChargeEndThreshold")
        p=probe(FakeBus(properties=props))
        self.assertEqual(p.capabilities.charge_control_types,("Firmware managed",))
        self.assertIsNone(p.capabilities.configured_end_threshold)
