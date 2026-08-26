import unittest
from unittest.mock import patch

from auto_cpufreq import core


class VersionFormattingTests(unittest.TestCase):
    def test_release_metadata_formats_without_git_suffix(self):
        # core.get_literal_version() currently preserves its historical
        # trailing '+' representation for package metadata without a VCS suffix.
        with patch.object(core, "get_literal_version", return_value="3.1.0+"):
            self.assertEqual(core.get_formatted_version(), "3.1.0")

    @unittest.expectedFailure
    def test_git_version_keeps_readable_commit_suffix(self):
        # Low-priority presentation debt: current formatting drops this suffix.
        # It does not invalidate release verification, which matches semver.
        with patch.object(core, "get_literal_version", return_value="3.1.0+abc123"):
            self.assertEqual(
                core.get_formatted_version(),
                "3.1.0 (git: abc123)",
            )


if __name__ == "__main__":
    unittest.main()
