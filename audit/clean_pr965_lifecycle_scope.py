#!/usr/bin/env python3
from pathlib import Path

path = Path("auto_cpufreq/core.py")
lines = path.read_text().splitlines(keepends=True)

targets = {
    "max(psutil.cpu_percent(percpu=True, interval=0.01)), 100": "        max(psutil.cpu_percent(percpu=True, interval=0.01)), 100\n",
    '): print("High CPU load", end="")': '    ): print("High CPU load", end="")\n',
}

for needle, replacement in targets.items():
    matches = [index for index, line in enumerate(lines) if needle in line]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {needle!r} line, found {len(matches)}")
    lines[matches[0]] = replacement

path.write_text("".join(lines))
print("removed unrelated mon_powersave formatting hunk")
