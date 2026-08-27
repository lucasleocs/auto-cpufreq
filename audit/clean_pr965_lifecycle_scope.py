#!/usr/bin/env python3
from pathlib import Path

path = Path("auto_cpufreq/core.py")
text = path.read_text()
changes = (
    (
        '            max(psutil.cpu_percent(percpu=True, interval=0.01)), 100\n',
        '        max(psutil.cpu_percent(percpu=True, interval=0.01)), 100\n',
    ),
    (
        '        ): print("High CPU load", end="")\n',
        '    ): print("High CPU load", end="")\n',
    ),
)
for old, new in changes:
    if text.count(old) != 1:
        raise SystemExit(f"unexpected mon_powersave state for {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text)
print("removed unrelated mon_powersave formatting hunk")
