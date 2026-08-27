#!/usr/bin/env python3
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import auto_cpufreq.core as core

failures = []
passes = []

def check(name, condition, detail=""):
    if condition:
        passes.append(name)
        print(f"PASS: {name}")
    else:
        failures.append(name)
        print(f"FAIL: {name}{': ' + detail if detail else ''}")

class Response:
    status_code = 200
    def json(self):
        return {"tag_name": "v9.9.9"}

old_get = core.get
old_check_output = core.check_output
try:
    core.get = lambda *args, **kwargs: Response()
    core.check_output = lambda *args, **kwargs: b"auto-cpufreq version: 3.1.0\n"
    target = core.check_for_update()
finally:
    core.get = old_get
    core.check_output = old_check_output

check("checker carries exact release tag", target == "v9.9.9", repr(target))
check("update preparation stays in core", hasattr(core, "prepare_update_source"))
check("daemon installation detection stays in core", hasattr(core, "daemon_is_installed"))

installer = (ROOT / "auto-cpufreq-installer").read_text()
check("source installer fails fast", "set -e" in installer)
check("safe.directory handles spaces", 'safe.directory "$(pwd)"' in installer)
check("yum dependency installs are non-interactive", "yum install -y" in installer or "yum -y install" in installer)
check("eopkg dependency installs are non-interactive", "eopkg install -y" in installer or "eopkg -y install" in installer)
check("desktop-file-utils is declared", "desktop-file-utils" in installer)

bin_source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
check("CLI has no package-specific daemon wrapper path", "/usr/local/bin/auto-cpufreq-remove" not in bin_source)
check("no separate update module", not (ROOT / "auto_cpufreq/update.py").exists())

print(f"\n{len(passes)} passed, {len(failures)} failed")
if failures:
    print("Failures:")
    for item in failures:
        print(f"- {item}")
    raise SystemExit(1)
