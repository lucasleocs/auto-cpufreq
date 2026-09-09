# * add status as one of the available options
# * alert user on snap if detected and how to remove first time live/stats message starts
# * if daemon is disabled and auto-cpufreq is removed (snap) remind user to enable it back
import click
from shutil import which
from subprocess import call, getoutput, run
from sys import argv

# ToDo: update README part how to run this script
from auto_cpufreq.core import *
from auto_cpufreq.globals import GITHUB, IS_INSTALLED_WITH_SNAP
from auto_cpufreq.tlp_stat_parser import TLPStatusParser

# app_name var
app_name = "python3 power_helper.py" if argv[0] == "power_helper.py" else "auto-cpufreq"

def header(): print("\n------------------------- auto-cpufreq: Power helper -------------------------\n")
def warning(): print("\n----------------------------------- Warning -----------------------------------\n")

def helper_opts(): print("\nFor full list of options run: python3 -m auto_cpufreq.power_helper --help")

# used to check if binary exists on the system
def does_command_exists(cmd): return which(cmd) is not None

bluetoothctl_exists = does_command_exists("bluetoothctl")
powerprofilesctl_exists = does_command_exists("powerprofilesctl")
systemctl_exists = does_command_exists("systemctl")
tlp_stat_exists = does_command_exists("tlp-stat")
tuned_stat_exists = does_command_exists("tuned")

# detect if gnome power profile service is running
gnome_power_status = None
if not IS_INSTALLED_WITH_SNAP:
    if systemctl_exists:
        try: gnome_power_status = call(["systemctl", "is-active", "--quiet", "power-profiles-daemon"])
        except (OSError, FileNotFoundError):
            print("\nUnable to determine init system")
            print("If this causes any problems, please submit an issue:")
            print(GITHUB+"/issues")

# alert in case TLP service is running
def tlp_service_detect():
    if tlp_stat_exists:
        status_output = getoutput("tlp-stat -s")
        tlp_status = TLPStatusParser(status_output)
        if tlp_status.is_enabled():
            warning()
            print("Detected you are running a TLP service!")
            print("This daemon might interfere with auto-cpufreq which can lead to unexpected results.")
            print("We strongly encourage you to remove TLP unless you really know what you are doing.")

# alert about TLP when using snap
def tlp_service_detect_snap():
    warning()
    print("Unable to detect if you are using a TLP service!")
    print("This daemon might interfere with auto-cpufreq which can lead to unexpected results.")
    print("We strongly encourage you not to use TLP unless you really know what you are doing.")

# alert in case gnome power profile service is running
def gnome_power_detect():
    if systemctl_exists and gnome_power_status == 0:
        warning()
        print("Detected running GNOME Power Profiles daemon service!")
        print("\nThis daemon might interfere with auto-cpufreq and will be automatically")
        print("disabled when auto-cpufreq daemon is installed and")
        print("it will be re-enabled after auto-cpufreq is removed.")
        
        print("\nOnly necessary to be manually done on Snap package installs!")
        print("Steps to perform this action using auto-cpufreq: power_helper script:")
        print(f"git clone {GITHUB}.git")
        print("python3 -m auto_cpufreq.power_helper --gnome_power_disable")
        print(f"\nReference: {GITHUB}#configuring-auto-cpufreq")

# automatically disable gnome power profile service in case it's running during install
def gnome_power_detect_install():
    if systemctl_exists and gnome_power_status == 0:
        warning()
        print("Detected running GNOME Power Profiles daemon service!")
        print("\nThis daemon might interfere with auto-cpufreq and will be disabled.\n")
        print('This daemon is not automatically disabled in "monitor" mode and')
        print("will be enabled after auto-cpufreq daemon is removed.")

# alert before temporarily stopping gnome power profiles in live mode
def gnome_power_detect_live():
    if systemctl_exists and gnome_power_status == 0:
        warning()
        print("Detected running GNOME Power Profiles daemon service!")
        print("\nThis daemon might interfere with auto-cpufreq and will be stopped.\n")
        print('It will be started again when "live" mode exits.')


