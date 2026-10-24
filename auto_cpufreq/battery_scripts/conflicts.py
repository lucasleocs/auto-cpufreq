"""Conservative conflict checks for charging thresholds.

Never modifies other services. Retains observed kernel state after our verified
write so an unexpected third-party change suspends automatic reapplication.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess

from auto_cpufreq.config.config import config


@dataclass(frozen=True)
class ChargeConflict:
    blocked: bool
    reason: str = ""


def competing_charge_manager() -> ChargeConflict:
    """Do not share power-management tunables with an active TLP service."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "tlp.service"],
            timeout=2, check=False, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        # This isn't proof that no third party owns the resource: drift
        # detection below remains authoritative for subsequent changes.
        return ChargeConflict(False)
    if result.returncode == 0:
        return ChargeConflict(True, "TLP service active; avoid competing charge managers")
    return ChargeConflict(False)


class ChargeDriftGuard:
    """Daemon-lifetime, per-battery state; no files or hardware writes."""

    def __init__(self):
        self._expected = {}
        self._suspended = set()
        self._suspended_policy = {}

    def suspend(self, battery: str, requested: tuple, reason: str) -> None:
        """Track a partial write even if there was no prior successful write."""
        self._suspended.add(battery)
        self._suspended_policy[battery] = (requested, reason)

    def ready(self, battery: str, requested: tuple, observed: tuple) -> ChargeConflict:
        policy = self._suspended_policy.get(battery)
        if policy is not None and policy[0] != requested:
            self._suspended_policy.pop(battery, None)
            self._suspended.discard(battery)
            self._expected.pop(battery, None)

        current = self._expected.get(battery)
        if current is not None and current[0] != requested:
            self._expected.pop(battery, None)
            self._suspended.discard(battery)
            current = None

        if battery in self._suspended:
            reason = self._suspended_policy.get(
                battery, (None, "External threshold change detected; auto writes suspended")
            )[1]
            return ChargeConflict(True, reason)

        if current is not None:
            _, baseline = current
            # Only compare fields we could positively read on application.
            changed = any(
                expected is not None and actual != expected
                for expected, actual in zip(baseline, observed)
            )
            if changed:
                self.suspend(
                    battery, requested,
                    "Kernel threshold changed after our verified application"
                )
                return ChargeConflict(
                    True, "Kernel threshold changed after our verified application"
                )
        return ChargeConflict(False)

    def record(self, battery: str, requested: tuple, observed: tuple) -> None:
        if any(v is not None for v in observed):
            self._expected[battery] = (requested, observed)
            self._suspended.discard(battery)
            self._suspended_policy.pop(battery, None)
