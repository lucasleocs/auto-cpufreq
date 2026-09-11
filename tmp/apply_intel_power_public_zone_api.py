#!/usr/bin/env python3
from pathlib import Path


path = Path("auto_cpufreq/modules/intel_power.py")
text = path.read_text(encoding="utf-8")
needle = (
    "    def _powercap_zone_paths(self) -> tuple[tuple[Path, Optional[str]], ...]:\n"
)
assert text.count(needle) == 1, text.count(needle)
wrapper = (
    "    def powercap_zone_paths(self) -> tuple[tuple[Path, Optional[str]], ...]:\n"
    "        \"\"\"Return canonical Powercap zones using existing safety rules.\"\"\"\n"
    "        return self._powercap_zone_paths()\n\n"
)
text = text.replace(needle, wrapper + needle, 1)
path.write_text(text, encoding="utf-8")
