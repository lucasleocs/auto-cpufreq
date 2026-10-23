import ast
import os
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_battery_functions():
    system_tree = ast.parse((ROOT / "auto_cpufreq/modules/system_info.py").read_text())
    battery_class = next(
        node for node in system_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "BatteryInfo"
    )
    helpers = [
        node for node in system_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"battery_status_label", "battery_power_label"}
    ]
    system_class = next(
        node for node in system_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SystemInfo"
    )
    methods = [
        node for node in system_class.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"battery_info", "_battery_power_watts"}
    ]
    isolated_system = ast.ClassDef(
        name="SystemInfo", bases=[], keywords=[], body=methods, decorator_list=[]
    )
    namespace = {"__name__": __name__, "dataclass": dataclass, "os": os}
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[battery_class, *helpers, isolated_system], type_ignores=[])
            ),
            "system_info.py",
            "exec",
        ),
        namespace,
    )
    gui_tree = ast.parse((ROOT / "auto_cpufreq/gui/app.py").read_text())
    gui_status = next(
        node for node in gui_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_battery_status"
    )
    exec(
        compile(ast.Module(body=[gui_status], type_ignores=[]), "app.py", "exec"),
        namespace,
    )
    return namespace


class BatteryStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ns = load_battery_functions()

    def make_battery(self, status, ac_connected, watts=0.0):
        battery = self.ns["BatteryInfo"]
        return battery(
            is_charging=status is not None and status.strip().lower() == "charging",
            is_ac_plugged=ac_connected,
            charging_start_threshold=None,
            charging_stop_threshold=None,
            battery_level=99,
            power_watts=watts,
            status=status,
        )

    def test_status_and_power_labels(self):
        scenarios = (
            ("Charging", True, "Charging", "Charging power"),
            ("Discharging", False, "Discharging", "Discharge power"),
            ("Discharging", True, "Discharging", "Discharge power"),
            ("Not charging", True, "Not charging", "Battery power"),
            ("Not charging", False, "Not charging", "Battery power"),
            ("Full", True, "Full", "Battery power"),
            ("Unknown", True, "Unknown", "Battery power"),
            (None, False, "Unknown", "Battery power"),
            ("garbled", False, "Unknown", "Battery power"),
            (" dIsChArGiNg ", True, "Discharging", "Discharge power"),
        )
        for raw, connected, label, power_label in scenarios:
            with self.subTest(status=raw, ac=connected):
                info = self.make_battery(raw, connected)
                self.assertEqual(self.ns["battery_status_label"](info), label)
                self.assertEqual(self.ns["_battery_status"](info), label)
                self.assertEqual(str(info), label)
                self.assertEqual(self.ns["battery_power_label"](info), power_label)

    def test_absent_battery(self):
        self.assertEqual(self.ns["battery_status_label"](None), "Unavailable")
        self.assertEqual(self.ns["_battery_status"](None), "Unavailable")
        self.assertEqual(self.ns["battery_power_label"](None), "Battery power")

    def test_legacy_charging_flag(self):
        info = self.make_battery(None, True)
        info.is_charging = True
        self.assertEqual(self.ns["battery_status_label"](info), "Charging")
        self.assertEqual(self.ns["battery_power_label"](info), "Charging power")

    def test_sysfs_status_propagation(self):
        system = self.ns["SystemInfo"]
        system.get_battery_path = staticmethod(lambda: "/virtual/BAT0")
        system.external_power_state = staticmethod(lambda _: True)
        system._battery_power_watts = staticmethod(lambda _: 0.0)
        data = {"status": "Discharging", "capacity": "91"}
        system.read_file = staticmethod(lambda path: data.get(os.path.basename(path)))
        info = system.battery_info()
        self.assertEqual(info.status, "Discharging")
        self.assertTrue(info.is_ac_plugged)
        self.assertEqual(self.ns["battery_power_label"](info), "Discharge power")

    def test_zero_power_and_current_voltage_fallback(self):
        system = self.ns["SystemInfo"]
        data = {"power_now": "0", "current_now": "2000000", "voltage_now": "12000000"}
        system.read_file = staticmethod(lambda path: data.get(os.path.basename(path)))
        self.assertEqual(system._battery_power_watts("/virtual/BAT0"), 0.0)
        data.pop("power_now")
        self.assertEqual(system._battery_power_watts("/virtual/BAT0"), 24.0)
        data.pop("voltage_now")
        self.assertIsNone(system._battery_power_watts("/virtual/BAT0"))


if __name__ == "__main__":
    unittest.main()
