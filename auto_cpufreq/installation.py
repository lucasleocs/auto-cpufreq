import sys
from pathlib import Path
from typing import Optional


SOURCE_VENV = Path("/opt/auto-cpufreq/venv")
SOURCE_COMMAND = Path("/usr/local/bin/auto-cpufreq")


def is_source_install(*, prefix: Optional[Path] = None) -> bool:
    runtime_prefix = Path(sys.prefix if prefix is None else prefix)
    try:
        return runtime_prefix.resolve() == SOURCE_VENV.resolve()
    except OSError:
        return runtime_prefix == SOURCE_VENV
