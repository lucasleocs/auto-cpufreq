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
    "from shutil import copy\nfrom subprocess import call, check_output, DEVNULL, getoutput, run\n",
    "from shutil import copy, rmtree\nfrom subprocess import call, CalledProcessError, check_output, DEVNULL, getoutput, run\n",
)

replace_once(
    "auto_cpufreq/core.py",
    '            return True\n    # Handle the case where "tag_name" key doesn\'t exist\n',
    '            return latest_version\n    # Handle the case where "tag_name" key doesn\'t exist\n',
)

replace_once(
    "auto_cpufreq/core.py",
    '''def new_update(custom_dir):
    os.chdir(custom_dir)
    print(f"Cloning the latest release to {custom_dir}")
    run(["git", "clone", GITHUB+".git"])
    os.chdir("auto-cpufreq")
    print(f"package cloned to directory {custom_dir}")
    run(['./auto-cpufreq-installer'], input='i\\n', encoding='utf-8')
''',
    '''def daemon_is_installed():
    return os.path.exists("/usr/local/bin/auto-cpufreq-remove")


def prepare_update_source(custom_dir, latest_version):
    """Prepare the exact selected release before changing the installation."""
    source_dir = os.path.join(custom_dir, "auto-cpufreq")

    try:
        os.makedirs(custom_dir, exist_ok=True)
        if os.path.exists(source_dir):
            rmtree(source_dir)
    except OSError as error:
        raise click.ClickException(f"Unable to prepare update directory: {error}") from error

    print(f"Cloning release {latest_version} to {source_dir}")
    try:
        result = run([
            "git", "clone", "--depth", "1", "--branch", latest_version,
            GITHUB + ".git", source_dir,
        ])
    except OSError as error:
        raise click.ClickException(f"Unable to start git clone: {error}") from error

    if result.returncode != 0:
        if os.path.exists(source_dir):
            rmtree(source_dir, ignore_errors=True)
        raise click.ClickException(
            f"Unable to download auto-cpufreq release {latest_version} "
            f"(status {result.returncode})"
        )

    if not os.path.isfile(os.path.join(source_dir, "auto-cpufreq-installer")):
        raise click.ClickException(
            f"Downloaded release {latest_version} does not contain auto-cpufreq-installer"
        )
    return source_dir


def new_update(source_dir):
    """Install source that has already been downloaded and validated."""
    installer = os.path.join(source_dir, "auto-cpufreq-installer")
    try:
        result = run([installer], cwd=source_dir, input="i\\n", encoding="utf-8")
    except OSError as error:
        raise click.ClickException(f"Unable to start release installer: {error}") from error

    if result.returncode != 0:
        raise click.ClickException(f"Release installer failed (status {result.returncode})")


def update_version_matches(expected_version):
    try:
        output = check_output(["auto-cpufreq", "--version"]).decode("utf-8")
    except (OSError, CalledProcessError):
        return False

    match = next(
        (search(r"\\d+\\.\\d+\\.\\d+", line) for line in output.splitlines()
         if line.startswith("auto-cpufreq version")),
        None,
    )
    return match is not None and "v" + match.group() == expected_version
''',
)

replace_once(
    "auto_cpufreq/bin/auto_cpufreq.py",
    "from subprocess import run\nfrom shutil import rmtree\n",
    "from subprocess import run\n",
)

