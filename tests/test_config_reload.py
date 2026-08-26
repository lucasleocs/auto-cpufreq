import os
import tempfile
import unittest
from unittest.mock import Mock

from auto_cpufreq.config.config import _Config


class ConfigReloadTests(unittest.TestCase):
    def test_invalid_reload_preserves_last_valid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "auto-cpufreq.conf")
            with open(path, "w") as handle:
                handle.write("[battery]\ngovernor = powersave\n")

            config = _Config()
            config.set_path(path)
            self.assertEqual(config.get_config()["battery"]["governor"], "powersave")

            with open(path, "w") as handle:
                handle.write("[battery\ngovernor = performance\n")

            self.assertFalse(config.update_config())
            self.assertEqual(config.get_config()["battery"]["governor"], "powersave")

    def test_deleted_file_clears_active_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "auto-cpufreq.conf")
            with open(path, "w") as handle:
                handle.write("[battery]\ngovernor = powersave\n")

            config = _Config()
            config.set_path(path)
            os.unlink(path)

            self.assertTrue(config.update_config())
            self.assertFalse(config.get_config().has_section("battery"))

    def test_repeated_path_in_same_directory_does_not_duplicate_watch(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "one.conf")
            second = os.path.join(directory, "two.conf")
            for path in (first, second):
                with open(path, "w") as handle:
                    handle.write("[battery]\n")

            config = _Config()
            config.watch_manager = Mock()
            config.set_path(first)
            config.set_path(second)

            self.assertEqual(config.watch_manager.add_watch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
