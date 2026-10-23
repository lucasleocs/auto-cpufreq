"""Parse opt-in package-RAPL settings without issuing any hardware writes.

Resolution is intentionally separate from Intel HWP policy, the daemon, and the
Powercap writer. A readable Powercap limit is *not* proof that it is writable.
"""
from configparser import ConfigParser
from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Optional

from auto_cpufreq.modules.rapl_capabilities import (
    CONTROL_TYPES,
    RaplCapabilities,
    RaplPackageLimit,
)


# Application-level configuration guard, not an Intel electrical specification.
MAX_UNSIGNED_POWER_UW = (1 << 64) - 1
MICROWATTS_PER_WATT = Decimal("1000000")
# Bound the input before parsing to avoid giant integer / Decimal conversion.
WATTS_FORMAT = re.compile(r"(?:0|[1-9][0-9]{0,19})(?:\.[0-9]{1,6})?\Z")
PACKAGE_FORMAT = re.compile(r"package-[0-9]+\Z")


class RaplPolicyError(ValueError):
    """Invalid RAPL opt-in setting or scope selector."""


@dataclass(frozen=True)
class RaplTargets:
    long_term_uw: Optional[int] = None
    short_term_uw: Optional[int] = None

    def requested_names(self) -> tuple[str, ...]:
        return tuple(
            name for name, value in (
                ("long_term", self.long_term_uw),
                ("short_term", self.short_term_uw),
            ) if value is not None
        )


@dataclass(frozen=True)
class RaplConfiguration:
    enabled: bool
    control_type: Optional[str] = None
    package: Optional[str] = None
    targets: RaplTargets = RaplTargets()


@dataclass(frozen=True)
class RaplSelection:
    """Reporting decision, not authorization to perform a Powercap write."""

    state: str
    control_type: Optional[str]
    package: Optional[str]
    observed_limits: tuple[RaplPackageLimit, ...]
    missing_constraints: tuple[str, ...]
    message: str

    @property
    def has_observed_target(self) -> bool:
        return bool(self.observed_limits)


def _option(conf: ConfigParser, section: str, name: str) -> Optional[str]:
    if not conf.has_option(section, name):
        return None
    return conf.get(section, name).strip()


def _watts_to_uw(raw: str, label: str) -> int:
    if not WATTS_FORMAT.fullmatch(raw):
        raise RaplPolicyError(
            f"Invalid {label}: use positive decimal watts with at most "
            "six fractional digits"
        )
    microwatts = int(Decimal(raw) * MICROWATTS_PER_WATT)
    # This is a representation bound, not an arbitrary TDP/SKU limit.
    # Actual writability and safety are validated against the kernel's
    # constraint metadata and the observed external baseline by the writer.
    if not 0 < microwatts <= MAX_UNSIGNED_POWER_UW:
        raise RaplPolicyError(
            f"Invalid {label}: the micro-watt value exceeds a 64-bit "
            "unsigned Powercap limit"
        )
    return microwatts


def parse_rapl_configuration(
    conf: ConfigParser, profile: str
) -> RaplConfiguration:
    """Parse a single AC/battery profile. Disabled settings are inert."""
    if profile not in ("charger", "battery"):
        raise RaplPolicyError(f"Unsupported RAPL profile: {profile}")

    key = "enable_rapl_envelopes"
    if not conf.has_option("intel_power", key):
        return RaplConfiguration(enabled=False)
    try:
        enabled = conf.getboolean("intel_power", key)
    except ValueError as exc:
        raise RaplPolicyError(f"Invalid boolean for [intel_power] {key}") from exc
    if not enabled:
        return RaplConfiguration(enabled=False)

    control = _option(conf, "intel_power", "rapl_control_type") or None
    if control is not None and control not in CONTROL_TYPES:
        raise RaplPolicyError(
            "rapl_control_type must be intel-rapl or intel-rapl-mmio"
        )

    package = _option(conf, "intel_power", "rapl_package") or None
    if package is not None and not PACKAGE_FORMAT.fullmatch(package):
        raise RaplPolicyError(
            "rapl_package must be a package-N name exposed by Powercap"
        )

    def target(option: str) -> Optional[int]:
        value = _option(conf, profile, option)
        return _watts_to_uw(value, f"[{profile}] {option}") if value is not None else None

    return RaplConfiguration(
        enabled=True,
        control_type=control,
        package=package,
        targets=RaplTargets(
            long_term_uw=target("rapl_package_long_term_w"),
            short_term_uw=target("rapl_package_short_term_w"),
        ),
    )


def resolve_rapl_selection(
    conf: RaplConfiguration, capabilities: RaplCapabilities
) -> RaplSelection:
    """Resolve a specific control/package, failing closed on ambiguity.

    The result can be displayed by a caller, but MUST NOT be treated as a
    permit to write. Active writes need independent enable-state, ownership,
    target-vs-baseline, locking and restoration checks.
    """
    def result(state: str, message: str,
               observed: tuple[RaplPackageLimit, ...] = (),
               missing: tuple[str, ...] = (),
               control: Optional[str] = None,
               package: Optional[str] = None) -> RaplSelection:
        return RaplSelection(state, control, package, observed, missing, message)

    if not conf.enabled:
        return result("disabled_by_config", "Intel RAPL writes are disabled")
    requested = conf.targets.requested_names()
    if not requested:
        return result(
            "no_targets",
            "RAPL opt-in is enabled but this profile has no PL1/PL2 target",
        )
    if not capabilities.control_types:
        return result(
            "not_exposed",
            "Linux does not expose a supported Intel RAPL control type",
        )

    if conf.control_type is not None:
        if conf.control_type not in capabilities.control_types:
            return result(
                "control_unavailable",
                "The selected Intel RAPL control type is not available",
            )
        control = conf.control_type
    elif len(capabilities.control_types) != 1:
        return result(
            "ambiguous_control",
            "More than one Intel RAPL control type is exposed; "
            "set [intel_power] rapl_control_type explicitly",
        )
    else:
        control = capabilities.control_types[0]

    available_in_control = tuple(
        limit for limit in capabilities.limits
        if limit.control_type == control
    )
    package_names = tuple(sorted({limit.package for limit in available_in_control}))
    if not package_names:
        return result(
            "no_package_constraints",
            f"No supported package constraints for {control}",
            control=control,
        )
    if conf.package is not None:
        if conf.package not in package_names:
            return result(
                "package_unavailable",
                f"Package {conf.package} has no recognized limits in {control}",
                control=control,
            )
        package = conf.package
    elif len(package_names) != 1:
        return result(
            "ambiguous_package",
            "Multiple package zones are present; "
            "set [intel_power] rapl_package explicitly",
            control=control,
        )
    else:
        package = package_names[0]

    selected = tuple(
        limit for limit in available_in_control
        if limit.package == package and limit.constraint_name in requested
    )
    readable = tuple(limit for limit in selected if limit.state == "observed")
    missing = tuple(name for name in requested if not any(
        limit.constraint_name == name and limit.state == "observed"
        for limit in selected
    ))
    if not readable:
        return result(
            "targets_unavailable",
            "None of the requested package limits is readable and enabled",
            missing=missing, control=control, package=package,
        )
    return result(
        "partially_observed" if missing else "observed",
        "Requested limits are readable; no write access has been established",
        observed=readable, missing=missing,
        control=control, package=package,
    )
