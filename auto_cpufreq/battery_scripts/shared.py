#!/usr/bin/env python3
from __future__ import annotations

import os
from enum import Enum
from typing import Any

from auto_cpufreq.config.config import config
from auto_cpufreq.globals import POWER_SUPPLY_DIR
from auto_cpufreq.battery_scripts.priority import resolve_charge_control
from auto_cpufreq.battery_scripts.conflicts import (ChargeDriftGuard, competing_charge_manager)

# The charge_control_{start,end}_threshold files
# are officially documented and preferred,
# but some models or older kernels may only have charge_{start,stop}_threshold files
CHARGE_START_THRESHOLD_FILES = [
    "charge_control_start_threshold",
    "charge_start_threshold",
]
CHARGE_STOP_THRESHOLD_FILES = [
    "charge_control_end_threshold",
    "charge_stop_threshold",
]

 
class BatteryApplyResult(str, Enum):
    """Policy outcome used by the daemon's retry scheduler."""
    APPLIED = "applied"
    SATISFIED = "satisfied"
    RETRY = "retry"
    BLOCKED = "blocked"
    INVALID = "invalid"


def writable_threshold_file(bat: str, candidates) -> str | None:
    """Select the first existing writable attribute in declared order."""
    if not bat or os.path.basename(bat) != bat:
        return None
    for filename in candidates:
        path = os.path.join(POWER_SUPPLY_DIR, bat, filename)
        if os.path.isfile(path) and os.access(path, os.W_OK):
            return path
    return None


