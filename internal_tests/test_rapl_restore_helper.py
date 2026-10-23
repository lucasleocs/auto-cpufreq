"""Lab-only tests for the small independent RAPL stop-time helper."""
import io
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from auto_cpufreq.bin import auto_cpufreq_restore_rapl as helper
from auto_cpufreq.modules import intel_rapl as rapl


class FakeDiscovery:
    def __init__(self, root, zone):
        self.powercap_root = root
        self.zone = zone

    def powercap_zone_paths(self):
        return ((self.zone, "intel-rapl"),)


class RestoreHelperTests(unittest.TestCase):
    def test_nonroot_refuses_without_instantiating_controller(self):
        with patch.object(helper.os, "geteuid", return_value=1000), \
             patch.object(helper, "IntelRaplController") as controller:
            with redirect_stderr(io.StringIO()) as output:
                rc = helper.main()
        self.assertEqual(rc, 1)
        controller.assert_not_called()
        self.assertIn("requires root", output.getvalue())

    def test_no_owned_state_is_success(self):
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper, "IntelRaplController") as factory:
            factory.return_value.restore_owned.return_value = ()
            factory.return_value.state_store.load.return_value = ()
            with redirect_stderr(io.StringIO()):
                rc = helper.main()
        self.assertEqual(rc, 0)

    def test_remaining_journal_is_nonzero_even_for_unknown_action(self):
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper, "IntelRaplController") as factory:
            factory.return_value.restore_owned.return_value = (
                SimpleNamespace(action="new-status", message="unknown state"),
            )
            factory.return_value.state_store.load.return_value = (object(),)
            with redirect_stderr(io.StringIO()) as output:
                rc = helper.main()
        self.assertEqual(rc, 1)
        self.assertIn("unresolved constraint", output.getvalue())

    def test_corrupt_journal_does_not_claim_success(self):
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper, "IntelRaplController") as factory:
            factory.return_value.restore_owned.side_effect = (
                rapl.RaplStateError("malformed state")
            )
            with redirect_stderr(io.StringIO()) as output:
                rc = helper.main()
        self.assertEqual(rc, 1)
        self.assertIn("malformed state", output.getvalue())

    def test_external_drift_with_retired_journal_is_success(self):
        with patch.object(helper.os, "geteuid", return_value=0), \
             patch.object(helper, "IntelRaplController") as factory:
            factory.return_value.restore_owned.return_value = (
                SimpleNamespace(action="retired-drift", message="external drift"),
            )
            factory.return_value.state_store.load.return_value = ()
            with redirect_stderr(io.StringIO()) as output:
                rc = helper.main()
        self.assertEqual(rc, 0)
        self.assertIn("external drift", output.getvalue())

    def test_real_fake_sysfs_restoration_and_journal_cleanup(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            zone = root / "intel-rapl" / "intel-rapl:0"
            zone.mkdir(parents=True)
            (zone / "name").write_text("package-0\n")
            (zone / "constraint_0_name").write_text("long_term\n")
            power = zone / "constraint_0_power_limit_uw"
            power.write_text("15000000\n")
            state = rapl.RaplStateStore(root / "private" / "power-state.json")
            controller = rapl.IntelRaplController(
                discovery=FakeDiscovery(root, zone),
                state_store=state,
            )
            acquire = controller.apply(
                rapl.RaplEnvelopeTargets(long_term_uw=12_000_000)
            )
            self.assertEqual(acquire[0].action, "acquired")
            self.assertEqual(int(power.read_text()), 12_000_000)

            with patch.object(helper.os, "geteuid", return_value=0), \
                 patch.object(helper, "IntelRaplController",
                              return_value=controller):
                with redirect_stderr(io.StringIO()):
                    rc = helper.main()

            self.assertEqual(rc, 0)
            self.assertEqual(int(power.read_text()), 15_000_000)
            self.assertEqual(state.load(), ())


if __name__ == "__main__":
    unittest.main()
