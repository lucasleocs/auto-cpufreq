"""Synthetic, rootless checks for read-only RAPL discovery (lab only)."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_cpufreq.modules.rapl_capabilities import (
    format_rapl_capabilities,
    inspect_rapl,
)


class RaplCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text) + "\n", encoding="utf-8")

    def _make_package(
        self, control="intel-rapl", index=0, package="package-0",
        zone_enabled="1", control_enabled="1",
    ):
        ctl = self.root / control
        ctl.mkdir(exist_ok=True)
        self._write(ctl / "enabled", control_enabled)
        zone = ctl / f"{control}:{index}"
        zone.mkdir(exist_ok=True)
        self._write(zone / "name", package)
        self._write(zone / "enabled", zone_enabled)
        return zone

    def _add_limit(self, zone, index, name, value):
        self._write(zone / f"constraint_{index}_name", name)
        self._write(zone / f"constraint_{index}_power_limit_uw", value)

    def test_no_powercap_support_is_noop(self):
        self.assertEqual(inspect_rapl(self.root).state, "not_exposed")
        self.assertEqual(format_rapl_capabilities(inspect_rapl(self.root)),
                         ("Intel RAPL: Not exposed by kernel",))

    def test_control_without_package_limits(self):
        (self.root / "intel-rapl").mkdir()
        self.assertEqual(inspect_rapl(self.root).state, "no_package_constraints")

    def test_named_constraints_ignore_indices_and_subzones(self):
        zone = self._make_package()
        self._add_limit(zone, 8, "short_term", 41_000_000)
        self._add_limit(zone, 3, "long_term", 15_500_000)
        self._add_limit(zone, 0, "peak_power", 60_000_000)
        core = zone / "intel-rapl:0:0"
        core.mkdir()
        self._write(core / "name", "core")
        self._add_limit(core, 0, "long_term", 10_000_000)
        result = inspect_rapl(self.root)
        self.assertEqual(result.state, "observed")
        self.assertEqual([(x.constraint_name, x.constraint_index) for x in result.limits],
                         [("long_term", 3), ("short_term", 8)])
        self.assertEqual(result.limits[0].limit_uw, 15_500_000)

    def test_top_level_alias_deduplicates_same_real_zone(self):
        zone = self._make_package()
        self._add_limit(zone, 0, "long_term", 28_000_000)
        (self.root / "intel-rapl:0").symlink_to(zone, target_is_directory=True)
        result = inspect_rapl(self.root)
        self.assertEqual(len(result.limits), 1)

    def test_direct_top_level_zone_can_be_read_without_control_dir(self):
        zone = self.root / "intel-rapl-mmio:0"
        zone.mkdir()
        self._write(zone / "name", "package-1")
        self._add_limit(zone, 4, "long_term", 15_000_000)
        result = inspect_rapl(self.root)
        self.assertEqual(result.control_types, ("intel-rapl-mmio",))
        self.assertEqual(result.limits[0].package, "package-1")

    def test_both_control_types_remain_distinct(self):
        msr = self._make_package("intel-rapl")
        mmio = self._make_package("intel-rapl-mmio")
        self._add_limit(msr, 0, "long_term", 28_000_000)
        self._add_limit(mmio, 0, "long_term", 15_000_000)
        result = inspect_rapl(self.root)
        self.assertEqual(len(result.limits), 2)
        self.assertEqual(
            {(x.control_type, x.limit_uw) for x in result.limits},
            {("intel-rapl", 28_000_000), ("intel-rapl-mmio", 15_000_000)}
        )

    def test_disabled_zone_is_not_reported_as_available(self):
        zone = self._make_package(zone_enabled="0")
        self._add_limit(zone, 0, "long_term", 15_000_000)
        result = inspect_rapl(self.root)
        self.assertEqual(result.state, "disabled")
        self.assertEqual(result.limits[0].state, "disabled")
        self.assertIn("(disabled)", format_rapl_capabilities(result)[0])

    def test_disabled_control_is_not_reported_as_available(self):
        zone = self._make_package(control_enabled="0")
        self._add_limit(zone, 0, "short_term", 40_000_000)
        self.assertEqual(inspect_rapl(self.root).state, "disabled")

    def test_missing_and_malformed_limits_are_unknown_not_zero(self):
        zone = self._make_package()
        self._add_limit(zone, 0, "long_term", "malformed")
        self._write(zone / "constraint_1_name", "short_term")
        result = inspect_rapl(self.root)
        self.assertEqual(result.state, "unreadable")
        self.assertTrue(all(x.limit_uw is None for x in result.limits))

    def test_one_valid_limit_survives_missing_other_limit(self):
        zone = self._make_package()
        self._add_limit(zone, 0, "long_term", 12_000_000)
        self._write(zone / "constraint_1_name", "short_term")
        result = inspect_rapl(self.root)
        self.assertEqual(result.state, "observed")
        self.assertEqual([x.state for x in result.limits],
                         ["observed", "unreadable"])

    def test_rejects_symlink_escape_from_control_tree(self):
        (self.root / "intel-rapl").mkdir()
        outsider = self.root / "unrelated" / "intel-rapl:0"
        outsider.mkdir(parents=True)
        self._write(outsider / "name", "package-0")
        self._add_limit(outsider, 0, "long_term", 50_000_000)
        (self.root / "intel-rapl:0").symlink_to(
            outsider, target_is_directory=True
        )
        result = inspect_rapl(self.root)
        self.assertEqual(result.state, "no_package_constraints")
        self.assertEqual(len(result.limits), 0)

    def test_rejects_malformed_package_name(self):
        zone = self._make_package(package="package-foo")
        self._add_limit(zone, 0, "long_term", 15_000_000)
        self.assertEqual(inspect_rapl(self.root).state, "no_package_constraints")

    def test_inspection_does_not_write_files(self):
        zone = self._make_package()
        self._add_limit(zone, 0, "long_term", 15_000_000)
        with patch.object(Path, "write_text", side_effect=AssertionError("write")):
            result = inspect_rapl(self.root)
        self.assertEqual(result.state, "observed")


if __name__ == "__main__":
    unittest.main()
