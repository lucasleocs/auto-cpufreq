#!/usr/bin/env python3
from subprocess import PIPE, run
import os
from threading import Thread
from time import monotonic, sleep

from auto_cpufreq.battery_scripts.conflicts import ChargeDriftGuard

from auto_cpufreq.battery_scripts.asus import AsusBatteryDevice
from auto_cpufreq.battery_scripts.ideapad_laptop import IdeapadBatteryDevice
from auto_cpufreq.battery_scripts.shared import (
    BatteryDevice, BatteryApplyResult, writable_threshold_file,
)
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.modules.system_info import SystemInfo
from auto_cpufreq.config.config import config

BATTERY_APPLY_INTERVAL = 3600  # 1 hour
BATTERY_CONFIG_POLL_INTERVAL = 15  # detect reloaded configuration without busy polling
BATTERY_RETRY_INTERVAL = 300  # bounded retry of unavailable battery/UPower


def lsmod(module: str) -> bool:
    try:
        with open("/proc/modules", "r") as f:
            for line in f:
                parts = line.split()
                if parts and parts[0] == module:
                    return True
        return False
    except OSError:
        try:
            return module in run(["lsmod"], stdout=PIPE, stderr=PIPE, text=True).stdout
        except (OSError, FileNotFoundError):
            return False


def battery_get_thresholds():
    dev = get_battery_device()
    if dev is not None:
        return dev.print_thresholds()


def _battery_policy_signature(snapshot=None):
    """Only charging-policy changes trigger early application.

    The PR #985 watcher publishes fully parsed snapshots. A controller tick
    reuses exactly one snapshot for signature, selection and application.
    """
    conf = config.get_config() if snapshot is None else snapshot
    return (
        tuple(sorted(conf.items("battery"))) if conf.has_section("battery") else (),
        tuple(sorted(conf.items("power_supply_ignore_list")))
        if conf.has_section("power_supply_ignore_list") else (),
    )


def _device_identity(dev):
    """Compare discovered kernel paths, not only class names."""
    if dev is None:
        return None
    return (
        type(dev),
        tuple(sorted(dev.batteries)),
        tuple(sorted(dev.start_paths.items())),
        tuple(sorted(dev.stop_paths.items())),
        getattr(dev, "conservation_mode_path", None),
    )


class BatteryDaemonController:
    """One synchronous iteration; background thread only supplies scheduling.

    The shared drift guard survives backend reselection so a transient
    transition never discards knowledge that an external writer intervened.
    """

    def __init__(self):
        self.device = None
        self._identity = None
        self._policy_signature = None
        self._last_run = None
        self._retry_at = None
        self._drift_guard = ChargeDriftGuard()
        self._reported_conflicts = set()
        self._warned_missing = False

    def tick(self, now=None):
        if now is None:
            now = monotonic()

        snapshot = config.get_config()
        policy = _battery_policy_signature(snapshot)
        policy_changed = policy != self._policy_signature
        due = (self._last_run is None
               or now - self._last_run >= BATTERY_APPLY_INTERVAL)
        retry_due = self._retry_at is not None and now >= self._retry_at
        if not (policy_changed or due or retry_due):
            return

        self._policy_signature = policy
        self._last_run = now
        self._retry_at = None
        # No write and no UPower/TLP probe when the feature is disabled.
        if (not snapshot.has_section("battery")
                or snapshot.get("battery", "enable_thresholds", fallback="false") != "true"):
            return

        try:
            candidate = get_battery_device(snapshot)
            if candidate is None:
                if not self._warned_missing:
                    print("WARNING: No compatible charge-threshold device available")
                    self._warned_missing = True
                self.device = None
                self._identity = None
                self._retry_at = now + BATTERY_RETRY_INTERVAL
                return

            self._warned_missing = False
            identity = _device_identity(candidate)
            if self.device is None or self._identity != identity:
                candidate._drift_guard = self._drift_guard
                candidate._reported_conflicts = self._reported_conflicts
                self.device = candidate
                self._identity = identity
            # Reuse the prior instance when selection has not changed.
            result = self.device.apply_threshold_settings(snapshot)
            if result == BatteryApplyResult.RETRY:
                self._retry_at = now + BATTERY_RETRY_INTERVAL
        except Exception:
            self._retry_at = now + BATTERY_RETRY_INTERVAL
            raise


def start_battery_daemon():
    """Run charging policy periodically and after battery-config reloads."""
    controller = BatteryDaemonController()

    def battery_daemon():
        while True:
            try:
                controller.tick()
            except Exception as e:
                print(
                    f"ERROR: An error occurred while applying battery thresholds: {e}"
                )
            sleep(BATTERY_CONFIG_POLL_INTERVAL)

    Thread(target=battery_daemon, daemon=True).start()


def get_battery_device(snapshot=None):
    """Prefer the portable standard charge-stop attribute for numeric settings.

    An explicitly requested Lenovo conservation mode retains its specialized
    manager. On machines without a writable standard stop threshold, preserve
    existing manufacturer-specific behavior.
    """
    settings = config.get_config() if snapshot is None else snapshot
    explicitly_conservation = (
        settings.has_section("battery")
        and settings.has_option("battery", "ideapad_laptop_conservation_mode")
    )
    explicitly_selected = settings.has_option("battery", "battery_device")
    # A configured target must never silently fall back to another battery,
    # including manufacturer backends or firmware conservation controls.
    battery_path = SystemInfo.get_battery_path(
        settings, strict_configured=True
    )
    if explicitly_selected and battery_path is None:
        return None

    if explicitly_conservation and lsmod("ideapad_laptop"):
        return IdeapadBatteryDevice(settings)

    if not explicitly_conservation:
        if battery_path and writable_threshold_file(
            os.path.basename(battery_path), ("charge_control_end_threshold",)
        ):
            dev = GenericKernelBatteryDevice(settings)
            if dev.stop_paths.get(os.path.basename(battery_path)):
                return dev

    if lsmod("ideapad_acpi"):
        return BatteryDevice(settings)
    if lsmod("ideapad_laptop"):
        return IdeapadBatteryDevice(settings)
    if lsmod("thinkpad_acpi"):
        return BatteryDevice(settings)
    if lsmod("asus_wmi"):
        return AsusBatteryDevice(settings)
    return None
