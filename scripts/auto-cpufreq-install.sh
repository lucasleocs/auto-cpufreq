#!/usr/bin/env bash
#
# auto-cpufreq daemon install script
# reference: https://github.com/AdnanHodzic/auto-cpufreq
# Thanks to https://github.com/errornonamer for openrc fix

MID="$((`tput cols` / 2))"
SHARE_DIR=/opt/auto-cpufreq/current/share
[ -d "$SHARE_DIR/scripts" ] || SHARE_DIR=/usr/local/share/auto-cpufreq

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
    echo -e "\n* Enabling auto-cpufreq daemon ($1) at boot"
    [ -z "${3:-}" ] || $3 || return $?
    echo -e "\n* Starting auto-cpufreq daemon ($1) service"
    [ -z "${2:-}" ] || $2 || return $?
}

case "$(ps h -o comm 1)" in
  dinit) 
    echo -e "\n* Deploying auto-cpufreq (dinit) unit file"
    cp "$SHARE_DIR/scripts/auto-cpufreq-dinit" /etc/dinit.d/auto-cpufreq || exit $?

    auto_cpufreq_install "dinit" "dinitctl start auto-cpufreq" "dinitctl enable auto-cpufreq" || exit $?
  ;;
  init) 
    echo -e "\n* Deploying auto-cpufreq openrc unit file"
    cp "$SHARE_DIR/scripts/auto-cpufreq-openrc" /etc/init.d/auto-cpufreq || exit $?
    chmod +x /etc/init.d/auto-cpufreq || exit $?

    auto_cpufreq_install "openrc" "rc-service auto-cpufreq start" "rc-update add auto-cpufreq" || exit $?
  ;;
  runit)
    # First argument is the "sv" path, second argument is the "service" path
    runit_ln() {
      local active_link="$2/service/auto-cpufreq"
      local service_dir="$1/sv/auto-cpufreq"

      echo -e "\n* Deploying auto-cpufreq (runit) unit file"
      if [ -L "$service_dir" ] || { [ -e "$service_dir" ] && [ ! -d "$service_dir" ]; }; then
        echo "Error: Refusing to replace an unmanaged runit service path: $service_dir"
        return 1
      fi
      mkdir -p "$service_dir" || return $?
      cp "$SHARE_DIR/scripts/auto-cpufreq-runit" "$service_dir/run" || return $?
      chmod +x "$service_dir/run" || return $?

      echo -e "\n* Creating symbolic link ($active_link -> $service_dir)"
      if [ -L "$active_link" ]; then
        if [ "$(readlink "$active_link")" != "$service_dir" ]; then
          echo "Error: Refusing to replace an unmanaged runit service link: $active_link"
          return 1
        fi
      elif [ -e "$active_link" ]; then
        echo "Error: Refusing to replace an unmanaged runit service path: $active_link"
        return 1
      else
        ln -s "$service_dir" "$active_link" || return $?
      fi

      # `sv start` is the documented waiting form of `sv up`; one command is
      # sufficient both to request and verify that the service reached "up".
      sv start "$active_link" || return $?
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
    else
      echo -e "\n* Runit init detected but /etc/os-release is unavailable\n"
      exit 1
    fi
  ;;
  systemd)
    echo -e "Deploying auto-cpufreq systemd unit file"
    cp "$SHARE_DIR/scripts/auto-cpufreq.service" /etc/systemd/system/auto-cpufreq.service || exit $?

    echo -e "\n* Reloading systemd manager configuration"
    systemctl daemon-reload || exit $?

    auto_cpufreq_install "systemd" "systemctl start auto-cpufreq" "systemctl enable auto-cpufreq" || exit $?
  ;;
  s6-svscan)
    s6_service_dir=/etc/s6/sv/auto-cpufreq
    s6_bundle_entry=/etc/s6/adminsv/default/contents.d/auto-cpufreq
    echo -e "\n* Deploying auto-cpufreq (s6) unit file"
    if [ -L "$s6_service_dir" ] \
      || { [ -e "$s6_service_dir" ] && [ ! -d "$s6_service_dir" ]; }; then
      echo "Error: Refusing to replace an unmanaged s6 service path: $s6_service_dir"
      exit 1
    fi
    mkdir -p "$s6_service_dir" || exit $?
    cp -r "$SHARE_DIR/scripts/auto-cpufreq-s6/." "$s6_service_dir/" || exit $?

    echo -e "\n* Add auto-cpufreq service (s6) to default bundle"
    if [ ! -e "$s6_bundle_entry" ] && [ ! -L "$s6_bundle_entry" ]; then
      s6-service add default auto-cpufreq || exit $?
    fi

    echo -e "\n* Update daemon service bundle (s6)"
    s6-db-reload || exit $?

    auto_cpufreq_install "s6" "s6-rc -u change auto-cpufreq default" || exit $?
  ;;
  *)
    echo -e "\n* Unsupported init system detected, could not install the daemon\n"
    echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
    exit 1
  ;;
esac
