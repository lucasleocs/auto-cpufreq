#!/usr/bin/env python3

from auto_cpufreq.battery_scripts.shared import BatteryDevice


class AsusBatteryDevice(BatteryDevice):
    """Preserve the ASUS backend identity while using the shared writer."""
