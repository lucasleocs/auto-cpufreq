#!/usr/bin/env python3
from configparser import ConfigParser

from auto_cpufreq.modules.intel_rapl import (
    RaplConfigError,
    parse_rapl_policy_config,
)


def parse(mapping, profile="charger"):
    conf = ConfigParser()
    conf.read_dict(mapping)
    return parse_rapl_policy_config(conf, profile)


# Existing installs remain disabled by default.
def test_disabled_by_default():
    parsed = parse({"charger": {"rapl_package_long_term_w": "12"}})
    assert parsed.enabled is False
    assert parsed.targets.long_term_uw is None
    assert parsed.targets.short_term_uw is None


def test_exact_decimal_conversion():
    parsed = parse(
        {
            "intel_power": {"enable_rapl_envelopes": "true"},
            "charger": {
                "rapl_package_long_term_w": "12.5",
                "rapl_package_short_term_w": "20.000001",
            },
        }
    )
    assert parsed.enabled is True
    assert parsed.targets.long_term_uw == 12_500_000
    assert parsed.targets.short_term_uw == 20_000_001


def test_missing_profile_targets_are_none():
    parsed = parse(
        {"intel_power": {"enable_rapl_envelopes": "yes"}},
        profile="battery",
    )
    assert parsed.enabled is True
    assert parsed.targets.long_term_uw is None
    assert parsed.targets.short_term_uw is None


def test_disabled_mode_does_not_parse_unused_values():
    parsed = parse(
        {
            "intel_power": {"enable_rapl_envelopes": "false"},
            "charger": {"rapl_package_long_term_w": "not-a-number"},
        }
    )
    assert parsed.enabled is False
    assert parsed.targets.long_term_uw is None


def test_invalid_values_fail_closed():
    invalid_values = (
        "0",
        "-1",
        "nan",
        "inf",
        "12.0000001",
        "abc",
        "",
    )
    for raw in invalid_values:
        try:
            parse(
                {
                    "intel_power": {"enable_rapl_envelopes": "true"},
                    "charger": {"rapl_package_long_term_w": raw},
                }
            )
        except RaplConfigError:
            pass
        else:
            raise AssertionError(f"Expected RaplConfigError for {raw!r}")


def test_invalid_boolean_fails_closed():
    try:
        parse({"intel_power": {"enable_rapl_envelopes": "sometimes"}})
    except RaplConfigError:
        pass
    else:
        raise AssertionError("invalid enable_rapl_envelopes must fail closed")


if __name__ == "__main__":
    test_disabled_by_default()
    test_exact_decimal_conversion()
    test_missing_profile_targets_are_none()
    test_disabled_mode_does_not_parse_unused_values()
    test_invalid_values_fail_closed()
    test_invalid_boolean_fails_closed()
    print("Intel RAPL config parsing checks passed")
