#!/usr/bin/env python3
from subprocess import PIPE, run
import os
from threading import Thread
from time import sleep

from auto_cpufreq.battery_scripts.asus import AsusBatteryDevice
from auto_cpufreq.battery_scripts.ideapad_laptop import IdeapadBatteryDevice
from auto_cpufreq.battery_scripts.shared import BatteryDevice
from auto_cpufreq.battery_scripts.generic import GenericKernelBatteryDevice
from auto_cpufreq.modules.system_info import SystemInfo
from auto_cpufreq.config.config import config

BATTERY_APPLY_INTERVAL = 3600  # 1 hour


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


def start_battery_daemon():
    """Battery daemon that applies battery charge thresholds at regular intervals."""
    dev = get_battery_device()
    if dev is None:
        print(
            "WARNING: No supported battery device found, battery thresholds will not be applied."
        )
        return

    def battery_daemon():
        while True:
            try:
                dev.apply_threshold_settings()
            except Exception as e:
                print(
                    f"ERROR: An error occurred while applying battery thresholds: {e}"
                )
            sleep(BATTERY_APPLY_INTERVAL)

    Thread(target=battery_daemon, daemon=True).start()


def get_battery_device():
    """Prefer the portable standard charge-stop attribute for numeric settings.

    An explicitly requested Lenovo conservation mode retains its specialized
    manager. On machines without a writable standard stop threshold, preserve
    existing manufacturer-specific behavior.
    """
    settings = config.get_config()
    explicitly_conservation = (
        settings.has_section("battery")
        and settings.has_option("battery", "ideapad_laptop_conservation_mode")
    )
    if not explicitly_conservation:
        battery_path = SystemInfo.get_battery_path()
        if (battery_path
                and os.path.isfile(os.path.join(
                    battery_path, "charge_control_end_threshold"))):
            dev = GenericKernelBatteryDevice()
            if dev.stop_paths:
                return dev

    if lsmod("ideapad_acpi"):
        return BatteryDevice()
    if lsmod("ideapad_laptop"):
        return IdeapadBatteryDevice()
    if lsmod("thinkpad_acpi"):
        return BatteryDevice()
    if lsmod("asus_wmi"):
        return AsusBatteryDevice()
    return None