# notification on snap
def gnome_power_detect_snap():
    warning()
    print("Due to Snap package confinement limitations please consider installing auto-cpufreq using")
    print(f"auto-cpufreq-installer: {GITHUB}#auto-cpufreq-installer")
    print()
    print("Unable to detect state of GNOME Power Profiles daemon service!")
    print("This daemon might interfere with auto-cpufreq and should be disabled!")
    print("\nSteps to perform this action using auto-cpufreq: power_helper script:")
    print(f"git clone {GITHUB}.git")
    print("python3 -m auto_cpufreq.power_helper --gnome_power_disable")
    print(f"\nReference: {GITHUB}#configuring-auto-cpufreq")

# stops gnome >= 40 power profiles (live)
def gnome_power_stop_live():
    if not IS_INSTALLED_WITH_SNAP and systemctl_exists and gnome_power_status == 0:
        if powerprofilesctl_exists:
            try:
                call(["powerprofilesctl", "set", "balanced"])
            except (OSError, FileNotFoundError, PermissionError):
                pass

        try:
            call(["systemctl", "stop", "power-profiles-daemon"])
        except (OSError, FileNotFoundError, PermissionError):
            pass

# stops tuned (live)
def tuned_stop_live():
    if not IS_INSTALLED_WITH_SNAP and systemctl_exists and tuned_stat_exists:
        try:
            call(["systemctl", "stop", "tuned"])
        except (OSError, FileNotFoundError, PermissionError):
            pass

# starts gnome >= 40 power profiles (live)
def gnome_power_start_live():
    if not IS_INSTALLED_WITH_SNAP and systemctl_exists and gnome_power_status == 0:
        try:
            call(["systemctl", "start", "power-profiles-daemon"])
        except (OSError, FileNotFoundError, PermissionError):
            pass

def tuned_start_live():
    if not IS_INSTALLED_WITH_SNAP and systemctl_exists and tuned_stat_exists: 
        try:
            call(["systemctl", "start", "tuned"])
        except (OSError, FileNotFoundError, PermissionError):
            pass

# enable gnome >= 40 power profiles (uninstall)
def gnome_power_svc_enable():
    if systemctl_exists:
        try:
            print("* Enabling GNOME power profiles\n")
            call(["systemctl", "unmask", "power-profiles-daemon"])
            call(["systemctl", "enable", "--now", "power-profiles-daemon"])
        except (OSError, FileNotFoundError):
            print("\nUnable to enable GNOME power profiles")
            print("If this causes any problems, please submit an issue:")
            print(GITHUB+"/issues")

def tuned_svc_enable():
    if systemctl_exists and tuned_stat_exists:
        try:
            print("* Enabling TuneD\n")
            call(["systemctl", "unmask", "tuned"])
            call(["systemctl", "enable", "--now", "tuned"])
        except (OSError, FileNotFoundError):
            print("\nUnable to enable TuneD daemon")
            print("If this causes any problems, please submit an issue:")
            print(GITHUB+"/issues")

# gnome power profiles current status
def gnome_power_svc_status():
    if systemctl_exists:
        try:
            print("* GNOME power profiles status")
            call(["systemctl", "status", "power-profiles-daemon"])
        except (OSError, FileNotFoundError):
            print("\nUnable to see GNOME power profiles status")
            print("If this causes any problems, please submit an issue:")
            print(GITHUB+"/issues")

def set_bluetooth_auto_enable(value: bool) -> bool:
    """Set AutoEnable in [Policy] section of /etc/bluetooth/main.conf.
    Returns True on success, False on failure."""
    btconf = Path("/etc/bluetooth/main.conf")
    setting = f"AutoEnable={'true' if value else 'false'}"

    try:
        lines = btconf.read_text().splitlines(keepends=True)
    except Exception:
        return False

    new_lines = []
    in_policy_section = False
    found_and_set = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("["):
            if in_policy_section and not found_and_set:
                new_lines.append(f"{setting}\n")
                found_and_set = True
            in_policy_section = stripped.lower() == "[policy]"
            new_lines.append(line)
            continue

        if in_policy_section:
            if not stripped.startswith("#") and stripped.startswith("AutoEnable="):
                new_lines.append(f"{setting}\n")
                found_and_set = True
                continue
            if stripped.startswith("#"):
                uncommented = stripped.lstrip("#").strip()
                if uncommented.startswith("AutoEnable="):
                    new_lines.append(f"{setting}\n")
                    found_and_set = True
                    continue

        new_lines.append(line)

    if in_policy_section and not found_and_set:
        new_lines.append(f"{setting}\n")
        found_and_set = True

    if not found_and_set:
        new_lines.append("\n[Policy]\n")
        new_lines.append(f"{setting}\n")

    try:
        btconf.write_text("".join(new_lines))
        return True
    except Exception:
        return False

