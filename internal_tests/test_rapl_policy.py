"""Lab-only RAPL opt-in parsing and fail-closed selection regression tests."""
from configparser import ConfigParser
import unittest

from auto_cpufreq.modules.rapl_capabilities import (
    RaplCapabilities,
    RaplPackageLimit,
)
from auto_cpufreq.modules.rapl_policy import (
    RaplPolicyError,
    parse_rapl_configuration,
    resolve_rapl_selection,
)


def snapshot(control_types=("intel-rapl",), limits=()):
    return RaplCapabilities(tuple(control_types), tuple(limits))


def limit(control="intel-rapl", package="package-0", name="long_term",
          watts=15_000_000, zone_enabled=True, control_enabled=True):
    return RaplPackageLimit(control_type=control, package=package,
                            zone_id=control + ":0", constraint_index=0,
                            constraint_name=name, limit_uw=watts,
                            zone_enabled=zone_enabled,
                            control_enabled=control_enabled)


def conf(source=""):
    parser = ConfigParser()
    parser.read_string("[charger]\n[battery]\n[intel_power]\n" + source)
    return parser


def request(*, control=None, package=None, profile="charger",
            pl1="12", pl2=None, enabled="true"):
    parser = conf(f"enable_rapl_envelopes = {enabled}\n" +
                  (f"rapl_control_type = {control}\n" if control is not None else "") +
                  (f"rapl_package = {package}\n" if package is not None else ""))
    if pl1 is not None:
        parser.set(profile, "rapl_package_long_term_w", pl1)
    if pl2 is not None:
        parser.set(profile, "rapl_package_short_term_w", pl2)
    return parse_rapl_configuration(parser, profile)


class PolicyParsingTests(unittest.TestCase):
    def test_disabled_by_default_ignores_invalid_unused_values(self):
        parser = conf()
        parser.set("battery", "rapl_package_long_term_w", "garbage")
        parser.set("intel_power", "rapl_control_type", "both")
        self.assertFalse(parse_rapl_configuration(parser, "battery").enabled)

    def test_disabled_opt_in_remains_inert(self):
        data = request(pl1="INVALID", enabled="false")
        self.assertFalse(data.enabled)
        self.assertEqual(data.targets.requested_names(), ())

    def test_explicit_true_parses_micro_watts_exactly(self):
        data = request(pl1="12.000001", pl2="30.5")
        self.assertEqual(data.targets.long_term_uw, 12_000_001)
        self.assertEqual(data.targets.short_term_uw, 30_500_000)

    def test_allows_one_watt_and_one_micro_watt(self):
        self.assertEqual(request(pl1="0.000001").targets.long_term_uw, 1)
        self.assertEqual(request(pl1="1").targets.long_term_uw, 1_000_000)

    def test_rejects_invalid_booleans(self):
        with self.assertRaises(RaplPolicyError):
            request(enabled="maybe")

    def test_rejects_invalid_profiles(self):
        with self.assertRaises(RaplPolicyError):
            parse_rapl_configuration(conf(), "profile-unknown")

    def test_rejects_invalid_control_type_including_both(self):
        for name in ("both", "intel-rapl-tpmi", "other", "INTEL-RAPL"):
            with self.subTest(name=name), self.assertRaises(RaplPolicyError):
                request(control=name)

    def test_rejects_bad_package_selector(self):
        for name in ("package-a", "../etc/passwd", "core", "package-0/../1"):
            with self.subTest(name=name), self.assertRaises(RaplPolicyError):
                request(package=name)

    def test_rejects_bad_power(self):
        for raw in ("0", "-1", "nan", "inf", "1e3", "1000.000001",
                    "1001", "3.1234567", " 2 2 ", ".5", "10,4", ""):
            with self.subTest(raw=raw), self.assertRaises(RaplPolicyError):
                request(pl1=raw)

    def test_no_targets_does_not_infer_values(self):
        selection = resolve_rapl_selection(
            request(pl1=None, pl2=None),
            snapshot(limits=(limit(),))
        )
        self.assertEqual(selection.state, "no_targets")
        self.assertFalse(selection.has_observed_target)


