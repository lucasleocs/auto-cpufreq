"""Non-privileged config/inotify contract tests; never touch CPU settings."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auto_cpufreq.config.config import _Config, find_config_file
from auto_cpufreq.config.config_event_handler import ConfigEventHandler


class ConfigContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / "auto-cpufreq.conf"
        self.config_path.write_text("[battery]\ngovernor=powersave\n")
        self.config = _Config()
        self.config.set_path(str(self.config_path))
        self.addCleanup(self._close_watch)

    def _close_watch(self):
        notifier = self.config._notifier
        if notifier is not None and notifier.is_alive():
            notifier.stop()
        elif notifier is not None:
            # The notifier owns the watch-manager descriptor.
            self.config.watch_manager.rm_watch(
                self.config._watch_descriptor
            )
            os.close(self.config.watch_manager.get_fd())
            for fd in notifier._pipe:
                os.close(fd)
        else:
            if self.config._watch_descriptor is not None:
                self.config.watch_manager.rm_watch(
                    self.config._watch_descriptor
                )
            os.close(self.config.watch_manager.get_fd())

    def governor(self):
        return self.config.get_config().get("battery", "governor", fallback=None)

    def test_selected_config_order_is_explicit_then_user_then_system(self):
        home = self.root / "mockhome"
        user_file = home / ".config/auto-cpufreq/auto-cpufreq.conf"
        user_file.parent.mkdir(parents=True)
        user_file.write_text("[battery]\\ngovernor=powersave\\n")
        with patch.dict(os.environ, {
            "USER": "test-account",
            "SUDO_USER": "",
        }, clear=True), patch(
            "auto_cpufreq.config.config.pwd.getpwnam",
            return_value=SimpleNamespace(pw_dir=str(home)),
        ):
            self.assertEqual(find_config_file(None), str(user_file))
            self.assertEqual(
                find_config_file(str(self.config_path)),
                str(self.config_path),
            )

    def test_bad_account_name_uses_current_uid_home_fallback(self):
        home = self.root / "fallback"
        user_file = home / ".config/auto-cpufreq/auto-cpufreq.conf"
        user_file.parent.mkdir(parents=True)
        user_file.write_text("[charger]\\ngovernor=performance\\n")
        with patch.dict(os.environ, {
            "USER": "not-a-real-account",
            "SUDO_USER": "",
        }, clear=True), patch(
            "auto_cpufreq.config.config.pwd.getpwnam",
            side_effect=KeyError("missing account"),
        ), patch(
            "auto_cpufreq.config.config.pwd.getpwuid",
            return_value=SimpleNamespace(pw_dir=str(home)),
        ):
            self.assertEqual(find_config_file(None), str(user_file))

    def test_successful_reload_publishes_new_snapshot(self):
        original = self.config.get_config()
        self.config_path.write_text("[battery]\ngovernor=performance\n")
        self.assertTrue(self.config.update_config())
        self.assertEqual(self.governor(), "performance")
        self.assertEqual(original.get("battery", "governor"), "powersave")

    def test_malformed_or_unreadable_update_keeps_last_valid(self):
        original = self.config.get_config()
        self.config_path.write_text("[battery]\ninvalid line\n")
        self.assertFalse(self.config.update_config())
        self.assertIs(self.config.get_config(), original)
        self.assertEqual(self.governor(), "powersave")
        self.config_path.unlink()
        self.config_path.mkdir()
        self.assertFalse(self.config.update_config())
        self.assertIs(self.config.get_config(), original)

    def test_file_changed_during_read_keeps_previous_snapshot(self):
        original = self.config.get_config()
        self.config_path.write_text("[battery]\\ngovernor=performance\\n")
        real = os.stat(self.config_path)
        switched = SimpleNamespace(
            st_dev=real.st_dev,
            st_ino=real.st_ino,
            st_size=real.st_size,
            st_mtime_ns=real.st_mtime_ns + 1,
        )
        with patch("auto_cpufreq.config.config.os.stat",
                   return_value=switched):
            self.assertFalse(self.config.update_config())
        self.assertIs(self.config.get_config(), original)
        self.assertEqual(self.governor(), "powersave")

    def test_deletion_resets_to_default_and_restoration_recovers(self):
        self.config_path.unlink()
        self.assertTrue(self.config.update_config())
        self.assertIsNone(self.governor())
        self.config_path.write_text("[charger]\ngovernor=performance\n")
        self.assertTrue(self.config.update_config())
        self.assertEqual(
            self.config.get_config().get("charger", "governor"),
            "performance",
        )

    def test_backup_modify_event_does_not_trigger_reload(self):
        original = self.config.get_config()
        handler = ConfigEventHandler(self.config)
        with patch.object(self.config, "update_config") as update:
            handler.process_IN_CLOSE_WRITE(
                SimpleNamespace(pathname=str(self.config_path) + "~")
            )
            update.assert_not_called()
            handler.process_IN_CLOSE_WRITE(
                SimpleNamespace(pathname=str(self.config_path))
            )
            update.assert_called_once_with()
        self.assertIs(self.config.get_config(), original)

    def test_overflow_rescans_current_file(self):
        self.config_path.write_text("[battery]\ngovernor=performance\n")
        ConfigEventHandler(self.config).process_IN_Q_OVERFLOW(
            SimpleNamespace()
        )
        self.assertEqual(self.governor(), "performance")

    def test_notifier_is_lazy_and_duplicate_set_path_has_no_extra_watch(self):
        self.assertIsNone(self.config._notifier)
        descriptor = self.config._watch_descriptor
        self.config.set_path(str(self.config_path))
        self.assertEqual(self.config._watch_descriptor, descriptor)
        self.assertIsNone(self.config._notifier)

    def test_close_write_not_modify_triggers_real_reload(self):
        notifier = self.config.notifier
        notifier.start()
        # Explicit open + flush must not reload until close.
        with self.config_path.open("w") as handle:
            handle.write("[battery]\ngovernor=performance\n")
            handle.flush()
            time.sleep(0.15)
            self.assertEqual(self.governor(), "powersave")
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and self.governor() != "performance":
            time.sleep(0.02)
        self.assertEqual(self.governor(), "performance")

    def test_atomic_replace_and_deletion_events(self):
        notifier = self.config.notifier
        notifier.start()
        replacement = self.root / "staged.conf"
        replacement.write_text("[charger]\ngovernor=performance\n")
        os.replace(replacement, self.config_path)
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and not self.config.get_config().has_section("charger"):
            time.sleep(0.02)
        self.assertTrue(self.config.get_config().has_section("charger"))
        self.config_path.unlink()
        limit = time.monotonic() + 2.0
        while time.monotonic() < limit and self.config.get_config().has_section("charger"):
            time.sleep(0.02)
        self.assertFalse(self.config.get_config().has_section("charger"))

    def test_missing_directory_does_not_prevent_initial_read_or_crash(self):
        not_yet = self.root / "future" / "auto-cpufreq.conf"
        self.config.set_path(str(not_yet))
        self.assertFalse(self.config.has_config())
        self.assertIsNone(self.config._watch_descriptor)
        not_yet.parent.mkdir()
        not_yet.write_text("[battery]\ngovernor=performance\n")
        self.config.set_path(str(not_yet))
        self.assertEqual(self.governor(), "performance")
        self.assertIsNotNone(self.config._watch_descriptor)


if __name__ == "__main__":
    unittest.main(verbosity=2)
