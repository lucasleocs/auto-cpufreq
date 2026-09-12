# Source-install ownership detection is kept separate from package-manager
# detection so lifecycle code can decide whether it may safely mutate
# /opt/auto-cpufreq. Package markers alone are insufficient because source and
# packaged installations can coexist on the same host.

import sys
from pathlib import Path
from typing import Optional


SOURCE_VENV = Path("/opt/auto-cpufreq/venv")
SOURCE_COMMAND = Path("/usr/local/bin/auto-cpufreq")


def is_source_install(*, prefix: Optional[Path] = None) -> bool:
    # The source installer always runs auto-cpufreq from its dedicated venv;
    # sys.prefix therefore identifies the installation that owns this process.
    runtime_prefix = Path(sys.prefix if prefix is None else prefix)
    try:
        return runtime_prefix.resolve() == SOURCE_VENV.resolve()
    except OSError:
        return runtime_prefix == SOURCE_VENV
