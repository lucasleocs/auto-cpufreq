"""Read-only arbitration of battery charge control ownership.

UPower reports existing charging policy but does not set arbitrary percentages
through EnableChargeThreshold. Decisions here never write to UPower or sysfs.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from auto_cpufreq.battery_scripts.probe import probe_charge_service
from auto_cpufreq.modules.system_info import SystemInfo


@dataclass(frozen=True)
class ChargeControlDecision:
    action: str  # "apply", "satisfied", "blocked"
    reason: str


def _kernel_threshold(battery_path: str, names: tuple[str, ...]) -> int | None:
    for name in names:
        raw = SystemInfo.read_file(os.path.join(battery_path, name))
        if raw is not None and raw.strip().isdecimal():
            value = int(raw.strip())
            if 0 <= value <= 100:
                return value
    return None


def resolve_charge_control(
    battery_path: str, requested_stop: int, requested_start: int | None = None,
    *, firmware_operation: bool = False,
) -> ChargeControlDecision:
    """Preflight an explicitly enabled charge request, without side effects.

    An unavailable UPower service differs from a healthy service explicitly
    reporting charge control unsupported. Fail closed on unknown ownership.
    """
    probe = probe_charge_service(battery_path)
    if probe.status == "absent":
        return ChargeControlDecision(
            "apply", "UPower service confirmed absent on a healthy system bus"
        )
    if probe.status != "ready" or probe.capabilities is None:
        return ChargeControlDecision(
            "blocked", "UPower service or battery capability unavailable: "
            + (probe.reason or probe.status)
        )

    info = probe.capabilities
    if not info.charge_protection_supported:
        return ChargeControlDecision("apply", "UPower reports no charge-limit control")

    if info.charge_protection_enabled is None:
        return ChargeControlDecision(
            "blocked", "UPower supports protection but its enabled state is unknown"
        )

    if not info.charge_protection_enabled:
        return ChargeControlDecision(
            "apply", "UPower protection is currently disabled"
        )

    if firmware_operation:
        return ChargeControlDecision(
            "blocked", "UPower protection is active; do not override firmware mode"
        )

    if "Firmware managed" in info.charge_control_types and not (
        "Start threshold" in info.charge_control_types
        or "End threshold" in info.charge_control_types
    ):
        return ChargeControlDecision(
            "blocked", "Active firmware-managed protection has no numeric stop threshold"
        )

    actual_stop = _kernel_threshold(
        battery_path, ("charge_control_end_threshold", "charge_stop_threshold")
    )
    actual_start = _kernel_threshold(
        battery_path, ("charge_control_start_threshold", "charge_start_threshold")
    )

    if (info.configured_end_threshold == requested_stop
            and actual_stop == requested_stop
            and (requested_start is None or (
                info.configured_start_threshold == requested_start
                and actual_start == requested_start
            ))):
        return ChargeControlDecision(
            "satisfied", "UPower protection is active and kernel readback matches"
        )

    return ChargeControlDecision(
        "blocked", "UPower protection is active; configured limits or kernel readback differ"
    )