class ScopeSelectionTests(unittest.TestCase):
    def test_explicitly_disabled_without_ramp(self):
        decision = resolve_rapl_selection(request(enabled="false"), snapshot())
        self.assertEqual(decision.state, "disabled_by_config")

    def test_unsupported_hardware(self):
        result = resolve_rapl_selection(request(), snapshot(control_types=()))
        self.assertEqual(result.state, "not_exposed")

    def test_single_msr_package_can_be_selected(self):
        result = resolve_rapl_selection(
            request(), snapshot(limits=(limit(),))
        )
        self.assertEqual(result.state, "observed")
        self.assertEqual(result.control_type, "intel-rapl")
        self.assertEqual(result.package, "package-0")
        self.assertEqual(result.observed_limits[0].limit_uw, 15_000_000)

    def test_both_msr_and_mmio_require_selector(self):
        capabilities = snapshot(
            ("intel-rapl", "intel-rapl-mmio"),
            (limit(), limit(control="intel-rapl-mmio", watts=28_000_000)),
        )
        result = resolve_rapl_selection(request(), capabilities)
        self.assertEqual(result.state, "ambiguous_control")
        self.assertFalse(result.has_observed_target)

    def test_explicit_mmio_selects_only_mmio_without_mutating_msr(self):
        capabilities = snapshot(
            ("intel-rapl", "intel-rapl-mmio"),
            (limit(watts=28_000_000), limit(control="intel-rapl-mmio", watts=15_000_000))
        )
        result = resolve_rapl_selection(
            request(control="intel-rapl-mmio"), capabilities
        )
        self.assertEqual(result.state, "observed")
        self.assertEqual(len(result.observed_limits), 1)
        self.assertEqual(result.observed_limits[0].control_type, "intel-rapl-mmio")
        self.assertEqual(result.observed_limits[0].limit_uw, 15_000_000)

    def test_explicit_missing_controller_fails_closed(self):
        result = resolve_rapl_selection(
            request(control="intel-rapl-mmio"), snapshot(limits=(limit(),))
        )
        self.assertEqual(result.state, "control_unavailable")
        self.assertFalse(result.has_observed_target)

    def test_two_packages_require_explicit_package(self):
        capabilities = snapshot(limits=(limit(), limit(package="package-1")))
        result = resolve_rapl_selection(request(), capabilities)
        self.assertEqual(result.state, "ambiguous_package")
        self.assertFalse(result.has_observed_target)

    def test_exact_package_selected(self):
        capabilities = snapshot(limits=(limit(), limit(package="package-1", watts=9_000_000)))
        result = resolve_rapl_selection(
            request(package="package-1"), capabilities
        )
        self.assertEqual(result.state, "observed")
        self.assertEqual(len(result.observed_limits), 1)
        self.assertEqual(result.observed_limits[0].package, "package-1")

    def test_stale_package_selector_fails_closed(self):
        result = resolve_rapl_selection(
            request(package="package-5"), snapshot(limits=(limit(),))
        )
        self.assertEqual(result.state, "package_unavailable")

    def test_no_named_pl1_or_pl2_for_controller(self):
        result = resolve_rapl_selection(
            request(), snapshot(limits=())
        )
        self.assertEqual(result.state, "no_package_constraints")

    def test_pl1_only_partially_observed_when_both_requested(self):
        result = resolve_rapl_selection(
            request(pl2="20"), snapshot(limits=(limit(),))
        )
        self.assertEqual(result.state, "partially_observed")
        self.assertEqual(result.missing_constraints, ("short_term",))

    def test_disabled_zone_is_not_selected(self):
        result = resolve_rapl_selection(
            request(), snapshot(limits=(limit(zone_enabled=False),))
        )
        self.assertEqual(result.state, "targets_unavailable")

    def test_unreadable_limit_is_not_selected(self):
        result = resolve_rapl_selection(
            request(), snapshot(limits=(limit(watts=None),))
        )
        self.assertEqual(result.state, "targets_unavailable")

    def test_partial_limit_preserves_independent_pl2(self):
        result = resolve_rapl_selection(
            request(pl2="22"), snapshot(limits=(
                limit(zone_enabled=False),
                limit(name="short_term", watts=41_000_000),
            ))
        )
        self.assertEqual(result.state, "partially_observed")
        self.assertEqual([x.constraint_name for x in result.observed_limits], ["short_term"])
        self.assertEqual(result.missing_constraints, ("long_term",))


if __name__ == "__main__":
    unittest.main()
