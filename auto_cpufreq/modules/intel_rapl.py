from configparser import ConfigParser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


MICROWATTS_PER_WATT = Decimal("1000000")
INTEL_POWER_SECTION = "intel_power"
ENABLE_RAPL_OPTION = "enable_rapl_envelopes"
LONG_TERM_OPTION = "rapl_package_long_term_w"
SHORT_TERM_OPTION = "rapl_package_short_term_w"


class RaplConfigError(ValueError):
    """Raised when opt-in Intel RAPL configuration is invalid."""


@dataclass(frozen=True)
class RaplEnvelopeTargets:
    long_term_uw: Optional[int] = None
    short_term_uw: Optional[int] = None


@dataclass(frozen=True)
class RaplPolicyConfig:
    enabled: bool
    targets: RaplEnvelopeTargets


def _watts_to_microwatts(raw: str, option: str) -> int:
    try:
        watts = Decimal(raw.strip())
    except InvalidOperation as exc:
        raise RaplConfigError(f"Invalid value for '{option}': {raw}") from exc

    if not watts.is_finite() or watts <= 0:
        raise RaplConfigError(f"Invalid value for '{option}': {raw}")

    microwatts = watts * MICROWATTS_PER_WATT
    if microwatts != microwatts.to_integral_value():
        raise RaplConfigError(
            f"'{option}' has precision below one microwatt: {raw}"
        )

    return int(microwatts)


def _rapl_enabled(conf: ConfigParser) -> bool:
    if not conf.has_option(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION):
        return False

    try:
        return conf.getboolean(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION)
    except ValueError as exc:
        raw = conf.get(INTEL_POWER_SECTION, ENABLE_RAPL_OPTION, fallback="")
        raise RaplConfigError(
            f"Invalid value for '{ENABLE_RAPL_OPTION}': {raw}"
        ) from exc


def _optional_target(
    conf: ConfigParser,
    profile: str,
    option: str,
) -> Optional[int]:
    if not conf.has_option(profile, option):
        return None
    return _watts_to_microwatts(conf.get(profile, option), option)


def parse_rapl_policy_config(
    conf: ConfigParser,
    profile: str,
) -> RaplPolicyConfig:
    """Parse opt-in package RAPL limits for one power-source profile.

    RAPL targets are intentionally ignored while the feature is disabled so
    existing or example configuration cannot cause validation errors, much
    less hardware writes, without the explicit global opt-in.
    """
    enabled = _rapl_enabled(conf)
    if not enabled:
        return RaplPolicyConfig(
            enabled=False,
            targets=RaplEnvelopeTargets(),
        )

    return RaplPolicyConfig(
        enabled=True,
        targets=RaplEnvelopeTargets(
            long_term_uw=_optional_target(conf, profile, LONG_TERM_OPTION),
            short_term_uw=_optional_target(conf, profile, SHORT_TERM_OPTION),
        ),
    )