# disable bluetooth on boot
def bluetooth_disable():
    if IS_INSTALLED_WITH_SNAP: bluetooth_notif_snap()
    elif bluetoothctl_exists:
        print("* Turn off Bluetooth on boot (only)!")
        print("  If you want bluetooth enabled on boot run: auto-cpufreq --bluetooth_boot_on")
        if not set_bluetooth_auto_enable(False):
            print("\nERROR:\nWas unable to turn off bluetooth on boot")
    else: print("* Turn off bluetooth on boot [skipping] (package providing bluetooth access is not present)")

# enable bluetooth on boot
def bluetooth_enable():
    if IS_INSTALLED_WITH_SNAP: bluetooth_on_notif_snap()
    elif bluetoothctl_exists:
        print("* Turn on bluetooth on boot")
        if not set_bluetooth_auto_enable(True):
            print("\nERROR:\nWas unable to turn on bluetooth on boot")
    else: print("* Turn on bluetooth on boot [skipping] (package providing bluetooth access is not present)")

# turn off bluetooth on snap message
def bluetooth_notif_snap():
    print("\n* Unable to turn off bluetooth on boot due to Snap package restrictions!")
    print("\nSteps to perform this action using auto-cpufreq: power_helper script:")
    print("python3 -m auto_cpufreq.power_helper --bluetooth_boot_off")
    print("\nFor help see: https://github.com/AdnanHodzic/auto-cpufreq/#1-power_helperpy-script-snap-package-install-only")

# turn off bluetooth on snap message
def bluetooth_on_notif_snap():
    print("\n* Unable to turn on bluetooth on boot due to Snap package restrictions!")
    print("\nSteps to perform this action using auto-cpufreq: power_helper script:")
    print("python3 -m auto_cpufreq.power_helper --bluetooth_boot_on")
    print("\nFor help see: https://github.com/AdnanHodzic/auto-cpufreq/#1-power_helperpy-script-snap-package-install-only")

# gnome power removal reminder
def gnome_power_rm_reminder():
    if systemctl_exists and bool(gnome_power_status):
        warning()
        print("Detected GNOME Power Profiles daemon service is stopped!")
        print("This service will now be enabled and started again.\n")


def gnome_power_rm_reminder_snap():
    warning()
    print("Unable to detect state of GNOME Power Profiles daemon service!")
    print("Now it's recommended to enable this service.")
    print("\nSteps to perform this action using auto-cpufreq: power_helper script:")
    print(f"git clone {GITHUB}.git")
    print("python3 -m auto_cpufreq.power_helper --gnome_power_enable")
    print(f"\nReference: {GITHUB}#configuring-auto-cpufreq")

def valid_options():
    print("--gnome_power_enable\t\tEnable GNOME Power Profiles daemon")
    print("--gnome_power_disable\t\tDisable GNOME Power Profiles daemon\n")

