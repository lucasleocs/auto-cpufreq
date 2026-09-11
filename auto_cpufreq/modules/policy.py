from dataclasses import dataclass
from enum import Enum
from typing import Callable

from auto_cpufreq.modules.intel_power import (
    IntelPowerSnapshot,
    ReadStatus,
)


class PowerSource(str, Enum):
    CHARGER = "charger"
    BATTERY = "battery"


@dataclass(frozen=True)
class PolicyActions:
    apply_charger: Callable[[], None]
    apply_battery: Callable[[], None]
    monitor_charger: Callable[[], None]
    monitor_battery: Callable[[], None]


class LegacyPolicy:
    """Route power-source decisions to the existing legacy handlers."""

    name = "legacy"
    requires_periodic_tick = True

    def __init__(self, actions: PolicyActions) -> None:
        self._actions = actions

    def apply(self, source: PowerSource) -> None:
        if source is PowerSource.CHARGER:
            self._actions.apply_charger()
        else:
            self._actions.apply_battery()

    def monitor(self, source: PowerSource) -> None:
        if source is PowerSource.CHARGER:
            self._actions.monitor_charger()
        else:
            self._actions.monitor_battery()


class ModernIntelHwpPolicy(LegacyPolicy):
    """Stage-2 placeholder for the future Modern Intel HWP behavior.

    This intentionally inherits the legacy routing unchanged. Stage 3 will
    replace the apply behavior only after the backend boundary is proven not
    to alter current policy decisions.
    """

    name = "modern-intel-hwp"


def modern_intel_hwp_eligible(snapshot: IntelPowerSnapshot) -> bool:
    """Return whether the snapshot exposes the capabilities for Modern HWP."""

    if (
        snapshot.intel_pstate_status.status is not ReadStatus.AVAILABLE
        or snapshot.intel_pstate_status.value != "active"
        or not snapshot.cpufreq_policies
    ):
        return False

    for policy in snapshot.cpufreq_policies:
        if (
            policy.scaling_driver.status is not ReadStatus.AVAILABLE
            or policy.scaling_driver.value != "intel_pstate"
            or policy.epp.status is not ReadStatus.AVAILABLE
        ):
            return False

    return True


def select_policy_backend(
    snapshot: IntelPowerSnapshot,
    actions: PolicyActions,
) -> LegacyPolicy:
    backend = ModernIntelHwpPolicy if modern_intel_hwp_eligible(snapshot) else LegacyPolicy
    return backend(actions)
