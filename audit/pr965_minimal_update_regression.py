#!/usr/bin/env python3
from pathlib import Path
from types import SimpleNamespace
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
check("installed-version verification stays in core", hasattr(core, "update_version_matches"))

if hasattr(core, "prepare_update_source"):
    calls = []
    old_run = core.run
    old_github = core.GITHUB
    try:
        core.GITHUB = "https://example.invalid/auto-cpufreq"
        def fake_run(args, *a, **kw):
            calls.append(list(args))
            if args[:2] == ["git", "clone"]:
                destination = Path(args[-1])
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
            return SimpleNamespace(returncode=0)
        core.run = fake_run
        with tempfile.TemporaryDirectory() as root:
            prepared = Path(core.prepare_update_source(root, "v9.9.9"))
            clone = calls[0] if calls else []
            exact_tag = "--branch" in clone and clone[clone.index("--branch") + 1] == "v9.9.9"
            check("prepared source clones exact selected tag", exact_tag, repr(clone))
            check("prepared source keeps historical workspace name", prepared == Path(root) / "auto-cpufreq", str(prepared))
    finally:
        core.run = old_run
        core.GITHUB = old_github

if hasattr(core, "new_update"):
    old_run = core.run
    try:
        core.run = lambda *args, **kwargs: SimpleNamespace(returncode=42)
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)
            (source / "auto-cpufreq-installer").write_text("#!/bin/sh\n")
            propagated = False
            try:
                core.new_update(source)
            except core.click.ClickException:
                propagated = True
            except Exception:
                pass
            check("source installer non-zero status propagates", propagated)
    finally:
        core.run = old_run

installer = (ROOT / "auto-cpufreq-installer").read_text()
check("source installer fails fast", "set -e" in installer)
check("safe.directory handles spaces", 'safe.directory "$(pwd)"' in installer)
check("yum dependency installs are non-interactive", "yum install -y" in installer or "yum -y install" in installer)
check("Fedora uses current python3-devel package", "python3-devel" in installer)
check("eopkg dependency installs are non-interactive", "eopkg install -y" in installer or "eopkg -y install" in installer)
check("desktop-file-utils is declared", "desktop-file-utils" in installer)

bin_source = (ROOT / "auto_cpufreq/bin/auto_cpufreq.py").read_text()
check("CLI has no package-specific daemon wrapper path", "/usr/local/bin/auto-cpufreq-remove" not in bin_source)
check("source preparation precedes daemon removal", bin_source.find("prepare_update_source") != -1 and bin_source.find("prepare_update_source") < bin_source.find("remove_daemon()"))
check("Snap path remains separate from source updater", bin_source.find("if IS_INSTALLED_WITH_SNAP") < bin_source.find("prepare_update_source"))
check("AUR path remains separate from source updater", bin_source.find("IS_INSTALLED_WITH_AUR") < bin_source.find("prepare_update_source"))
check("no separate update module", not (ROOT / "auto_cpufreq/update.py").exists())

print(f"\n{len(passes)} passed, {len(failures)} failed")
if failures:
    print("Failures:")
    for item in failures:
        print(f"- {item}")
    raise SystemExit(1)
