"""Bounded read-only UPower polling tests, without a real system bus."""

import asyncio
import time
import unittest
from unittest.mock import patch

from auto_cpufreq.modules.battery_upower import read_upower_battery


class RepeatedUPowerReadTests(unittest.TestCase):
    def test_repeated_failure_always_returns_none(self):
        async def unavailable(_battery, _timeout):
            raise ConnectionError("No bus")
        with patch("auto_cpufreq.modules.battery_upower._read_upower", side_effect=unavailable):
            for _ in range(30):
                self.assertIsNone(read_upower_battery("/sys/class/power_supply/BAT1"))

    def test_real_timeout_is_bounded_and_falls_back(self):
        async def too_slow(_battery, timeout):
            await asyncio.wait_for(asyncio.sleep(10), timeout=timeout)
        start = time.monotonic()
        with patch("auto_cpufreq.modules.battery_upower._read_upower", side_effect=too_slow):
            for _ in range(3):
                self.assertIsNone(read_upower_battery("/sys/class/power_supply/BAT1", timeout=0.03))
        self.assertLess(time.monotonic() - start, 1.0)
