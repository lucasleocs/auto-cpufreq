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

if ((EUID != 0)); then
  echo; echo "Must be run as root (i.e: 'sudo $0')."; echo
  exit 1
fi

fail_install() {
  echo "Error: $1" >&2
  exit 1
}

refuse_existing_path() {
  local path="$1"
  local description="$2"

  if [ -e "$path" ] || [ -L "$path" ]; then
    fail_install "Refusing to overwrite existing $description at '$path'."
  fi
}

publish_owned_file() {
  local source="$1"
  local destination="$2"
  local mode="$3"
  local description="$4"
  local temporary

  refuse_existing_path "$destination" "$description"
  if [ ! -f "$source" ] || [ -L "$source" ]; then
    fail_install "Source for $description is not a regular file."
  fi

  temporary="$(mktemp "${destination}.tmp.XXXXXX")" \
    || fail_install "Failed to create a temporary $description."
  if ! install -m "$mode" -- "$source" "$temporary"; then
    rm -f -- "$temporary"
    fail_install "Failed to prepare $description."
  fi

  # A hardlink publishes only the complete file and fails if another artifact
  # appeared after preflight; no reader can observe a partially copied unit.
  if ! ln -- "$temporary" "$destination"; then
    rm -f -- "$temporary"
    fail_install "Failed to publish $description without overwriting it."
  fi
  rm -f -- "$temporary" \
    || fail_install "Failed to remove the temporary $description."
}

run_step() {
  local description="$1"
  shift

  echo -e "\n* $description"
  if ! "$@"; then
    echo "Error: $description failed." >&2
    return 1
  fi
}

systemd_unit_file_state() {
  local state

  state="$(LC_ALL=C systemctl is-enabled "$1" 2>/dev/null)"
  case "$state" in
    enabled|enabled-runtime|linked|linked-runtime|alias|masked|masked-runtime|static|indirect|disabled|generated|transient|not-found)
      printf '%s\n' "$state"
      return 0
      ;;
  esac

  return 1
}

