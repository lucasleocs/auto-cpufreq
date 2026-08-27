#!/usr/bin/env python3
from pathlib import Path
import re

path = Path("auto_cpufreq/core.py")
text = path.read_text()

patterns = (
    (
        r'(?m)^\s+max\(psutil\.cpu_percent\(percpu=True, interval=0\.01\)\), 100\)$',
        '        max(psutil.cpu_percent(percpu=True, interval=0.01)), 100',
    ),
    (
        r'(?m)^\s+\): print\("High CPU load", end=""\)$',
        '    ): print("High CPU load", end="")',
    ),
)

for pattern, replacement in patterns:
    text, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"unexpected mon_powersave state for {pattern!r}")

path.write_text(text)
print("removed unrelated mon_powersave formatting hunk")