def _systemd_load_state(unit: str):
    """Return a unit LoadState while distinguishing absence from failure."""
    try:
        state = run(
            [
                "systemctl",
                "show",
                "--property=LoadState",
                "--value",
                unit,
            ],
            capture_output=True,
            text=True,
        )
    except (OSError, FileNotFoundError, PermissionError):
        return None

    load_state = state.stdout.strip()
    if load_state == "not-found":
        return load_state
    if state.returncode == 0:
        return load_state

    # Older systemd releases can make `show` fail for a missing unit. An empty
    # successful list-unit-files query establishes absence without accepting a
    # genuine systemctl failure as if the service were not installed.
    try:
        installed = run(
            [
                "systemctl",
                "list-unit-files",
                unit,
                "--no-legend",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
        )
    except (OSError, FileNotFoundError, PermissionError):
        return None

    if installed.returncode == 0 and not installed.stdout.strip():
        return "not-found"
    return None


def _run_required_power_command(args, description: str) -> bool:
    try:
        result = run(args)
    except (OSError, FileNotFoundError, PermissionError) as exc:
        print(f"\nUnable to {description}: {exc}")
        return False

    if result.returncode != 0:
        print(
            f"\nUnable to {description}: command exited with "
            f"status {result.returncode}"
        )
        return False

    return True


def disable_power_profiles_daemon() -> bool:
    print("\n* Disabling GNOME power profiles")
    if not _run_required_power_command(
        ["systemctl", "disable", "--now", "power-profiles-daemon"],
        "disable GNOME power profiles",
    ):
        return False

    if not _run_required_power_command(
        ["systemctl", "mask", "power-profiles-daemon"],
        "mask GNOME power profiles",
    ):
        return False

    return True


def disable_tuned_daemon() -> bool:
    print("\n* Disabling TuneD daemon")
    if not _run_required_power_command(
        ["systemctl", "disable", "--now", "tuned"],
        "disable TuneD daemon",
    ):
        return False

    if not _run_required_power_command(
        ["systemctl", "mask", "tuned"],
        "mask TuneD daemon",
    ):
        return False

    return True

# default gnome_power_svc_disable func (balanced)
def gnome_power_svc_disable() -> bool:
    if not systemctl_exists:
        return True

    if gnome_power_status != 0:
        # On non-systemd hosts the import-time status probe is expected to fail;
        # there is no systemd-managed PPD state to change in that case.
        if getoutput("ps h -o comm 1").strip() != "systemd":
            return True

        load_state = _systemd_load_state("power-profiles-daemon")
        if load_state is None:
            print("\nUnable to inspect GNOME power profiles with systemctl")
            return False
        if load_state == "not-found":
            return True
        if load_state not in ("loaded", "masked"):
            print(
                "\nUnable to safely disable GNOME power profiles: "
                f"unexpected LoadState={load_state!r}"
            )
            return False

    if gnome_power_status == 0 and powerprofilesctl_exists:
        print("\nUsing profile: balanced")
        if not _run_required_power_command(
            ["powerprofilesctl", "set", "balanced"],
            "set the GNOME power profile to balanced",
        ):
            return False

    return disable_power_profiles_daemon()


def tuned_svc_disable() -> bool:
    if not systemctl_exists or not tuned_stat_exists:
        return True
    if getoutput("ps h -o comm 1").strip() != "systemd":
        return True
    return disable_tuned_daemon()

# cli
@click.command()
#@click.option("--gnome_power_disable", help="Disable GNOME Power profiles service (default: balanced), reference:\n https://bit.ly/3bjVZW1", type=click.Choice(['balanced', 'performance'], case_sensitive=False))
@click.option("--gnome_power_disable", is_flag=True, help="Disable GNOME Power profiles service")
# ToDo:
# * update readme/docs
@click.option("--gnome_power_enable", is_flag=True, help="Enable GNOME Power Profiles daemon")

@click.option("--gnome_power_status", is_flag=True, help="Get status of GNOME Power profiles service")
@click.option("--bluetooth_boot_on", is_flag=True, help="Turn on Bluetooth on boot")
@click.option("--bluetooth_boot_off", is_flag=True, help="Turn off Bluetooth on boot")
def main(
    gnome_power_enable,
    gnome_power_disable,
    gnome_power_status,
    bluetooth_boot_off,
    bluetooth_boot_on,
):
    root_check()
    header()

    if len(argv) == 1: print('Unrecognized option!\n\nRun: "' + app_name + ' --help" for list of available options.')
    else:
        if gnome_power_enable: gnome_power_svc_enable()
        elif gnome_power_disable: gnome_power_svc_disable()
        elif gnome_power_status: gnome_power_svc_status()
        elif bluetooth_boot_off: bluetooth_disable()
        elif bluetooth_boot_on: bluetooth_enable()
        helper_opts()

    footer()

if __name__ == "__main__": main()
