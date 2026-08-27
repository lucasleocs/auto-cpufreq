#!/usr/bin/env python3
from pathlib import Path

path = Path("auto_cpufreq/core.py")
text = path.read_text()
old = '''    if psutil.cpu_percent(percpu=False, interval=0.01) >= 30.0 or isclose(
            max(psutil.cpu_percent(percpu=True, interval=0.01)), 100
        ): print("High CPU load", end="")
'''
new = '''    if psutil.cpu_percent(percpu=False, interval=0.01) >= 30.0 or isclose(
        max(psutil.cpu_percent(percpu=True, interval=0.01)), 100
    ): print("High CPU load", end="")
'''
if text.count(old) != 1:
    raise SystemExit("unexpected mon_powersave state")
path.write_text(text.replace(old, new, 1))
print("removed unrelated mon_powersave formatting hunk")