replace_once(
    "auto_cpufreq/bin/auto_cpufreq.py",
    '''                is_new_update = check_for_update()
                if not is_new_update: return
                ans = input("Do you want to update auto-cpufreq to the latest release? [Y/n]: ").strip().lower()
                if not os.path.exists(custom_dir): os.makedirs(custom_dir)
                if os.path.exists(os.path.join(custom_dir, "auto-cpufreq")): rmtree(os.path.join(custom_dir, "auto-cpufreq"))
                if ans in ['', 'y', 'yes']:
                    remove_daemon()
                    remove_complete_msg()
                    new_update(custom_dir)
                    print("enabling daemon")
                    run(["auto-cpufreq", "--install"])
                    print("auto-cpufreq is installed with the latest version")
                    run(["auto-cpufreq", "--version"])
                else: print("Aborted")
''',
    '''                latest_version = check_for_update()
                if not latest_version: return
                ans = input("Do you want to update auto-cpufreq to the latest release? [Y/n]: ").strip().lower()
                if ans in ['', 'y', 'yes']:
                    daemon_was_installed = daemon_is_installed()
                    source_dir = prepare_update_source(custom_dir, latest_version)

                    if daemon_was_installed:
                        remove_daemon()
                        remove_complete_msg()

                    new_update(source_dir)

                    if daemon_was_installed:
                        print("enabling daemon")
                        try:
                            install_result = run(["auto-cpufreq", "--install"])
                        except OSError as error:
                            raise click.ClickException(
                                f"The package was updated, but the daemon could not be reinstalled: {error}"
                            ) from error
                        if install_result.returncode != 0:
                            raise click.ClickException(
                                "The package was updated, but the daemon could not be reinstalled "
                                f"(status {install_result.returncode})"
                            )

                    if not update_version_matches(latest_version):
                        raise click.ClickException(
                            f"The installed version does not match selected release {latest_version}"
                        )
                    print("auto-cpufreq is installed with the latest version")
                    run(["auto-cpufreq", "--version"])
                else: print("Aborted")
''',
)

installer_changes = [
    (
        '  echo "Install: python3, pip3, python3-setuptools, gobject-introspection, cairo (or cairo-devel), gcc, and gtk3"; echo\n',
        '  echo "Install: python3, pip3, python3-setuptools, gobject-introspection, cairo (or cairo-devel), gcc, gtk3, and desktop-file-utils"; echo\n',
    ),
    (
        'function tool_install {\n  echo\n',
        'function tool_install {\n  set -e\n\n  echo\n',
    ),
    (
        '        echo "Error: pyproject.toml not found and PyGObject version not updated!"\n    fi\n',
        '        echo "Error: pyproject.toml not found and PyGObject version not updated!"\n        return 1\n    fi\n',
    ),
    (
        '    "$LIB_GI_REPO" libcairo2-dev libgtk-3-dev gcc python3-gi\n',
        '    "$LIB_GI_REPO" libcairo2-dev libgtk-3-dev gcc python3-gi desktop-file-utils\n',
    ),
    (
        '    if [ -f /etc/centos-release ]; then yum install platform-python-devel\n    else yum install python-devel\n    fi\n    yum install dmidecode gcc cairo-devel gobject-introspection-devel cairo-gobject-devel gtk3-devel\n',
        '    if [ -f /etc/centos-release ]; then yum install -y platform-python-devel\n    else yum install -y python3-devel\n    fi\n    yum install -y dmidecode gcc cairo-devel gobject-introspection-devel cairo-gobject-devel gtk3-devel desktop-file-utils\n',
    ),
    (
        '    eopkg install pip python3 python3-devel dmidecode gobject-introspection-devel libcairo-devel gcc libgtk-3\n    eopkg install -c system.devel\n',
        '    eopkg install -y pip python3 python3-devel dmidecode gobject-introspection-devel libcairo-devel gcc libgtk-3 desktop-file-utils\n    eopkg install -y -c system.devel\n',
    ),
    (
        '    pacman -S --noconfirm --needed python python-pip python-setuptools base-devel dmidecode gobject-introspection gtk3 gcc\n',
        '    pacman -S --noconfirm --needed python python-pip python-setuptools base-devel dmidecode gobject-introspection gtk3 gcc desktop-file-utils\n',
    ),
    (
        '        zypper install -y python3 python3-pip python311-setuptools python3-devel gcc dmidecode gobject-introspection-devel python3-cairo-devel gtk3 gtk3-devel\n',
        '        zypper install -y python3 python3-pip python311-setuptools python3-devel gcc dmidecode gobject-introspection-devel python3-cairo-devel gtk3 gtk3-devel desktop-file-utils\n',
    ),
    (
        '        xbps-install -Sy python3 python3-pip python3-devel python3-setuptools base-devel dmidecode cairo-devel gobject-introspection gcc gtk+3\n',
        '        xbps-install -Sy python3 python3-pip python3-devel python3-setuptools base-devel dmidecode cairo-devel gobject-introspection gcc gtk+3 desktop-file-utils\n',
    ),
    (
        '  git config --global --add safe.directory $(pwd)\n',
        '  git config --global --add safe.directory "$(pwd)"\n',
    ),
]

for old, new in installer_changes:
    replace_once("auto-cpufreq-installer", old, new)

print("candidate updater transformation complete")
