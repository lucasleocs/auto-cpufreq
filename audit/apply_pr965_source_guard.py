#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    file_path = ROOT / path
    text = file_path.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    file_path.write_text(text.replace(old, new, 1))
    print(f"updated {path}")


replace_once(
    "auto_cpufreq/core.py",
    '''def daemon_is_installed():
    return os.path.exists("/usr/local/bin/auto-cpufreq-remove")


def prepare_update_source(custom_dir, latest_version):
''',
    '''def daemon_is_installed():
    return os.path.exists("/usr/local/bin/auto-cpufreq-remove")


def source_installation_is_managed():
    """Return whether auto-cpufreq-installer owns the active installation."""
    return os.path.isfile("/opt/auto-cpufreq/venv/bin/auto-cpufreq")


def prepare_update_source(custom_dir, latest_version):
''',
)

replace_once(
    "auto_cpufreq/bin/auto_cpufreq.py",
    '''            else:
                latest_version = check_for_update()
''',
    '''            else:
                if not source_installation_is_managed():
                    raise click.ClickException(
                        "This auto-cpufreq installation is managed outside "
                        "auto-cpufreq-installer. Update it using the package manager "
                        "or installation method that installed it."
                    )

                latest_version = check_for_update()
''',
)

print("source-installation guard applied")
