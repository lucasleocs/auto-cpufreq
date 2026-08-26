import unittest
from unittest.mock import patch

from auto_cpufreq import core


class VersionFormattingTests(unittest.TestCase):
    def test_release_version_does_not_require_git_suffix(self):
        with patch.object(core, "get_literal_version", return_value="3.1.0"):
            self.assertEqual(core.get_formatted_version(), "3.1.0")

    def test_git_version_keeps_readable_commit_suffix(self):
        with patch.object(core, "get_literal_version", return_value="3.1.0+abc123"):
            self.assertEqual(
                core.get_formatted_version(),
                "3.1.0 (git: abc123)",
            )


if __name__ == "__main__":
    unittest.main()
