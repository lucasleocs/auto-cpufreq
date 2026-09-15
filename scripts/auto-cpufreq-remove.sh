#!/usr/bin/env bash
#
# auto-cpufreq daemon removal script
# reference: https://github.com/AdnanHodzic/auto-cpufreq
# Thanks to https://github.com/errornonamer for openrc fix

MID="$((`tput cols` / 2))"

echo
printf "%0.s─" $(seq $(( (MID-(${#1}/2)-2) / 2 )))
printf " Running auto-cpufreq daemon removal script "
printf "%0.s─" $(seq $(( (MID-(${#1}/2)-2) / 2 )))
echo; echo

# root check
if ((EUID != 0)); then
  echo; echo "Must be run as root (i.e: 'sudo $0')."; echo
  exit 1
fi

# First argument is the init name, second argument is the stop command, third argument is the disable command and the fourth is the "service" path
function auto_cpufreq_remove {
    echo -e "\n* Stopping auto-cpufreq daemon ($1) service"
    [ -z "${2:-}" ] || $2 || return $?
    echo -e "\n* Disabling auto-cpufreq daemon ($1) at boot"
    [ -z "${3:-}" ] || $3 || return $?
    echo -e "\n* Removing auto-cpufreq daemon ($1) unit file"
    rm -f -- "$4" || return $?
}

case "$(ps h -o comm 1)" in
  dinit)
    if [ -e /etc/dinit.d/auto-cpufreq ] || [ -L /etc/dinit.d/auto-cpufreq ]; then
      auto_cpufreq_remove "dinit" "dinitctl stop --ignore-unstarted auto-cpufreq" "dinitctl disable auto-cpufreq" "/etc/dinit.d/auto-cpufreq" || exit $?
    else
      echo -e "\n* auto-cpufreq dinit service is already removed"
    fi
  ;;
  init)
    if [ -e /etc/init.d/auto-cpufreq ] || [ -L /etc/init.d/auto-cpufreq ]; then
      auto_cpufreq_remove "openrc" "rc-service --ifexists --ifstarted auto-cpufreq stop" "rc-update del auto-cpufreq" "/etc/init.d/auto-cpufreq" || exit $?
    else
      echo -e "\n* auto-cpufreq OpenRC service is already removed"
    fi
  ;;
  runit)
    # First argument is the "sv" path, second argument is the "service" path
    rm_sv() {
      local active_link="$2/service/auto-cpufreq"
      local service_dir="$1/sv/auto-cpufreq"

      if [ -e "$active_link" ] || [ -L "$active_link" ]; then
        echo -e "\n* Stopping auto-cpufreq daemon (runit) service"
        sv stop "$active_link" || return $?
        echo -e "\n* Disabling auto-cpufreq daemon (runit) at boot"
        rm -f -- "$active_link" || return $?
      fi
      echo -e "\n* Removing auto-cpufreq daemon (runit) unit file"
      rm -rf -- "$service_dir" || return $?
    }

    if [ -f /etc/os-release ]; then
      . /etc/os-release
      case $ID in
        void) rm_sv /etc /var || exit $?;;
        artix) rm_sv /etc/runit /run/runit || exit $?;;
        *)
          echo -e "\n* Runit init detected but your distro is not supported\n"
          echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
          exit 1
        ;;
      esac
    else
      echo -e "\n* Runit init detected but /etc/os-release is unavailable\n"
      exit 1
    fi
  ;;
  systemd)
    systemd_unit=/etc/systemd/system/auto-cpufreq.service
    if [ -e "$systemd_unit" ] || [ -L "$systemd_unit" ]; then
      echo -e "\n* Stopping auto-cpufreq daemon (systemd) service"
      systemctl stop auto-cpufreq || exit $?
      # disable reloads systemd, which may unload the now-inactive unit.
      # stop already clears its failed state, so no reset-failed is needed.
      echo -e "\n* Disabling auto-cpufreq daemon (systemd) at boot"
      systemctl disable auto-cpufreq || exit $?
      echo -e "\n* Removing auto-cpufreq daemon (systemd) unit file"
      rm -f -- "$systemd_unit" || exit $?
    else
      echo -e "\n* auto-cpufreq systemd unit is already removed"
    fi

    # Keep reload after deletion. If it fails, a retry sees the missing owned
    # unit, skips the completed stop/disable steps, and retries this commit.
    echo -e "\n* Reloading systemd manager configuration"
    systemctl daemon-reload || exit $?
  ;;
  s6-svscan)
    s6_service_dir=/etc/s6/sv/auto-cpufreq
    s6_bundle_entry=/etc/s6/adminsv/default/contents.d/auto-cpufreq
    if [ -e "$s6_bundle_entry" ] || [ -L "$s6_bundle_entry" ]; then
      echo -e "\n* Disabling auto-cpufreq daemon (s6) at boot"
      s6-service delete default auto-cpufreq || exit $?
    fi
    echo -e "\n* Removing auto-cpufreq daemon (s6) unit file"
    rm -rf -- "$s6_service_dir" || exit $?

    # The bundle entry and service directory are durable progress markers.
    # Once absent, retry only the database reload that commits their removal.
    echo -e "\n* Update daemon service bundle (s6)"
    s6-db-reload || exit $?
  ;;
  *)
    echo -e "\n* Unsupported init system detected, could not remove the daemon"
    echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
    exit 1
  ;;
esac
