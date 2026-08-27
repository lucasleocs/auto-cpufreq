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

# First argument is the init name, second argument is the start command, third argument is the enable command
function auto_cpufreq_install {
    if [ -n "${2:-}" ]; then
        echo -e "\n* Starting auto-cpufreq daemon ($1) service"
        $2 || return $?
    fi

    if [ -n "${3:-}" ]; then
        echo -e "\n* Enabling auto-cpufreq daemon ($1) at boot"
        $3 || return $?
    fi
}

case "$(ps h -o comm 1)" in
  dinit) 
    echo -e "\n* Deploying auto-cpufreq (dinit) unit file"
    cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-dinit /etc/dinit.d/auto-cpufreq || exit $?

    auto_cpufreq_install "dinit" "dinitctl start auto-cpufreq" "dinitctl enable auto-cpufreq" || exit $?
  ;;
  init) 
    echo -e "\n* Deploying auto-cpufreq openrc unit file"
    cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-openrc /etc/init.d/auto-cpufreq || exit $?
    chmod +x /etc/init.d/auto-cpufreq || exit $?

    auto_cpufreq_install "openrc" "rc-service auto-cpufreq start" "rc-update add auto-cpufreq" || exit $?
  ;;
  runit)
    # First argument is the "sv" path, second argument is the "service" path
    runit_ln() {
      echo -e "\n* Deploying auto-cpufreq (runit) unit file"
      mkdir -p "$1"/sv/auto-cpufreq || return $?
      cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-runit "$1"/sv/auto-cpufreq/run || return $?
      chmod +x "$1"/sv/auto-cpufreq/run || return $?

      echo -e "\n* Creating symbolic link ($2/service/auto-cpufreq -> $1/sv/auto-cpufreq)"
      ln -sfn "$1"/sv/auto-cpufreq "$2"/service/auto-cpufreq || return $?

      sv start auto-cpufreq || return $?
      sv up auto-cpufreq || return $?
    }

    if [ -f /etc/os-release ];then
      eval "$(cat /etc/os-release)"
      case $ID in
        void) runit_ln /etc /var || exit $?;;
        artix) runit_ln /etc/runit /run/runit || exit $?;;
        *)
          echo -e "\n* Runit init detected but your distro is not supported\n"
          echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
          exit 1
      esac
    fi
  ;;
  systemd)
    echo -e "Deploying auto-cpufreq systemd unit file"
    cp /usr/local/share/auto-cpufreq/scripts/auto-cpufreq.service /etc/systemd/system/auto-cpufreq.service || exit $?

    echo -e "\n* Reloading systemd manager configuration"
    systemctl daemon-reload || exit $?

    auto_cpufreq_install "systemd" "systemctl start auto-cpufreq" "systemctl enable auto-cpufreq" || exit $?
  ;;
  s6-svscan)
    cp -r /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-s6 /etc/s6/sv/auto-cpufreq || exit $?

    echo -e "\n* Add auto-cpufreq service (s6) to default bundle"
    s6-service add default auto-cpufreq || exit $?

    auto_cpufreq_install "s6" "s6-rc -u change auto-cpufreq default" || exit $?

    echo -e "\n* Update daemon service bundle (s6)"
    s6-db-reload || exit $?
  ;;
  *)
    echo -e "\n* Unsupported init system detected, could not install the daemon\n"
    echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
    exit 1
  ;;
esac
