"""Offline, read-only contract checks for the UPower transport spike."""

import asyncio
import importlib.util
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("upower_probe", HERE / "upower_dbus_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Variant:
    def __init__(self, value):
        self.value = value


class Message:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class MessageType:
    METHOD_RETURN = 2
    ERROR = 3


class Reply:
    def __init__(self, value, message_type=2):
        self.message_type = message_type
        self.body = [value]


class FakeBus:
    def __init__(self, paths, properties):
        self.paths = paths
        self.properties = properties
        self.calls = []

    async def call(self, message):
        q = message.kwargs
        self.calls.append((q["member"], q["path"]))
        if q["member"] == "EnumerateDevices":
            return Reply(self.paths)
        return Reply({k: Variant(v) for k, v in self.properties[q["path"]].items()})


BAT1 = Path("/sys/class/power_supply/BAT1")
BAT0 = Path("/sys/class/power_supply/BAT0")
OBJECT = "/org/freedesktop/UPower/devices/battery_BAT1"


def properties(**kwargs):
    d = dict(
        Type=2, PowerSupply=True, IsPresent=True,
        NativePath="BAT1", State=4, Percentage=99.0, EnergyRate=0.0,
        UpdateTime=1792799460,
    )
    d.update(kwargs)
    return d


class UpowerProbeTests(unittest.TestCase):
    def test_fully_charged_upower_at_99_percent_zero_watts(self):
        got = probe.decode_device(properties(), BAT1)
        self.assertEqual(got["source"], "upower")
        self.assertEqual(got["status"], "Fully charged")
        self.assertEqual(got["percentage"], 99)
        self.assertEqual(got["power_watts"], 0)
        self.assertIsNone(got["threshold_capabilities"])

    def test_all_power_states(self):
        for key, name in probe.STATES.items():
            with self.subTest(key=key):
                self.assertEqual(probe.decode_device(properties(State=key), BAT1)["status"], name)

    def test_unknown_state_does_not_override_sysfs(self):
        self.assertIsNone(probe.decode_device(properties(State=0), BAT1))

    def test_wrong_or_peripheral_battery(self):
        for updates in [dict(Type=3), dict(PowerSupply=False), dict(IsPresent=False), dict(NativePath="BAT0")]:
            with self.subTest(updates=updates):
                self.assertIsNone(probe.decode_device(properties(**updates), BAT1))

    def test_physical_native_path_matching(self):
        self.assertTrue(probe.native_matches("/sys/class/power_supply/BAT1", BAT1))
        self.assertTrue(probe.native_matches("BAT1", BAT1))
        self.assertFalse(probe.native_matches("/sys/class/power_supply/BAT0", BAT1))
        self.assertFalse(probe.native_matches("USB_BAT1", BAT1))

    def test_bad_percentages_and_power(self):
        for value in [-1, 101, float("nan"), float("inf"), "99", True]:
            with self.subTest(value=value):
                self.assertIsNone(probe.decode_device(properties(Percentage=value), BAT1))
        for value in [-0.1, float("nan"), float("inf"), "0", True]:
            with self.subTest(value=value):
                self.assertIsNone(probe.decode_device(properties(EnergyRate=value), BAT1))

    def test_threshold_mask_and_enabled(self):
        for mask, expected in [
            (1, ["Start threshold"]),
            (2, ["End threshold"]),
            (3, ["Start threshold", "End threshold"]),
            (4, ["Firmware managed"]),
            (6, ["End threshold", "Firmware managed"]),
        ]:
            with self.subTest(mask=mask):
                t = probe.threshold_metadata(properties(
                    ChargeThresholdSupported=True, ChargeThresholdEnabled=False,
                    ChargeThresholdSettingsSupported=mask,
                    ChargeStartThreshold=75, ChargeEndThreshold=80,
                ))
                self.assertEqual(t["control_types"], expected)
                self.assertEqual(t["start_threshold"], 75 if mask & 1 else None)
                self.assertEqual(t["end_threshold"], 80 if mask & 2 else None)
                self.assertIs(t["enabled"], False)

    def test_old_daemon_omitted_threshold_api(self):
        self.assertIsNone(probe.threshold_metadata(properties()))
        t = probe.threshold_metadata(properties(ChargeThresholdSupported=True))
        self.assertEqual(t["control_types"], [])
        self.assertIsNone(t["enabled"])

    def test_sentinel_and_false_support(self):
        self.assertIsNone(probe.threshold_metadata(properties(ChargeThresholdSupported=False)))
        t = probe.threshold_metadata(properties(
            ChargeThresholdSupported=True, ChargeThresholdSettingsSupported=3,
            ChargeStartThreshold=4294967295, ChargeEndThreshold=4294967295,
        ))
        self.assertIsNone(t["start_threshold"])
        self.assertIsNone(t["end_threshold"])

    def test_transport_finds_right_battery_and_skips_aggregate(self):
        paths = ["/org/freedesktop/UPower/devices/DisplayDevice",
                 "/org/freedesktop/UPower/devices/battery_BAT0", OBJECT]
        bus = FakeBus(paths, {
            paths[1]: properties(NativePath="BAT0", State=2),
            OBJECT: properties(),
        })
        result = asyncio.run(probe.read_from_bus(bus, BAT1, Message, MessageType))
        self.assertEqual(result["status"], "Fully charged")
        self.assertEqual(len(bus.calls), 3)
        self.assertNotIn(("GetAll", paths[0]), bus.calls)

    def test_transport_no_match_returns_none(self):
        bus = FakeBus([OBJECT], {OBJECT: properties(NativePath="BAT0")})
        self.assertIsNone(asyncio.run(probe.read_from_bus(bus, BAT1, Message, MessageType)))

    def test_sysfs_fallback_zero_and_current_voltage(self):
        with TemporaryDirectory() as directory:
            p = Path(directory) / "BAT1"
            p.mkdir()
            (p / "type").write_text("Battery")
            (p / "status").write_text("Not charging")
            (p / "capacity").write_text("99")
            (p / "current_now").write_text("0")
            (p / "voltage_now").write_text("16887000")
            self.assertEqual(probe.battery_path("BAT1", Path(directory)), p)
            out = probe.sysfs_snapshot(p)
            self.assertEqual(out["power_watts"], 0)
            (p / "current_now").write_text("24000")
            self.assertAlmostEqual(probe.sysfs_snapshot(p)["power_watts"], 0.405288)

    def test_invalid_battery_names(self):
        with TemporaryDirectory() as directory:
            self.assertIsNone(probe.battery_path("../BAT1", Path(directory)))
            self.assertIsNone(probe.battery_path("not-here", Path(directory)))


if __name__ == "__main__":
    unittest.main()
