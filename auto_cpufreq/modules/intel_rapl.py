from configparser import ConfigParser
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from auto_cpufreq.modules.intel_power import IntelPowerDiscovery


MICROWATTS_PER_WATT = Decimal("1000000")
INTEL_POWER_SECTION = "intel_power"
ENABLE_RAPL_OPTION = "enable_rapl_envelopes"
LONG_TERM_OPTION = "rapl_package_long_term_w"
SHORT_TERM_OPTION = "rapl_package_short_term_w"
SUPPORTED_CONTROL_TYPES = frozenset({"intel-rapl", "intel-rapl-mmio"})
SUPPORTED_PACKAGE_CONSTRAINTS = frozenset({"long_term", "short_term"})


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


@dataclass(frozen=True)
class RaplConstraintRef:
    control_type: str
    zone_id: str
    zone_name: str
    constraint_index: int
    constraint_name: str
    power_limit_path: Path
    min_power_path: Path
    max_power_path: Path


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


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


class IntelRaplController:
    """Discover package RAPL controls without changing hardware state."""

    def __init__(self, discovery: Optional[IntelPowerDiscovery] = None) -> None:
        self.discovery = discovery or IntelPowerDiscovery()

    def discover_package_constraints(self) -> tuple[RaplConstraintRef, ...]:
        refs: list[RaplConstraintRef] = []

        for zone_path, control_type in self.discovery.powercap_zone_paths():
            if control_type not in SUPPORTED_CONTROL_TYPES:
                continue

            zone_name = _read_text(zone_path / "name")
            if zone_name is None or not zone_name.startswith("package-"):
                continue

            try:
                zone_id = str(zone_path.relative_to(self.discovery.powercap_root))
            except ValueError:
                continue

            try:
                name_paths = sorted(
                    zone_path.glob("constraint_*_name"),
                    key=lambda path: path.name,
                )
            except OSError:
                continue

            for name_path in name_paths:
                suffix = name_path.name.removeprefix("constraint_").removesuffix(
                    "_name"
                )
                if not suffix.isdigit():
                    continue

                constraint_name = _read_text(name_path)
                if constraint_name not in SUPPORTED_PACKAGE_CONSTRAINTS:
                    continue

                index = int(suffix)
                power_limit_path = zone_path / f"constraint_{index}_power_limit_uw"
                try:
                    has_power_limit = power_limit_path.is_file()
                except OSError:
                    has_power_limit = False
                if not has_power_limit:
                    continue

                refs.append(
                    RaplConstraintRef(
                        control_type=control_type,
                        zone_id=zone_id,
                        zone_name=zone_name,
                        constraint_index=index,
                        constraint_name=constraint_name,
                        power_limit_path=power_limit_path,
                        min_power_path=zone_path / f"constraint_{index}_min_power_uw",
                        max_power_path=zone_path / f"constraint_{index}_max_power_uw",
                    )
                )

        return tuple(
            sorted(
                refs,
                key=lambda ref: (
                    ref.control_type,
                    ref.zone_id,
                    ref.constraint_index,
                ),
            )
        )
