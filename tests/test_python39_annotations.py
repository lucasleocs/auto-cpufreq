"""Import annotated modules on Python 3.9 without reading host telemetry."""

import ast
import importlib
import platform
import subprocess
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def module(module_name, **attributes):
    result = types.ModuleType(module_name)
    for key, value in attributes.items():
        setattr(result, key, value)
    return result


class Python39AnnotationImportTests(unittest.TestCase):
    def test_hardware_modules_import_with_synthetic_host_stubs(self):
        config = types.SimpleNamespace(
            has_config=lambda: False,
            get_config=lambda: None,
            path="",
        )
        psutil = module(
            "psutil",
            cpu_count=lambda logical=True: 1,
            cpu_freq=lambda percpu=True: [],
            cpu_percent=lambda interval=None, percpu=False: [],
            sensors_fans=lambda: {},
            sensors_temperatures=lambda: {},
        )
        distro = module(
            "distro",
            name=lambda pretty=False: "synthetic-linux",
            version=lambda: "0",
        )
        config_module = module("auto_cpufreq.config.config", config=config)
        core_module = module(
            "auto_cpufreq.core",
            get_hwp_dynamic_boost=lambda: None,
            get_power_supply_ignore_list=lambda: [],
        )
        globals_module = module(
            "auto_cpufreq.globals",
            AVAILABLE_GOVERNORS_SORTED=(),
            CPU_TEMP_SENSOR_PRIORITY=(),
            IS_INSTALLED_WITH_SNAP=False,
            POWER_SUPPLY_DIR="synthetic-power-supply",
        )
        urwid = module("urwid")
        command_calls = []

        def synthetic_command(_command):
            command_calls.append("stubbed")
            return "synthetic"

        def forbidden_command(*_args, **_kwargs):
            raise AssertionError("an external command was not stubbed")

        stubs = {
            "distro": distro,
            "psutil": psutil,
            "urwid": urwid,
            "auto_cpufreq.config.config": config_module,
            "auto_cpufreq.core": core_module,
            "auto_cpufreq.globals": globals_module,
        }
        module_names = (
            "auto_cpufreq.modules.platform_profile",
            "auto_cpufreq.modules.system_info",
            "auto_cpufreq.battery_scripts.shared",
            "auto_cpufreq.battery_scripts.ideapad_laptop",
            "auto_cpufreq.modules.system_monitor",
            "auto_cpufreq.modules.process_health",
            "auto_cpufreq.modules.report_refresh",
            "auto_cpufreq.modules.report_recommendations",
        )

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, stubs))
            stack.enter_context(
                patch.object(
                    subprocess,
                    "getoutput",
                    side_effect=synthetic_command,
                )
            )
            for function in ("run", "call", "Popen"):
                stack.enter_context(
                    patch.object(
                        subprocess,
                        function,
                        side_effect=forbidden_command,
                    )
                )
            stack.enter_context(
                patch.object(
                    platform,
                    "machine",
                    return_value="synthetic-arch",
                )
            )
            stack.enter_context(
                patch.object(
                    platform,
                    "release",
                    return_value="synthetic-kernel",
                )
            )

            imported = {
                name: importlib.import_module(name)
                for name in module_names
            }

        self.assertEqual(len(command_calls), 2)
        self.assertEqual(
            imported["auto_cpufreq.modules.system_info"].system_info.processor_model,
            "synthetic",
        )


    def test_gtk_selection_methods_keep_python39_compatible_behaviour(self):
        # Extract only the methods: GTK should not be imported on a headless
        # CI runner, but the complete module must parse on Python 3.9.
        tree = ast.parse((ROOT / "auto_cpufreq/gui/objects.py").read_text())
        cases = (
            ("RadioButtonView", "get_override",
             ("powersave", "performance", "default"),
             {"powersave": "powersave", "performance": "performance",
              "default": "default", "unknown": None}, "default"),
            ("CPUTurboOverride", "get_turbo_override",
             ("never", "always", "auto"),
             {"never": "never", "always": "always", "auto": "auto",
              "unknown": None}, "auto"),
            ("BluetoothBootControl", "get_bluetooth_boot_status",
             ("off_btn", "on_btn"),
             {"off": "off_btn", "on": "on_btn", "unknown": "on_btn"},
             "on_btn"),
        )
        for class_name, getter, buttons, values, default_button in cases:
            cls = next(node for node in tree.body
                       if isinstance(node, ast.ClassDef)
                       and node.name == class_name)
            method = next(node for node in cls.body
                          if isinstance(node, ast.FunctionDef)
                          and node.name == "set_selected")
            code = compile(
                ast.fix_missing_locations(
                    ast.Module(body=[method], type_ignores=[])
                ),
                str(ROOT / "auto_cpufreq/gui/objects.py"), "exec",
            )
            for value, expected in values.items():
                with self.subTest(cls=class_name, value=value):
                    namespace = {getter: lambda value=value: value}
                    exec(code, namespace)
                    called = []
                    widget = types.SimpleNamespace(set_by_app=True)
                    for button in buttons:
                        setattr(
                            widget, button,
                            types.SimpleNamespace(
                                set_active=lambda active, button=button:
                                    called.append((button, active))
                            ),
                        )
                    namespace["set_selected"](widget)
                    self.assertEqual(
                        called, [] if expected is None else [(expected, True)]
                    )
                    self.assertEqual(
                        widget.set_by_app, expected != default_button
                    )


if __name__ == "__main__":
    unittest.main()
