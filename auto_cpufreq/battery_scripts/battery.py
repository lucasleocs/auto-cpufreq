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
    if lsmod("ideapad_acpi"):
        return BatteryDevice()
    elif lsmod("ideapad_laptop"):
        return IdeapadBatteryDevice()
    elif lsmod("thinkpad_acpi"):
        return BatteryDevice()
    elif lsmod("asus_wmi"):
        return AsusBatteryDevice()

    # Portable fallback for kernels exposing the standard battery stop
    # threshold, including end-only machines. Preserve all vendor paths.
    battery_path = SystemInfo.get_battery_path()
    if not battery_path:
        return None
    if not os.path.isfile(os.path.join(battery_path, "charge_control_end_threshold")):
        return None
    device = GenericKernelBatteryDevice()
    return device if device.stop_paths else None
