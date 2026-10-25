"""Generic kernel charge thresholds without manufacturer-specific drivers.

Only the existing [battery] configuration can enable writes. The default
remains read-only; this module never installs a service or invokes UPower
charge-control methods.
"""

import os

from auto_cpufreq.battery_scripts.shared import BatteryDevice
from auto_cpufreq.globals import POWER_SUPPLY_DIR
from auto_cpufreq.modules.system_info import SystemInfo


class GenericKernelBatteryDevice(BatteryDevice):
    """Apply standard Linux charge-control attributes to one system battery."""

    def __init__(self, snapshot=None):
        self._config_snapshot = snapshot
        self._accepted_stops = {}
        super().__init__(snapshot)

    def _get_batteries(self) -> list[str]:
        selected = SystemInfo.get_battery_path(
            self._config_snapshot, strict_configured=True
        )
        if not selected:
            return []
        selected = os.path.normpath(selected)
        # Restrict writes to one enumerated power_supply entry; never use
        # arbitrary paths, even with a misconfigured battery_device.
        if os.path.dirname(selected) != os.path.normpath(POWER_SUPPLY_DIR):
            return []
        battery = os.path.basename(selected)
        if battery in ("", ".", ".."):
            return []
        if (SystemInfo.read_file(os.path.join(selected, "type")) or "").lower() != "battery":
            return []
        if (SystemInfo.read_file(os.path.join(selected, "scope")) or "").lower() == "device":
            return []
        return [battery]

    def _choose_threshold_file(self, bat: str, files: list[str]) -> str | None:
        # The generic backend is exclusively for the standard kernel ABI.
        # Manufacturer-specific legacy attributes belong to vendor backends.
        return super()._choose_threshold_file(bat, files[:1])

    def _parse_threshold_values(self, start: str | None, stop: str | None):
        if stop is None or not stop.isdecimal():
            raise ValueError("Generic kernel threshold requires integer stop_threshold")
        stop_value = int(stop)
        if not 1 <= stop_value <= 100:
            raise ValueError("stop_threshold must be between 1 and 100")

        if start is None or not start.strip():
            return None, stop_value

        if not start.isdecimal():
            raise ValueError("start_threshold must be an integer")
        start_value = int(start)
        if not 0 <= start_value < stop_value:
            raise ValueError("start_threshold must be between 0 and stop_threshold - 1")
        return start_value, stop_value

    def get_current_threshold(self, bat: str):
        # A generic battery may expose only an end threshold.
        start = self._read_value_from_file(self.start_paths[bat]) if bat in self.start_paths else ""
        stop = self._read_value_from_file(self.stop_paths[bat]) if bat in self.stop_paths else ""
        return (
            int(start) if start.isdecimal() else None,
            int(stop) if stop.isdecimal() else None,
        )

    def apply_threshold_settings_to_bat(self, bat: str, config: dict):
        stop_path = self.stop_paths.get(bat)
        if not stop_path:
            print(f"WARNING: {bat} does not expose a writable charge stop threshold")
            return False

        requested = config["stop_threshold"]
        configured_start = config["start_threshold"]
        current_start, current_stop = self.get_current_threshold(bat)
        if configured_start is not None:
            if bat not in self.start_paths:
                print(f"ERROR: {bat} has no configurable charge start threshold")
                return False
            # Preserve the established path for devices with both attributes.
            return self.set_battery_thresholds(bat, configured_start, requested)

        if current_start is not None and current_start >= requested:
            print(
                f"ERROR: {bat} existing start threshold {current_start}% is "
                f"not below the requested stop threshold {requested}%; "
                "set start_threshold explicitly"
            )
            return False

        if current_stop == requested:
            self._accepted_stops.pop(bat, None)
            return True

        # Kernel drivers can round unsupported stop values. After one
        # successful write, accept the documented readback on later polls,
        # unless some other actor changes it.
        if self._accepted_stops.get(bat) == (requested, current_stop):
            return True

        if not self._write_value_to_file(stop_path, requested):
            return False
        _, actual = self.get_current_threshold(bat)
        if actual is None or not 0 <= actual <= 100:
            print(f"ERROR: {bat} stop threshold has no valid kernel readback")
            return False
        if actual == current_stop and actual != requested:
            print(
                f"ERROR: {bat} requested stop {requested}%, but the "
                f"kernel threshold is unchanged at {actual}%"
            )
            return False
        if current_start is not None and actual <= current_start:
            print(
                f"WARNING: kernel accepted stop {actual}% below the existing "
                f"start threshold {current_start}% for {bat}"
            )
            return False
        if actual != requested:
            print(
                f"WARNING: {bat} requested stop {requested}% rounded "
                f"by the kernel to {actual}%"
            )
        self._accepted_stops[bat] = (requested, actual)
        return True

    def print_battery_info(self, bat: str):
        start, stop = self.get_current_threshold(bat)
        print(f"{bat} start threshold = {start if start is not None else 'Unavailable'}")
        print(f"{bat} stop threshold = {stop if stop is not None else 'Unavailable'}")
