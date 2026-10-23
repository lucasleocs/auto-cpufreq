"""Expected safety guarantees for the legacy Intel RAPL controller.

These tests intentionally identify unsafe legacy behavior before refactoring.
They are laboratory checks and are not part of the contribution branch.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auto_cpufreq.modules.intel_rapl as rapl


class FakeDiscovery:
    def __init__(self, root: Path, zone: Path):
        self.powercap_root = root
        self.zone = zone

    def powercap_zone_paths(self):
        return ((self.zone, "intel-rapl"),)


class RaplSafetyRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zone = self.root / "intel-rapl" / "intel-rapl:0"
        self.zone.mkdir(parents=True)
        (self.zone / "name").write_text("package-0\n")
        (self.zone / "constraint_0_name").write_text("long_term\n")
        self.power_path = self.zone / "constraint_0_power_limit_uw"
        self.power_path.write_text("15000000\n")
        self.store = rapl.RaplStateStore(self.root / "state" / "power-state.json")
        self.controller = rapl.IntelRaplController(
            discovery=FakeDiscovery(self.root, self.zone),
            state_store=self.store,
        )
        self.target = rapl.RaplEnvelopeTargets(long_term_uw=14_500_000)

    def test_disabled_zone_must_not_be_modified(self):
        (self.zone / "enabled").write_text("0\n")
        self.controller.apply(self.target)
        self.assertEqual(
            self.power_path.read_text().strip(), "15000000",
            "An explicitly disabled Powercap zone must not be enabled or written",
        )

    def test_external_drift_suspends_reacquisition(self):
        acquired = self.controller.apply(self.target)
        self.assertEqual(acquired[0].action, "acquired")
        self.power_path.write_text("14250000\n")  # external firmware actor
        drift = self.controller.apply(self.target)
        self.assertEqual(drift[0].action, "drift")
        self.power_path.write_text("15000000\n")  # external actor restores cap
        self.controller.apply(self.target)
        self.assertEqual(
            self.power_path.read_text().strip(), "15000000",
            "After drift, do not fight the external controller on the next cycle",
        )

    def test_failed_readback_keeps_recovery_intent(self):
        actual_read = rapl._read_int
        reads = [0]

        def fail_after_write(path):
            if path == self.power_path:
                reads[0] += 1
                if reads[0] >= 2:
                    return None
            return actual_read(path)

        with patch.object(rapl, "_read_int", side_effect=fail_after_write):
            result = self.controller.apply(self.target)

        self.assertEqual(
            result[0].action, "readback-failed",
            "The scenario must enter post-write readback failure",
        )
        self.assertEqual(self.power_path.read_text().strip(), "14500000")
        self.assertTrue(
            self.store.load(),
            "An unresolved write must leave durable recovery metadata",
        )


if __name__ == "__main__":
    unittest.main()
