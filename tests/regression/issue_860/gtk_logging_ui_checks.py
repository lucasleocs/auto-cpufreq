import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from auto_cpufreq.gui.diagnostics import diagnostics


def load_widgets():
    config = types.ModuleType("auto_cpufreq.config.config")
    config.config = types.SimpleNamespace()
    config.find_config_file = lambda _value: None

    core = types.ModuleType("auto_cpufreq.core")
    for key in (
        "distro_info", "get_formatted_version", "get_override",
        "get_turbo_override", "sysinfo",
    ):
        setattr(core, key, lambda *args: None)

    globals_module = types.ModuleType("auto_cpufreq.globals")
    globals_module.GITHUB = "https://github.com/AdnanHodzic/auto-cpufreq"
    globals_module.IS_INSTALLED_WITH_AUR = False
    globals_module.IS_INSTALLED_WITH_SNAP = False

    info = types.ModuleType("auto_cpufreq.modules.system_info")
    info.system_info = types.SimpleNamespace()

    power = types.ModuleType("auto_cpufreq.power_helper")
    power.bluetoothctl_exists = False

    replacements = {
        config.__name__: config,
        core.__name__: core,
        globals_module.__name__: globals_module,
        info.__name__: info,
        power.__name__: power,
    }
    spec = importlib.util.spec_from_file_location(
        "_issue860_logging_widgets",
        ROOT / "auto_cpufreq/gui/objects.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(module)
    return module


WIDGETS = load_widgets()


class DebugLoggingGuiTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.old_log = diagnostics.log_path
        self.old_settings = diagnostics.settings_path
        diagnostics.log_path = Path(self.root.name) / "state/auto-cpufreq/gtk.log"
        diagnostics.settings_path = Path(self.root.name) / "config/auto-cpufreq/gtk-debug.json"
        self.addCleanup(self._reset_diagnostics)
        self.parent = Gtk.Window()
        self.addCleanup(self.parent.destroy)

    def _reset_diagnostics(self):
        diagnostics.shutdown()
        diagnostics.log_path = self.old_log
        diagnostics.settings_path = self.old_settings

    def test_menu_toggle_and_log_location(self):
        menu = WIDGETS.DropDownMenu(self.parent)
        items = menu.menu.get_children()
        labels = {item.get_label(): item for item in items
                  if isinstance(item, Gtk.MenuItem)}
        self.assertIn("Enable debug logging", labels)
        self.assertIn("Debug log location", labels)
        toggle = labels["Enable debug logging"]
        self.assertFalse(toggle.get_active())
        toggle.set_active(True)
        self.assertTrue(diagnostics.enabled)
        self.assertTrue(diagnostics.log_path.exists())
        toggle.set_active(False)
        self.assertFalse(diagnostics.enabled)

    def test_monitor_entry_toggle_with_daemon_absent(self):
        view = WIDGETS.DaemonNotRunningView(self.parent)
        toggle = view.debug_logging_button
        self.assertFalse(toggle.get_active())
        toggle.set_active(True)
        self.assertTrue(diagnostics.enabled)
        toggle.set_active(False)
        self.assertFalse(diagnostics.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
