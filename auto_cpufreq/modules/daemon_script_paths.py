"""Resolve lifecycle scripts from one installed source generation.

The public /usr/local/bin daemon helpers remain unchanged during this stage:
the update command and source installer still use the remove helper as the
historical daemon-installed marker.
"""
from pathlib import Path


_SCRIPT_NAMES = {
    "install": "auto-cpufreq-install.sh",
    "remove": "auto-cpufreq-remove.sh",
}


def resolve_daemon_script(scripts_dir, action):
    """Return an absolute, resolved script path, independent of `current`."""
    if action not in _SCRIPT_NAMES:
        raise ValueError("Unsupported daemon lifecycle action: %s" % action)
    scripts = Path(scripts_dir).resolve(strict=True)
    script = (scripts / _SCRIPT_NAMES[action]).resolve(strict=True)
    if not script.is_file():
        raise FileNotFoundError("Daemon lifecycle script is not a file: %s" % script)
    return script
