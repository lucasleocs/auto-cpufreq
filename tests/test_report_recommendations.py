"""Pure cross-frontend monitor recommendations: no sysfs reads or writes."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from auto_cpufreq.modules.report_recommendations import (
    RecommendationStatus as State,
    evaluate_recommendations,
    governor_candidate,
    turbo_candidate,
)


GOVERNORS = ("performance", "schedutil", "powersave")


def snapshot(**overrides):
    data = {
        "battery_info": SimpleNamespace(is_ac_plugged=True),
        "current_gov": "powersave",
        "is_turbo_on": (False, None),
        "cpu_usage": 35.0,
        "cpu_avg_temp": 51.0,
        "cores_info": [SimpleNamespace(temperature=51.0)],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class RecommendationContractTests(unittest.TestCase):
    def test_governor_candidate_retains_legacy_ac_and_battery_order(self):
        self.assertEqual(governor_candidate(True, GOVERNORS), "performance")
        self.assertEqual(governor_candidate(False, GOVERNORS), "powersave")
        # Legacy callers historically assume AC on unavailable source.
        self.assertEqual(governor_candidate(None, GOVERNORS), "performance")
        self.assertIsNone(governor_candidate(True, ()))

    def test_turbo_candidate_retains_existing_threshold(self):
        for usage, temp, expected in (
            (20.0, 50.0, True),
            (95.0, 75.0, True),
            (19.9, 75.0, False),
            (0.0, 100.0, False),
            (0.0, 0.0, False),
        ):
            with self.subTest(usage=usage, temp=temp):
                self.assertIs(turbo_candidate(usage, temp), expected)

    def test_change_and_no_change_are_distinct(self):
        result = evaluate_recommendations(snapshot(), GOVERNORS)
        self.assertEqual(result.governor.status, State.CHANGE)
        self.assertEqual(result.governor.suggested, "performance")
        self.assertTrue(result.governor.needs_change)
        self.assertEqual(result.turbo.status, State.CHANGE)
        self.assertIs(result.turbo.suggested, True)

        result = evaluate_recommendations(snapshot(
            current_gov="performance", is_turbo_on=(True, None)
        ), GOVERNORS)
        self.assertEqual(result.governor.status, State.NO_CHANGE)
        self.assertEqual(result.turbo.status, State.NO_CHANGE)
        self.assertFalse(result.governor.needs_change)
        self.assertFalse(result.turbo.needs_change)

    def test_battery_and_unknown_source_do_not_become_ac_assumptions(self):
        result = evaluate_recommendations(snapshot(
            battery_info=SimpleNamespace(is_ac_plugged=False),
            current_gov="performance",
        ), GOVERNORS)
        self.assertEqual(result.governor.status, State.CHANGE)
        self.assertEqual(result.governor.suggested, "powersave")

        for source in (None, SimpleNamespace(is_ac_plugged=None)):
            with self.subTest(source=source):
                result = evaluate_recommendations(snapshot(
                    battery_info=source
                ), GOVERNORS)
                self.assertEqual(result.governor.status, State.INDETERMINATE)
                self.assertIsNone(result.governor.suggested)

    def test_missing_governors_and_missing_observation_do_not_recommend(self):
        no_available = evaluate_recommendations(snapshot(), ())
        self.assertEqual(no_available.governor.status, State.UNAVAILABLE)
        self.assertIsNone(no_available.governor.suggested)
        missing_current = evaluate_recommendations(snapshot(
            current_gov=None
        ), GOVERNORS)
        self.assertEqual(
            missing_current.governor.status, State.INDETERMINATE
        )

    def test_turbo_driver_managed_unavailable_and_unknown(self):
        for state, status in (
            ((None, True), State.UNAVAILABLE),
            ((None, False), State.UNAVAILABLE),
            ((None, None), State.INDETERMINATE),
        ):
            with self.subTest(state=state):
                result = evaluate_recommendations(snapshot(
                    is_turbo_on=state
                ), GOVERNORS)
                self.assertEqual(result.turbo.status, status)
                self.assertIsNone(result.turbo.suggested)

    def test_turbo_missing_usage_is_not_false(self):
        for usage in (None, float("nan"), float("inf")):
            with self.subTest(usage=usage):
                result = evaluate_recommendations(snapshot(
                    cpu_usage=usage
                ), GOVERNORS)
                self.assertEqual(result.turbo.status, State.INDETERMINATE)
                self.assertIsNone(result.turbo.suggested)

    def test_no_sensor_reading_is_not_fabricated(self):
        result = evaluate_recommendations(snapshot(
            cpu_avg_temp=None,
            cores_info=[],
            cpu_usage=5.0,
            is_turbo_on=(True, None),
        ), GOVERNORS)
        # The existing suggestion is based on load >=20%, so a reading is
        # not needed for the low-load Off candidate.
        self.assertEqual(result.turbo.status, State.CHANGE)
        self.assertIs(result.turbo.suggested, False)

    def test_immutable_and_does_not_mutate_snapshot(self):
        input_report = snapshot()
        before = vars(input_report).copy()
        result = evaluate_recommendations(input_report, GOVERNORS)
        self.assertEqual(vars(input_report), before)
        with self.assertRaises(FrozenInstanceError):
            result.turbo.status = State.UNAVAILABLE


if __name__ == "__main__":
    unittest.main(verbosity=2)