class BatteryDevice:
    def __init__(self, snapshot=None):
        self._config_snapshot = snapshot
        self._drift_guard = ChargeDriftGuard()
        self._reported_conflicts = set()
        self.batteries = self._get_batteries()
        self.start_paths = {
            bat: path
            for bat in self.batteries
            if (path := self._choose_threshold_file(bat, CHARGE_START_THRESHOLD_FILES))
            is not None
        }
        self.stop_paths = {
            bat: path
            for bat in self.batteries
            if (path := self._choose_threshold_file(bat, CHARGE_STOP_THRESHOLD_FILES))
            is not None
        }

    def _get_batteries(self) -> list[str]:
        """
        Get list of battery names from POWER_SUPPLY_DIR
        Return list of battery names (e.g., ['BAT0', 'BAT1'])
        """
        if not os.path.isdir(POWER_SUPPLY_DIR):
            print(f"WARNING: {POWER_SUPPLY_DIR} does NOT exist")
            return []
        snapshot = self._config_snapshot
        if snapshot is not None:
            # The legacy vendor writer may address several BAT* supplies.
            # An explicitly selected battery must instead be the only target.
            if snapshot.has_option("battery", "battery_device"):
                from auto_cpufreq.modules.system_info import SystemInfo
                selected = SystemInfo.get_battery_path(
                    snapshot, strict_configured=True
                )
                if selected is None:
                    return []
                # The selected entry was already validated as a system
                # battery by SystemInfo; sysfs does not require BAT* names.
                return [os.path.basename(selected)]

            from auto_cpufreq.core import get_power_supply_ignore_list
            ignore = get_power_supply_ignore_list(snapshot)
            return [
                name for name in sorted(os.listdir(POWER_SUPPLY_DIR))
                if name.startswith("BAT")
                and not any(item in name for item in ignore)
                and (self._read_value_from_file(
                    os.path.join(POWER_SUPPLY_DIR, name, "type")
                ).lower() == "battery")
                and self._read_value_from_file(
                    os.path.join(POWER_SUPPLY_DIR, name, "scope")
                ).lower() != "device"
            ]
        # Preserve direct legacy BatteryDevice() use without a controller.
        return [name for name in os.listdir(POWER_SUPPLY_DIR)
                if name.startswith("BAT")]

    def _choose_threshold_file(self, bat: str, files: list[str]) -> str | None:
        """Select a coherent pair for numeric vendor operations.

        A generic backend passes only a standard attribute (files[:1]) and
        therefore cannot be redirected to vendor attributes.
        """
        snapshot = self._config_snapshot
        if (len(files) > 1 and snapshot is not None
                and snapshot.get("battery", "start_threshold", fallback="").strip()):
            standard_start = writable_threshold_file(
                bat, ("charge_control_start_threshold",)
            )
            standard_stop = writable_threshold_file(
                bat, ("charge_control_end_threshold",)
            )
            if not (standard_start and standard_stop):
                legacy_start = writable_threshold_file(
                    bat, ("charge_start_threshold",)
                )
                legacy_stop = writable_threshold_file(
                    bat, ("charge_stop_threshold",)
                )
                if legacy_start and legacy_stop:
                    return legacy_start if files == CHARGE_START_THRESHOLD_FILES else legacy_stop

        return writable_threshold_file(bat, files)

    def _get_config(self, snapshot=None) -> dict[str, str]:
        conf = config.get_config() if snapshot is None else snapshot
        if not conf.has_section("battery"):
            return {}
        return dict(conf.items("battery"))

    def get_parsed_config(self, snapshot=None) -> dict[str, Any]:
        """
        Parse battery configuration from config file
        Return validated and parsed config as dictionary
        If invalid, thresholds_enabled will always be False
        So see valid values and more info about different devices,
        the TLP documentation is a good reference:
        https://linrunner.de/tlp/settings/bc-vendors.html
        """
        config = self._get_config(snapshot)

        parsed_config = {
            "thresholds_enabled": False,
            "start_threshold": 99,
            "stop_threshold": 100,
            "ideapad_conservation_mode": None,
        }

        if config.get("enable_thresholds") != "true":
            # Return early without further validation
            return parsed_config

        try:
            start_val, stop_val = self._parse_threshold_values(
                config.get("start_threshold"), config.get("stop_threshold")
            )
            parsed_config["start_threshold"] = start_val
            parsed_config["stop_threshold"] = stop_val

            parsed_config["ideapad_conservation_mode"] = (
                self._parse_ideapad_conservation_mode(
                    config.get("ideapad_laptop_conservation_mode")
                )
            )

            parsed_config["thresholds_enabled"] = True
        except ValueError as e:
            # Thresholds will not be enabled if config is invalid
            print(f"ERROR: {e}")
        return parsed_config

    def _parse_threshold_values(
        self, start: None | str, stop: None | str
    ) -> tuple[int, int]:
        """
        Parse and validate start and stop threshold values
        This method should be overridden in subclasses if needed
        Return tuple of (start, stop) as integers if valid
        Raise ValueError if invalid
        """
        if start is None or stop is None:
            raise ValueError("Start and stop thresholds must be set")
        if not start.isdigit() or not stop.isdigit():
            raise ValueError("Start and stop thresholds must be integers")
        start_val = int(start)
        stop_val = int(stop)
        if not (0 <= start_val <= 99):
            raise ValueError("Start threshold must be between 0 and 99")
        if not (1 <= stop_val <= 100):
            raise ValueError("Stop threshold must be between 1 and 100")
        if start_val >= stop_val:
            raise ValueError("Start threshold must be less than stop threshold")
        return start_val, stop_val

    def _parse_ideapad_conservation_mode(self, param: None | str) -> None | bool:
        """
        Parse ideapad conservation mode value from config
        This method is overridden in IdeapadBatteryDevice subclass
        Returns None when the conservation mode is explicitly not set.
        """
        return None

    @staticmethod
    def _valid_threshold_pair(values: tuple[int | None, int | None]) -> bool:
        start, stop = values
        return (
            type(start) is int and type(stop) is int
            and 0 <= start <= 99 and 1 <= stop <= 100
            and start < stop
        )

    @staticmethod
    def _threshold_steps(current: tuple[int, int],
                         target: tuple[int, int]) -> tuple[str, ...] | None:
        """Plan the minimum writes while maintaining start < stop."""
        old_start, old_stop = current
        start, stop = target
        change_start = start != old_start
        change_stop = stop != old_stop
        if not change_start and not change_stop:
            return ()
        if not change_start:
            return ("stop",) if stop > old_start else None
        if not change_stop:
            return ("start",) if start < old_stop else None
        if start < old_stop:
            return ("start", "stop")
        if stop > old_start:
            return ("stop", "start")
        return None

    def _transition_threshold_pair(
        self, bat: str, current: tuple[int, int], target: tuple[int, int],
        *, restoring: bool = False,
    ) -> tuple[bool, tuple[int | None, int | None], bool]:
        """Apply a validated two-value plan; report unexpected external changes.

        The last boolean indicates an unplanned concurrent change, in which
        case automatically restoring old settings may fight another controller.
        """
        steps = self._threshold_steps(current, target)
        if steps is None:
            return False, current, False

        last = current
        for field in steps:
            fresh = self.get_current_threshold(bat)
            if fresh != last:
                print(f"ERROR: battery {bat} thresholds changed during update: {fresh}")
                return False, fresh, True

            index = 0 if field == "start" else 1
            path = self.start_paths[bat] if field == "start" else self.stop_paths[bat]
            desired = target[index]
            wrote = self._write_value_to_file(path, desired)
            observed = self.get_current_threshold(bat)

            if not self._valid_threshold_pair(observed):
                print(f"ERROR: battery {bat} has invalid threshold readback: {observed}")
                return False, observed, False
            if observed[1 - index] != last[1 - index]:
                print(f"ERROR: battery {bat} other threshold changed unexpectedly: {observed}")
                return False, observed, True
            if not wrote or observed[index] == last[index]:
                print(f"ERROR: battery {bat} {field} write not confirmed: {observed}")
                return False, observed, False
            if restoring and observed[index] != desired:
                print(f"ERROR: battery {bat} restore rounded unexpectedly: {observed}")
                return False, observed, False

            # A driver may round an individual attribute. Only proceed if the
            # next requested write can still keep the pair valid.
            if field == "start" and "stop" in steps and target[1] <= observed[0]:
                print(f"ERROR: rounded start would conflict with requested stop: {observed}")
                return False, observed, False
            if field == "stop" and "start" in steps and target[0] >= observed[1]:
                print(f"ERROR: rounded stop would conflict with requested start: {observed}")
                return False, observed, False
            last = observed
        return True, last, False

    def set_battery_thresholds(self, bat, start: int, stop: int) -> bool:
        """Apply charge start/stop without an unconditional 100% detour.

        Kernel sysfs does not promise atomic writes across two attributes.
        After a partial failure, make at most one safe restoration attempt.
        """
        if bat not in self.start_paths or bat not in self.stop_paths:
            print(f"WARNING: battery {bat} has no writable start/stop attributes")
            return False

        target = (start, stop)
        old = self.get_current_threshold(bat)
        if not self._valid_threshold_pair(target):
            print(f"ERROR: invalid requested thresholds for {bat}: {target}")
            return False
        if not self._valid_threshold_pair(old):
            print(f"ERROR: cannot safely modify unknown thresholds for {bat}: {old}")
            return False
        if old == target:
            return True

        ok, observed, concurrent = self._transition_threshold_pair(bat, old, target)
        if ok:
            if observed != target:
                print(
                    f"WARNING: battery {bat} requested thresholds {target} "
                    f"were adjusted by the kernel to {observed}"
                )
            return True

        if concurrent:
            print(f"ERROR: battery {bat} thresholds changed unexpectedly; "
                  "not restoring over another possible controller")
            return False
        if not self._valid_threshold_pair(observed):
            print(f"ERROR: battery {bat} partial state is unknown: {observed}; "
                  "automatic restoration is unsafe")
            return False
        if observed == old:
            return False

        # One bounded attempt to recover the original pair, only if the
        # driver still provides a coherent and observable intermediate pair.
        restored, final, changed = self._transition_threshold_pair(
            bat, observed, old, restoring=True,
        )
        if restored and final == old:
            print(f"WARNING: battery {bat} threshold update failed; "
                  f"original values {old} were restored")
        else:
            print(f"ERROR: battery {bat} threshold update PARTIAL; "
                  f"observed {final}, original {old}. Manual verification required")
        return False

    def _write_value_to_file(self, path: str, value: str | int) -> bool:
        try:
            with open(path, "w") as f:
                f.write(str(value))
            return True
        except Exception as e:
            print(f"ERROR: Could not write value {value} to {path}: {e}")
            return False

    def _read_value_from_file(self, path: str, default: str = "") -> str:
        try:
            with open(path, "r") as f:
                output = f.read()
            return output.strip()
        except Exception as e:
            print(f"ERROR: Could not read value from {path}: {e}")
            return default

    def get_current_threshold(self, bat: str) -> tuple[int | None, int | None]:

        if bat not in self.start_paths or bat not in self.stop_paths:
            print(f"WARNING: battery {bat} has no threshold attributes")
            return None, None

        start = self._read_value_from_file(self.start_paths[bat])
        stop = self._read_value_from_file(self.stop_paths[bat])
        start = int(start) if start.isdigit() else None
        stop = int(stop) if stop.isdigit() else None
        return start, stop

    def print_thresholds(self):
        print(
            "\n-------------------------------- Battery Info ---------------------------------\n"
        )
        print(f"battery count = {len(self.batteries)}")
        for bat in self.batteries:
            self.print_battery_info(bat)

    def print_battery_info(self, bat: str):
        start_value, stop_value = self.get_current_threshold(bat)
        if start_value is None or stop_value is None:
            print(f"ERROR: failed to read battery {bat} thresholds")
        else:
            print(f"{bat} start threshold = {start_value}")
            print(f"{bat} stop threshold = {stop_value}")

    def apply_threshold_settings_to_bat(self, bat: str, config: dict[str, Any]):
        return self.set_battery_thresholds(
            bat,
            config["start_threshold"],
            config["stop_threshold"],
        )

    def apply_threshold_settings(self, snapshot=None) -> BatteryApplyResult:
        """Apply an enabled policy while classifying retryable and hard stops.

        A blocked owner or drift is never overridden by a retry.
        """
        parsed_config = self.get_parsed_config(snapshot)
        if not parsed_config["thresholds_enabled"]:
            return BatteryApplyResult.INVALID
        if not self.batteries:
            print("WARNING: no batteries found to set thresholds for")
            return BatteryApplyResult.RETRY

        competitor = competing_charge_manager()
        if competitor.blocked:
            if competitor.reason not in self._reported_conflicts:
                print(f"WARNING: charge control paused: {competitor.reason}")
                self._reported_conflicts.add(competitor.reason)
            return BatteryApplyResult.BLOCKED
        if competitor.status == "unknown":
            key = ("competitor", competitor.reason)
            if key not in self._reported_conflicts:
                print(f"WARNING: TLP status could not be determined: {competitor.reason}")
                self._reported_conflicts.add(key)

        retry_needed = False
        blocked = False
        applied = False
        for bat in self.batteries:
            signature = (
                parsed_config["start_threshold"],
                parsed_config["stop_threshold"],
                parsed_config.get("ideapad_conservation_mode"),
            )
            current = self.get_current_threshold(bat)
            drift = self._drift_guard.ready(bat, signature, current)
            if drift.blocked:
                key = (bat, drift.reason)
                if key not in self._reported_conflicts:
                    print(f"WARNING: skipping {bat}: {drift.reason}")
                    self._reported_conflicts.add(key)
                blocked = True
                continue

            request = resolve_charge_control(
                os.path.join(POWER_SUPPLY_DIR, bat),
                parsed_config["stop_threshold"],
                parsed_config["start_threshold"],
                firmware_operation=(
                    parsed_config.get("ideapad_conservation_mode") is not None
                ),
                observed_paths=(
                    self.start_paths.get(bat), self.stop_paths.get(bat)
                ),
            )
            if request.action == "satisfied":
                continue
            if request.action != "apply":
                print(f"WARNING: skipping {bat}: {request.reason}")
                if request.retryable:
                    retry_needed = True
                else:
                    blocked = True
                continue
            if not self.apply_threshold_settings_to_bat(bat, parsed_config):
                print(f"ERROR: failed to set thresholds for battery {bat}")
                after_failure = self.get_current_threshold(bat)
                if after_failure != current:
                    self._drift_guard.suspend(
                        bat, signature,
                        "Partial or concurrent threshold change; manual verification required"
                    )
                    blocked = True
                else:
                    retry_needed = True
                continue
            updated = self.get_current_threshold(bat)
            applied = applied or updated != current
            # Only numeric verified readbacks establish a drift baseline.
            if (updated != current and updated[1] is not None
                    and parsed_config.get("ideapad_conservation_mode") is None):
                self._drift_guard.record(bat, signature, updated)

        if retry_needed:
            return BatteryApplyResult.RETRY
        if blocked:
            return BatteryApplyResult.BLOCKED
        return BatteryApplyResult.APPLIED if applied else BatteryApplyResult.SATISFIED
