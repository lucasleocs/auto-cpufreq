#!/usr/bin/env bash
#
# auto-cpufreq daemon install script
# reference: https://github.com/AdnanHodzic/auto-cpufreq
# Thanks to https://github.com/errornonamer for openrc fix

MID="$((`tput cols` / 2))"

echo
printf "%0.s─" $(seq $(( (MID-(${#1}/2)-2) / 2 )))
printf " Running auto-cpufreq daemon install script "
printf "%0.s─" $(seq $(( (MID-(${#1}/2)-2) / 2 )))
echo; echo

# root check
if ((EUID != 0)); then
  echo; echo "Must be run as root (i.e: 'sudo $0')."; echo
  exit 1
fi

run_required() {
  "$@"
  status=$?
  if ((status != 0)); then
    echo -e "\n* Command failed with status ${status}: $*" >&2
    exit "$status"
  fi
}

case "$(ps h -o comm 1)" in
  dinit)
    echo -e "\n* Deploying auto-cpufreq (dinit) unit file"
    run_required cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-dinit /etc/dinit.d/auto-cpufreq

    echo -e "\n* Starting auto-cpufreq daemon (dinit) service"
    run_required dinitctl start auto-cpufreq
    echo -e "\n* Enabling auto-cpufreq daemon (dinit) at boot"
    run_required dinitctl enable auto-cpufreq
  ;;
  init)
    echo -e "\n* Deploying auto-cpufreq openrc unit file"
    run_required cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-openrc /etc/init.d/auto-cpufreq
    run_required chmod +x /etc/init.d/auto-cpufreq

    echo -e "\n* Starting auto-cpufreq daemon (openrc) service"
    run_required rc-service auto-cpufreq start
    echo -e "\n* Enabling auto-cpufreq daemon (openrc) at boot"
    run_required rc-update add auto-cpufreq
  ;;
  runit)
    # First argument is the "sv" path, second argument is the "service" path
    runit_ln() {
      service_dir="$1/sv/auto-cpufreq"
      service_link="$2/service/auto-cpufreq"

      echo -e "\n* Deploying auto-cpufreq (runit) unit file"
      run_required mkdir -p "$service_dir"
      run_required cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-runit "$service_dir/run"
      run_required chmod +x "$service_dir/run"

      echo -e "\n* Creating symbolic link ($service_link -> $service_dir)"
      if [ -L "$service_link" ]; then
        if [ "$(readlink "$service_link")" != "$service_dir" ]; then
          echo -e "\n* Existing runit service link points elsewhere: $service_link" >&2
          exit 1
        fi
      elif [ -e "$service_link" ]; then
        echo -e "\n* Existing runit service path is not the expected symlink: $service_link" >&2
        exit 1
      else
        run_required ln -s "$service_dir" "$2/service"
      fi

      echo -e "\n* Starting auto-cpufreq daemon (runit) service"
      run_required sv start auto-cpufreq
      run_required sv up auto-cpufreq
    }

    if [ -f /etc/os-release ]; then
      eval "$(cat /etc/os-release)"
      case $ID in
        void) runit_ln /etc /var;;
        artix) runit_ln /etc/runit /run/runit;;
        *)
          echo -e "\n* Runit init detected but your distro is not supported\n"
          echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
          exit 1
      esac
    else
      echo -e "\n* Runit init detected but /etc/os-release is unavailable\n"
      exit 1
    fi
  ;;
  systemd)
    echo -e "Deploying auto-cpufreq systemd unit file"
    run_required cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq.service /etc/systemd/system/auto-cpufreq.service

    echo -e "\n* Reloading systemd manager configuration"
    run_required systemctl daemon-reload

    echo -e "\n* Starting auto-cpufreq daemon (systemd) service"
    run_required systemctl start auto-cpufreq
    echo -e "\n* Enabling auto-cpufreq daemon (systemd) at boot"
    run_required systemctl enable auto-cpufreq
  ;;
  s6-svscan)
    echo -e "\n* Deploying auto-cpufreq (s6) unit file"
    run_required cp -r /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-s6 /etc/s6/sv/auto-cpufreq

    echo -e "\n* Add auto-cpufreq service (s6) to default bundle"
    run_required s6-service add default auto-cpufreq

    echo -e "\n* Starting auto-cpufreq daemon (s6) service"
    run_required s6-rc -u change auto-cpufreq default

    echo -e "\n* Update daemon service bundle (s6)"
    run_required s6-db-reload
  ;;
  *)
    echo -e "\n* Unsupported init system detected, could not install the daemon\n"
    echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
    exit 1
  ;;
esac
