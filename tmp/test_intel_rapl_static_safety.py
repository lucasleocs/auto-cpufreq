#!/usr/bin/env python3
from configparser import ConfigParser
from pathlib import Path

from auto_cpufreq.modules.intel_rapl import parse_rapl_policy_config


ROOT = Path(__file__).resolve().parents[1]
RAPL_MODULE = ROOT / "auto_cpufreq/modules/intel_rapl.py"


def test_opt_in_defaults_off():
    empty = ConfigParser()
    assert parse_rapl_policy_config(empty, "charger").enabled is False

    explicit_false = ConfigParser()
    explicit_false.read_dict(
        {
            "intel_power": {"enable_rapl_envelopes": "false"},
            "charger": {
                "rapl_package_long_term_w": "12",
                "rapl_package_short_term_w": "20",
            },
        }
    )
    policy = parse_rapl_policy_config(explicit_false, "charger")
    assert policy.enabled is False
    assert policy.targets.long_term_uw is None
    assert policy.targets.short_term_uw is None


def test_write_primitive_is_centralized():
    offenders = []
    for path in (ROOT / "auto_cpufreq").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_write_int(" in text and path != RAPL_MODULE:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], offenders

    rapl_source = RAPL_MODULE.read_text(encoding="utf-8")
    assert 'with path.open("w", encoding="utf-8") as handle:' in rapl_source
    assert rapl_source.count("_write_int(") >= 2


def test_only_systemd_service_enables_rapl_failsafe():
    marker = "AUTO_CPUFREQ_RAPL_FAILSAFE=1"
    occurrences = []

    runtime_paths = list((ROOT / "auto_cpufreq").rglob("*.py"))
    runtime_paths += [path for path in (ROOT / "scripts").rglob("*") if path.is_file()]
    runtime_paths.append(ROOT / "pyproject.toml")

    for path in runtime_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        if marker in text:
            occurrences.append(str(path.relative_to(ROOT)))

    assert occurrences == ["scripts/auto-cpufreq.service"], occurrences


def main():
    test_opt_in_defaults_off()
    test_write_primitive_is_centralized()
    test_only_systemd_service_enables_rapl_failsafe()
    print("Intel RAPL static safety audit checks passed")


if __name__ == "__main__":
    main()
