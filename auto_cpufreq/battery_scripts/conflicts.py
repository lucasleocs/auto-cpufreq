"""Conservative conflict checks for charging thresholds.

Never modifies other services. Retains observed kernel state after our verified
write so an unexpected third-party change suspends automatic reapplication.
"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess


@dataclass(frozen=True)
class ChargeConflict:
    blocked: bool
    reason: str = ""
    status: str = "clear"  # active, inactive, absent, unknown, or clear (drift)


def competing_charge_manager() -> ChargeConflict:
    """Do not share power-management tunables with an active TLP service."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "tlp.service"],
            timeout=2, check=False, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # Preserve the prior non-systemd behavior while exposing uncertainty.
        # No ownership proof is inferred from a failed status check.
        return ChargeConflict(False, type(error).__name__, "unknown")
    if result.returncode == 0:
        return ChargeConflict(
            True, "TLP service active; avoid competing charge managers", "active"
        )
    if result.returncode == 3:
        return ChargeConflict(False, "TLP service inactive", "inactive")
    if result.returncode == 4:
        return ChargeConflict(False, "TLP service unit not found", "absent")
    return ChargeConflict(
        False, f"systemctl is-active returned {result.returncode}", "unknown"
    )


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
