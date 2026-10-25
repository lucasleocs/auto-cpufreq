"""Verify UI imports never turn a saved debug preference into I/O."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from auto_cpufreq.gui.diagnostics import GuiDiagnostics


class GtkDiagnosticStartupTests(unittest.TestCase):
    def test_saved_preference_does_not_start_log_during_object_creation(self):
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_home = base / "config"
            state_home = base / "state"
            preference = config_home / "auto-cpufreq" / "gtk-debug.json"
            preference.parent.mkdir(parents=True)
            preference.write_text(json.dumps({"debug_logging": True}))
            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": str(config_home),
                 "XDG_STATE_HOME": str(state_home)},
            ):
                instance = GuiDiagnostics()
                self.assertFalse(instance.enabled)
                self.assertTrue(instance._read_preference())
                self.assertFalse(state_home.exists())
                self.assertIsNone(instance._watchdog_thread)
                self.assertIsNone(instance._health_sampler)

                glib = Mock()
                with patch.object(instance, "_activate") as activate:
                    instance.attach_mainloop(glib)
                    activate.assert_called_once_with()
                self.assertIs(instance._glib, glib)
                self.assertFalse(state_home.exists())

    def test_saved_disabled_preference_is_not_activated_on_attach(self):
        with TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {"XDG_CONFIG_HOME": str(Path(tmp) / "config"),
                 "XDG_STATE_HOME": str(Path(tmp) / "state")},
            ):
                instance = GuiDiagnostics()
                with patch.object(instance, "_activate") as activate:
                    instance.attach_mainloop(Mock())
                    activate.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