case "$(ps h -o comm 1)" in
  dinit)
    command -v dinitctl > /dev/null 2>&1 || fail_install "dinit detected but dinitctl is unavailable."

    refuse_existing_path /etc/dinit.d/auto-cpufreq "dinit service definition"

    echo -e "\n* Deploying auto-cpufreq (dinit) unit file"
    publish_owned_file \
      /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-dinit \
      /etc/dinit.d/auto-cpufreq 644 "dinit service definition"

    # Dinit caches loaded service descriptions. A stopped service from an
    # older source installation may still be loaded after its file was removed;
    # reload it now so the process-type definition is used before start.
    if dinitctl status auto-cpufreq > /dev/null 2>&1; then
      run_step "Reloading auto-cpufreq daemon (dinit) service definition" dinitctl reload auto-cpufreq \
        || exit 1
    fi

    run_step "Starting auto-cpufreq daemon (dinit) service" dinitctl start auto-cpufreq \
      || exit 1
    if ! run_step "Enabling auto-cpufreq daemon (dinit) at boot" dinitctl enable auto-cpufreq; then
      dinitctl stop --ignore-unstarted auto-cpufreq > /dev/null 2>&1 || true
      exit 1
    fi
  ;;

  init|openrc-init)
    if ! command -v rc-service > /dev/null 2>&1 || ! command -v rc-update > /dev/null 2>&1; then
      fail_install "OpenRC-style init detected, but rc-service or rc-update is unavailable."
    fi

    refuse_existing_path /etc/init.d/auto-cpufreq "OpenRC service definition"

    echo -e "\n* Deploying auto-cpufreq OpenRC unit file"
    publish_owned_file \
      /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-openrc \
      /etc/init.d/auto-cpufreq 755 "OpenRC service definition"

    run_step "Starting auto-cpufreq daemon (OpenRC) service" rc-service auto-cpufreq start \
      || exit 1
    if ! run_step "Enabling auto-cpufreq daemon (OpenRC) at boot" rc-update add auto-cpufreq default; then
      rc-service auto-cpufreq stop > /dev/null 2>&1 || true
      exit 1
    fi
  ;;

  runit)
    command -v sv > /dev/null 2>&1 || fail_install "runit detected but sv is unavailable."

    runit_ln() {
      local service_root="$1"
      local active_root="$2"
      local service_dir="$service_root/sv/auto-cpufreq"
      local active_link="$active_root/service/auto-cpufreq"

      [ -d "$active_root/service" ] \
        || fail_install "Runit service directory '$active_root/service' does not exist."

      refuse_existing_path "$service_dir" "runit service directory"
      refuse_existing_path "$active_link" "runit activation path"

      echo -e "\n* Deploying auto-cpufreq (runit) service directory"
      staged_service_dir="$(mktemp -d "${service_dir}.tmp.XXXXXX")" \
        || fail_install "Failed to stage the runit service directory."
      if ! install -m 755 /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-runit \
        "$staged_service_dir/run"; then
        rm -rf -- "$staged_service_dir"
        fail_install "Failed to prepare the runit service definition."
      fi
      refuse_existing_path "$service_dir" "runit service directory"
      if ! mv -Tn -- "$staged_service_dir" "$service_dir" \
        || [ -e "$staged_service_dir" ]; then
        rm -rf -- "$staged_service_dir"
        fail_install "Failed to publish the runit service directory."
      fi

      echo -e "\n* Enabling auto-cpufreq daemon (runit) at boot"
      ln -s "$service_dir" "$active_link" \
        || fail_install "Failed to enable the runit service."

      if ! run_step "Starting auto-cpufreq daemon (runit) service" sv start "$active_link"; then
        exit 1
      fi
    }

    if [ -r /etc/os-release ]; then
      . /etc/os-release
      case "$ID" in
        void) runit_ln /etc /var;;
        artix) runit_ln /etc/runit /run/runit;;
        *) fail_install "Runit is detected, but this distribution does not have a supported runit service layout.";;
      esac
    else
      fail_install "Runit is detected, but /etc/os-release is unavailable for service layout detection."
    fi
  ;;

  systemd)
    command -v systemctl > /dev/null 2>&1 || fail_install "systemd detected but systemctl is unavailable."

    existing_unit="$(systemd_unit_file_state auto-cpufreq.service)" \
      || fail_install "Failed to inspect existing systemd service definitions."
    if [ "$existing_unit" != "not-found" ] \
      || [ -e /etc/systemd/system/auto-cpufreq.service ] \
      || [ -L /etc/systemd/system/auto-cpufreq.service ]; then
      fail_install "Refusing to overwrite existing systemd service definition for auto-cpufreq."
    fi

    echo -e "\n* Deploying auto-cpufreq systemd unit file"
    publish_owned_file \
      /usr/local/share/auto-cpufreq/scripts/auto-cpufreq.service \
      /etc/systemd/system/auto-cpufreq.service 644 \
      "systemd service definition"

    run_step "Reloading systemd manager configuration" systemctl daemon-reload || exit 1
    run_step "Starting auto-cpufreq daemon (systemd) service" systemctl start auto-cpufreq.service || exit 1
    if ! run_step "Enabling auto-cpufreq daemon (systemd) at boot" systemctl enable auto-cpufreq.service; then
      systemctl stop auto-cpufreq.service > /dev/null 2>&1 || true
      systemctl disable auto-cpufreq.service > /dev/null 2>&1 || true
      exit 1
    fi
  ;;

  s6-svscan)
    if command -v s6 > /dev/null 2>&1; then
      s6_backend="frontend"
    else
      command -v s6-service > /dev/null 2>&1 || fail_install "s6 detected but neither s6-frontend nor s6-service is available."
      command -v s6-db-reload > /dev/null 2>&1 || fail_install "s6 detected but s6-db-reload is unavailable."
      command -v s6-rc > /dev/null 2>&1 || fail_install "s6 detected but s6-rc is unavailable."
      s6_backend="legacy"
    fi

    refuse_existing_path /etc/s6/sv/auto-cpufreq "s6 service definition"
    refuse_existing_path /etc/s6/adminsv/default/contents.d/auto-cpufreq "s6 default-bundle membership"

    echo -e "\n* Deploying auto-cpufreq (s6) service definition"
    staged_service_dir="$(mktemp -d /etc/s6/sv/auto-cpufreq.tmp.XXXXXX)" \
      || fail_install "Failed to stage the s6 service directory."
    if ! cp -r /usr/local/share/auto-cpufreq/scripts/auto-cpufreq-s6/. \
      "$staged_service_dir/"; then
      rm -rf -- "$staged_service_dir"
      fail_install "Failed to prepare the s6 service definition."
    fi
    refuse_existing_path /etc/s6/sv/auto-cpufreq "s6 service definition"
    if ! mv -Tn -- "$staged_service_dir" /etc/s6/sv/auto-cpufreq \
      || [ -e "$staged_service_dir" ]; then
      rm -rf -- "$staged_service_dir"
      fail_install "Failed to publish the s6 service directory."
    fi
    if [ "$s6_backend" = "frontend" ]; then
      run_step "Synchronizing the s6 service repository" s6 repository sync || exit 1
      run_step "Enabling auto-cpufreq daemon (s6) at boot" s6 set enable auto-cpufreq || exit 1
      run_step "Committing the updated s6 service set" s6 set commit || exit 1
      run_step "Installing the updated s6 live database" s6 live install || exit 1
      run_step "Starting auto-cpufreq daemon (s6) service" s6 live start auto-cpufreq || exit 1
    else
      run_step "Adding auto-cpufreq service (legacy s6) to default bundle" s6-service add default auto-cpufreq || exit 1
      if ! run_step "Updating legacy s6 service database" s6-db-reload; then
        s6-service delete default auto-cpufreq > /dev/null 2>&1 || true
        exit 1
      fi
      if ! run_step "Starting auto-cpufreq daemon (legacy s6) service" s6-rc -u change auto-cpufreq; then
        s6-service delete default auto-cpufreq > /dev/null 2>&1 || true
        s6-db-reload > /dev/null 2>&1 || true
        exit 1
      fi
    fi
  ;;

  *) fail_install "Unsupported init system detected; auto-cpufreq daemon was not installed.";;
esac
