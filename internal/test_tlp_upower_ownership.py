"""Isolated TLP/UPower compatibility and ownership checks."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from dbus_next import MessageFlag
from auto_cpufreq.tlp_stat_parser import TLPStatusParser
from auto_cpufreq.battery_scripts import conflicts, priority
from auto_cpufreq.battery_scripts.probe import _probe_bus
from auto_cpufreq.modules.battery_upower import decode_charge_capabilities, _decode
from test_charge_probe import FakeBus, FakeMessage, Reply, base_props


PATH = "/sys/class/power_supply/BAT1"


def probe_bus(bus):
    return asyncio.run(_probe_bus(
        bus, PATH, FakeMessage,
        SimpleNamespace(METHOD_RETURN="method_return"),
    ))


class TLPStatusTests(unittest.TestCase):
    def test_modern_enabled_including_last_run(self):
        state = TLPStatusParser("+++ TLP Status\ntlp = enabled, last run: 5 min ago\n")
        self.assertEqual(state.state(), "enabled")
        self.assertTrue(state.is_enabled())

    def test_modern_disabled(self):
        self.assertEqual(TLPStatusParser("tlp = disabled").state(), "disabled")

    def test_legacy_state_field_supported(self):
        self.assertEqual(TLPStatusParser("State = enabled").state(), "enabled")
        self.assertTrue(TLPStatusParser("State = enabled").is_enabled())

    def test_missing_unrecognized_conflicting_values_unknown(self):
        for text in (
            "", "tlp-rdw = enabled", "tlp = running",
            "tlp = enabled\nstate = disabled", "tlp = unknown",
        ):
            with self.subTest(text=text):
                self.assertEqual(TLPStatusParser(text).state(), "unknown")
                self.assertFalse(TLPStatusParser(text).is_enabled())

    def test_enabled_tlp_blocks_even_with_oneshot_unit_inactive(self):
        with patch.object(conflicts, "which", return_value="/usr/sbin/tlp-stat"), \
             patch.object(conflicts.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout="tlp = enabled, last run: boot"
                          )) as run:
            state = conflicts.competing_charge_manager()
        self.assertTrue(state.blocked)
        self.assertEqual(state.status, "active")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["tlp-stat", "-s"])

    def test_disabled_tlp_does_not_block(self):
        with patch.object(conflicts, "which", return_value="/usr/bin/tlp-stat"), \
             patch.object(conflicts.subprocess, "run",
                          return_value=SimpleNamespace(
                              returncode=0, stdout="tlp = disabled"
                          )):
            state = conflicts.competing_charge_manager()
        self.assertFalse(state.blocked)
        self.assertEqual(state.status, "inactive")

    def test_present_but_unreadable_tlp_blocks(self):
        for output, rc in (("unexpected status", 0), ("tlp = enabled", 1)):
            with self.subTest(rc=rc), \
                 patch.object(conflicts, "which", return_value="/bin/tlp-stat"), \
                 patch.object(conflicts.subprocess, "run",
                              return_value=SimpleNamespace(
                                  returncode=rc, stdout=output
                              )):
                state = conflicts.competing_charge_manager()
            self.assertTrue(state.blocked)
            self.assertEqual(state.status, "unknown")

    def test_tlp_stat_timeout_blocks(self):
        import subprocess
        with patch.object(conflicts, "which", return_value="/bin/tlp-stat"), \
             patch.object(conflicts.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("tlp-stat", 2)):
            state = conflicts.competing_charge_manager()
        self.assertTrue(state.blocked)

    def test_missing_tlp_stat_and_inactive_oneshot_remains_unknown(self):
        with patch.object(conflicts, "which", return_value=None), \
             patch.object(conflicts.subprocess, "run",
                          return_value=SimpleNamespace(returncode=3)):
            state = conflicts.competing_charge_manager()
        self.assertFalse(state.blocked)
        self.assertEqual(state.status, "unknown")

    def test_missing_tlp_stat_and_unit_not_found(self):
        with patch.object(conflicts, "which", return_value=None), \
             patch.object(conflicts.subprocess, "run",
                          return_value=SimpleNamespace(returncode=4)):
            state = conflicts.competing_charge_manager()
        self.assertFalse(state.blocked)
        self.assertEqual(state.status, "absent")


class UPowerCapabilityTests(unittest.TestCase):
    def test_partial_control_properties_do_not_imply_old_api(self):
        raw = {"ChargeThresholdEnabled": False}
        self.assertIsNone(decode_charge_capabilities(raw))
        self.assertIsNone(decode_charge_capabilities(
            {"ChargeThresholdSupported": None}
        ))

    def test_legacy_telemetry_keeps_valid_data(self):
        attrs = {
            "Type": 2, "PowerSupply": True, "IsPresent": True,
            "NativePath": "BAT1", "State": 2, "Percentage": 65.0,
        }
        self.assertIsNotNone(_decode(attrs, PATH))
        self.assertFalse(_decode(attrs, PATH).charge_protection_supported)

    def test_legacy_probe_confirms_property_not_in_introspection(self):
        attrs = base_props()
        for key in tuple(attrs):
            if key.startswith("Charge"):
                del attrs[key]
        bus = FakeBus(properties=attrs)
        result = probe_bus(bus)
        self.assertEqual(result.status, "ready")
        self.assertFalse(result.capabilities.charge_protection_supported)
        self.assertEqual(bus.calls[-1], "Introspect")

    def test_missing_getall_property_but_introspection_advertises_it_blocks(self):
        attrs = base_props()
        for key in tuple(attrs):
            if key.startswith("Charge"):
                del attrs[key]
        class Ambiguous(FakeBus):
            async def call(self, msg):
                if msg.member == "Introspect":
                    self.calls.append("Introspect")
                    self.last_introspect = msg
                    return Reply([
                        '<node><interface name="org.freedesktop.UPower.Device">'
                        '<property name="ChargeThresholdSupported" type="b" access="read"/>'
                        '</interface></node>'
                    ])
                return await super().call(msg)
        bus = Ambiguous(properties=attrs)
        result = probe_bus(bus)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("omitted by GetAll", result.reason)
        self.assertEqual(bus.last_introspect.flags, MessageFlag.NO_AUTOSTART)

    def test_inaccessible_introspection_fails_closed(self):
        attrs = base_props()
        for key in tuple(attrs):
            if key.startswith("Charge"):
                del attrs[key]
        bus = FakeBus(properties=attrs, broken="Introspect")
        state = probe_bus(bus)
        self.assertEqual(state.status, "unavailable")
        self.assertIn("introspection unavailable", state.reason)

    def test_partial_getall_without_supported_never_calls_introspection(self):
        attrs = base_props()
        del attrs["ChargeThresholdSupported"]
        bus = FakeBus(properties=attrs)
        self.assertEqual(probe_bus(bus).status, "unavailable")
        self.assertEqual(bus.calls[-1], "GetAll")

    def test_stopped_activatable_upower_is_nonretryable_policy_hold(self):
        from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe("inactive")):
            result = priority.resolve_charge_control(PATH, 80)
        self.assertEqual(result.action, "blocked")
        self.assertFalse(result.retryable)

    def test_true_dbus_error_is_retryable_and_never_authorizes_write(self):
        from auto_cpufreq.battery_scripts.probe import ChargeServiceProbe
        with patch.object(priority, "probe_charge_service",
                          return_value=ChargeServiceProbe("unavailable")):
            result = priority.resolve_charge_control(PATH, 80)
        self.assertEqual(result.action, "blocked")
        self.assertTrue(result.retryable)


if __name__ == "__main__":
    unittest.main()
