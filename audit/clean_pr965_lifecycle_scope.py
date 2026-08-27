#!/usr/bin/env python3
from pathlib import Path

path = Path("auto_cpufreq/core.py")
text = path.read_text()
start = text.index("def mon_powersave():")
next_def = text.find("\ndef ", start + 1)
end = len(text) if next_def == -1 else next_def + 1
block = text[start:end]

targets = {
    "max(psutil.cpu_percent(percpu=True, interval=0.01)), 100": "        max(psutil.cpu_percent(percpu=True, interval=0.01)), 100\n",
    '): print("High CPU load", end="")': '    ): print("High CPU load", end="")\n',
}

lines = block.splitlines(keepends=True)
for needle, replacement in targets.items():
    matches = [index for index, line in enumerate(lines) if needle in line]
    if len(matches) != 1:
        raise SystemExit(f"mon_powersave: expected exactly one {needle!r} line, found {len(matches)}")
    lines[matches[0]] = replacement

clean_block = "".join(lines)
path.write_text(text[:start] + clean_block + text[end:])
print("removed unrelated mon_powersave formatting hunk")
