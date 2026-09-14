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

if ((EUID != 0)); then
  echo; echo "Must be run as root (i.e: 'sudo $0')."; echo
  exit 1
fi

fail_remove() {
  echo "Error: $1" >&2
  exit 1
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

require_owned_file() {
  local installed="$1"
  local source="$2"
  local description="$3"

  if [ ! -f "$installed" ] || [ -L "$installed" ] \
    || [ ! -f "$source" ] || [ -L "$source" ] \
    || ! cmp -s -- "$installed" "$source"; then
    fail_remove "Refusing to remove $description at '$installed'; it no longer matches the source-installed artifact."
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
    command -v dinitctl > /dev/null 2>&1 || fail_remove "dinit detected but dinitctl is unavailable."

    dinit_unit=/etc/dinit.d/auto-cpufreq
    dinit_source=/opt/auto-cpufreq/current/share/scripts/auto-cpufreq-dinit
    if [ -e "$dinit_unit" ] || [ -L "$dinit_unit" ]; then
      require_owned_file "$dinit_unit" "$dinit_source" "dinit service definition"

      # --ignore-unstarted also treats an unloaded service as stopped, so an
      # interrupted removal can safely retry while the owned definition exists.
      run_step "Stopping auto-cpufreq daemon (dinit) service" dinitctl stop --ignore-unstarted auto-cpufreq \
        || exit 1

      # dinitctl disable fails when the persistent boot dependency is already
      # absent. Treat only that state as a completed retry step.
      echo -e "\n* Disabling auto-cpufreq daemon (dinit) at boot"
      if dinit_disable_output="$(LC_ALL=C dinitctl disable auto-cpufreq 2>&1)"; then
        [ -z "$dinit_disable_output" ] || printf '%s\n' "$dinit_disable_output"
      elif [[ "$dinit_disable_output" == *"service not currently enabled"* ]]; then
        echo "* auto-cpufreq daemon (dinit) is already disabled at boot"
      else
        [ -z "$dinit_disable_output" ] || printf '%s\n' "$dinit_disable_output" >&2
        fail_remove "Failed to disable the dinit service at boot."
      fi
      echo -e "\n* Removing auto-cpufreq daemon (dinit) service definition"
      rm -f "$dinit_unit" \
        || fail_remove "Failed to remove the dinit service definition."
    else
      echo -e "\n* auto-cpufreq dinit definition is already absent"
    fi
  ;;

  init|openrc-init)
    if ! command -v rc-service > /dev/null 2>&1 || ! command -v rc-update > /dev/null 2>&1; then
      fail_remove "OpenRC-style init detected, but rc-service or rc-update is unavailable."
    fi

    openrc_unit=/etc/init.d/auto-cpufreq
    openrc_source=/opt/auto-cpufreq/current/share/scripts/auto-cpufreq-openrc
    if [ -e "$openrc_unit" ] || [ -L "$openrc_unit" ]; then
      require_owned_file "$openrc_unit" "$openrc_source" "OpenRC service definition"

      # Conditional options make an already-stopped service a successful no-op
      # while preserving real stop failures.
      run_step "Stopping auto-cpufreq daemon (OpenRC) service" \
        rc-service --ifexists --ifstarted auto-cpufreq stop \
        || exit 1

      # Remove the exact default-runlevel dependency created by installation.
      echo -e "\n* Disabling auto-cpufreq daemon (OpenRC) at boot"
      if openrc_disable_output="$(LC_ALL=C rc-update delete auto-cpufreq default 2>&1)"; then
        [ -z "$openrc_disable_output" ] || printf '%s\n' "$openrc_disable_output"
      elif [[ "$openrc_disable_output" == *"service \`auto-cpufreq' is not in the runlevel \`default'"* ]]; then
        echo "* auto-cpufreq daemon (OpenRC) is already disabled at boot"
      else
        [ -z "$openrc_disable_output" ] || printf '%s\n' "$openrc_disable_output" >&2
        fail_remove "Failed to disable the OpenRC service at boot."
      fi

      echo -e "\n* Removing auto-cpufreq daemon (OpenRC) service definition"
      rm -f "$openrc_unit" \
        || fail_remove "Failed to remove the OpenRC service definition."
    else
      echo -e "\n* auto-cpufreq OpenRC definition is already absent"
    fi
  ;;

  runit)
    command -v sv > /dev/null 2>&1 || fail_remove "runit detected but sv is unavailable."

    rm_sv() {
      local service_root="$1"
      local active_root="$2"
      local service_dir="$service_root/sv/auto-cpufreq"
      local active_link="$active_root/service/auto-cpufreq"
      local source_run=/opt/auto-cpufreq/current/share/scripts/auto-cpufreq-runit

      if [ -e "$service_dir" ] || [ -L "$service_dir" ]; then
        [ -d "$service_dir" ] && [ ! -L "$service_dir" ] \
          || fail_remove "Refusing to remove a replaced runit service directory."
        require_owned_file "$service_dir/run" "$source_run" "runit service definition"
        if find "$service_dir" -mindepth 1 -maxdepth 1 \
          ! -name run ! -name supervise -print -quit | grep -q .; then
          fail_remove "Refusing to remove a runit service directory containing unexpected artifacts."
        fi
      fi

      if [ -e "$active_link" ] || [ -L "$active_link" ]; then
        if [ ! -L "$active_link" ] || [ "$(readlink "$active_link")" != "$service_dir" ]; then
          fail_remove "Refusing to remove a replaced runit activation path."
        fi
        if [ -d "$service_dir" ]; then
          run_step "Stopping auto-cpufreq daemon (runit) service" sv stop "$active_link" \
            || exit 1
        fi
      fi

      echo -e "\n* Disabling auto-cpufreq daemon (runit) at boot"
      rm -f "$active_link" \
        || fail_remove "Failed to remove the runit service symlink."

      echo -e "\n* Removing auto-cpufreq daemon (runit) service directory"
      rm -rf "$service_dir" \
        || fail_remove "Failed to remove the runit service directory."
    }

    if [ -r /etc/os-release ]; then
      . /etc/os-release
      case "$ID" in
        void) rm_sv /etc /var;;
        artix) rm_sv /etc/runit /run/runit;;
        *) fail_remove "Runit is detected, but this distribution does not have a supported runit service layout.";;
      esac
    else
      fail_remove "Runit is detected, but /etc/os-release is unavailable for service layout detection."
    fi
  ;;

  systemd)
    command -v systemctl > /dev/null 2>&1 || fail_remove "systemd detected but systemctl is unavailable."

    systemd_snapshot() {
      local output
      local status
      local load_state
      local unit_file_state
      local manager

      output="$(systemctl show auto-cpufreq.service --no-pager \
        --property=LoadState \
        --property=ActiveState \
        --property=UnitFileState \
        --property=FragmentPath 2>/dev/null)"
      status=$?
      load_state="$(printf '%s\n' "$output" | sed -n 's/^LoadState=//p')"

      if [ "$load_state" = "not-found" ]; then
        printf 'LoadState=not-found\nActiveState=inactive\nUnitFileState=\nFragmentPath=\n'
        return 0
      fi

      if [ "$status" -eq 0 ] \
        && [[ "$output" == *$'LoadState='* ]] \
        && [[ "$output" == *$'ActiveState='* ]] \
        && [[ "$output" == *$'UnitFileState='* ]] \
        && [[ "$output" == *$'FragmentPath='* ]]; then
        printf '%s\n' "$output"
        return 0
      fi

      # A few older systemd versions omit properties for a missing unit. Do
      # not turn a broken manager connection into not-found: both the unit-file
      # API and a separate manager query must succeed before accepting absence.
      unit_file_state="$(systemd_unit_file_state auto-cpufreq.service)" \
        || return 1
      [ "$unit_file_state" = "not-found" ] || return 1
      manager="$(systemctl show --no-pager --property=Version 2>/dev/null)" \
        || return 1
      [[ "$manager" == *$'Version='* ]] || return 1

      printf 'LoadState=not-found\nActiveState=inactive\nUnitFileState=\nFragmentPath=\n'
    }

    systemd_property() {
      printf '%s\n' "$systemd_state" | sed -n "s/^$1=//p"
    }

    systemd_state="$(systemd_snapshot)" \
      || fail_remove "Failed to inspect the systemd service state."
    load_state="$(systemd_property LoadState)"
    installed_unit="/etc/systemd/system/auto-cpufreq.service"
    source_unit="/opt/auto-cpufreq/current/share/scripts/auto-cpufreq.service"
    installed_unit_present=0

    # The unit file is a filesystem artifact even if systemd has not loaded it
    # yet. Verify and remove an interrupted installation without confusing
    # manager LoadState with ownership of the path on disk.
    if [ -e "$installed_unit" ] || [ -L "$installed_unit" ]; then
      if [ ! -f "$installed_unit" ] || [ -L "$installed_unit" ]; then
        fail_remove "Refusing to remove auto-cpufreq: the systemd service path was replaced or masked after installation."
      fi
      if [ ! -f "$source_unit" ] || [ -L "$source_unit" ]; then
        fail_remove "Unable to verify ownership of the installed systemd service definition."
      fi
      if ! cmp -s -- "$installed_unit" "$source_unit"; then
        fail_remove "Refusing to remove auto-cpufreq: the systemd service definition no longer matches the source-installed unit."
      fi
      installed_unit_present=1
    elif [ "$load_state" != "not-found" ]; then
      fail_remove "Refusing to remove auto-cpufreq: systemd loaded a service whose installed definition is absent."
    fi

    if [ "$load_state" = "not-found" ]; then
      echo -e "\n* auto-cpufreq systemd unit is already absent; skipping stop/disable"
    else
      fragment_path="$(systemd_property FragmentPath)" \
        || fail_remove "Failed to inspect the loaded systemd service definition."
      if [ "$fragment_path" != "$installed_unit" ]; then
        fail_remove "Refusing to remove auto-cpufreq: systemd loaded the service from '$fragment_path', not the source-installed unit."
      fi

      active_state="$(systemd_property ActiveState)" \
        || fail_remove "Failed to inspect whether the systemd service is active."

      case "$active_state" in
        active|activating|reloading|deactivating)
          run_step "Stopping auto-cpufreq daemon (systemd) service" systemctl stop auto-cpufreq.service \
            || exit 1
          systemd_state="$(systemd_snapshot)" \
            || fail_remove "Failed to refresh the systemd service state after stopping it."
          ;;
        inactive|failed|"")
          echo -e "\n* auto-cpufreq daemon (systemd) is already stopped"
          ;;
        *)
          fail_remove "Unexpected systemd ActiveState '$active_state'."
          ;;
      esac

      active_state="$(systemd_property ActiveState)" \
        || fail_remove "Failed to verify that the systemd service stopped."
      case "$active_state" in
        active|activating|reloading|deactivating)
          fail_remove "auto-cpufreq systemd service is still active after removal was requested."
          ;;
      esac

      unit_file_state="$(systemd_property UnitFileState)" \
        || fail_remove "Failed to inspect the systemd boot activation state."

      case "$unit_file_state" in
        enabled|enabled-runtime|linked|linked-runtime)
          run_step "Disabling auto-cpufreq daemon (systemd) at boot" systemctl disable auto-cpufreq.service \
            || exit 1
          systemd_state="$(systemd_snapshot)" \
            || fail_remove "Failed to refresh the systemd service state after disabling it."
          unit_file_state="$(systemd_property UnitFileState)" \
            || fail_remove "Failed to verify the systemd boot activation state."
          case "$unit_file_state" in
            enabled|enabled-runtime|linked|linked-runtime)
              fail_remove "auto-cpufreq systemd service is still enabled after removal was requested."
              ;;
          esac
          ;;
        disabled|static|indirect|generated|transient|alias|masked|masked-runtime|"")
          echo -e "\n* auto-cpufreq daemon (systemd) is already disabled at boot"
          ;;
        *)
          fail_remove "Unexpected systemd UnitFileState '$unit_file_state'."
          ;;
      esac
    fi

    if [ "$installed_unit_present" -eq 1 ]; then
      echo -e "\n* Removing auto-cpufreq daemon (systemd) unit file"
      rm -f "$installed_unit" \
        || fail_remove "Failed to remove the systemd service unit."
    fi

    run_step "Reloading systemd manager configuration" systemctl daemon-reload \
      || exit 1

    echo -e "\n* Resetting failed systemd unit state"
    systemctl reset-failed auto-cpufreq.service > /dev/null 2>&1 || true
  ;;

  s6-svscan)
    if command -v s6 > /dev/null 2>&1; then
      if [ -e /etc/s6/sv/auto-cpufreq ] || [ -L /etc/s6/sv/auto-cpufreq ]; then
        [ -d /etc/s6/sv/auto-cpufreq ] && [ ! -L /etc/s6/sv/auto-cpufreq ] \
          || fail_remove "Refusing to remove a replaced s6 service directory."
        require_owned_file /etc/s6/sv/auto-cpufreq/run \
          /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-s6/run \
          "s6 run definition"
        require_owned_file /etc/s6/sv/auto-cpufreq/type \
          /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-s6/type \
          "s6 type definition"
        if find /etc/s6/sv/auto-cpufreq -mindepth 1 -maxdepth 1 \
          ! -name run ! -name type -print -quit | grep -q .; then
          fail_remove "Refusing to remove an s6 service directory containing unexpected artifacts."
        fi
      fi

      echo -e "\n* Stopping auto-cpufreq daemon (s6) service"
      s6 live stop auto-cpufreq
      s6_stop_status=$?
      if [ "$s6_stop_status" -ne 0 ] && [ "$s6_stop_status" -ne 3 ]; then
        fail_remove "Failed to stop the s6 service (status $s6_stop_status)."
      fi
      if [ "$s6_stop_status" -eq 3 ]; then
        echo "* auto-cpufreq is already absent from the live s6 database"
      fi

      if [ -d /etc/s6/sv/auto-cpufreq ]; then
        # Mask in the working set while the store definition still exists, then
        # install that set so the live database no longer references it.
        run_step "Masking auto-cpufreq in the s6 service set" s6 set mask auto-cpufreq \
          || exit 1
        run_step "Committing the updated s6 service set" s6 set commit -f \
          || exit 1
        run_step "Installing the updated s6 live database" s6 live install \
          || exit 1

        echo -e "\n* Removing auto-cpufreq daemon (s6) service definition"
        rm -rf /etc/s6/sv/auto-cpufreq \
          || fail_remove "Failed to remove the s6 service definition."
        run_step "Synchronizing the s6 service repository" s6 repository sync \
          || exit 1
      else
        # A retry may begin after the store was removed. Sync first, then force
        # a fresh compiled set so live install also removes any stale database
        # entry left by an interrupted older removal.
        run_step "Synchronizing the s6 service repository" s6 repository sync \
          || exit 1
        run_step "Recompiling the s6 service set" s6 set commit -f \
          || exit 1
        run_step "Installing the updated s6 live database" s6 live install \
          || exit 1
      fi
    else
      # Keep compatibility with pre-s6-frontend Artix installations. The
      # legacy path is also retry-safe after the service definition vanished.
      command -v s6-service > /dev/null 2>&1 || fail_remove "s6 detected but neither s6-frontend nor s6-service is available."
      command -v s6-db-reload > /dev/null 2>&1 || fail_remove "s6 detected but s6-db-reload is unavailable."
      command -v s6-rc > /dev/null 2>&1 || fail_remove "s6 detected but s6-rc is unavailable."

      if [ -e /etc/s6/sv/auto-cpufreq ] || [ -L /etc/s6/sv/auto-cpufreq ]; then
        [ -d /etc/s6/sv/auto-cpufreq ] && [ ! -L /etc/s6/sv/auto-cpufreq ] \
          || fail_remove "Refusing to remove a replaced legacy s6 service directory."
        require_owned_file /etc/s6/sv/auto-cpufreq/run \
          /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-s6/run \
          "legacy s6 run definition"
        require_owned_file /etc/s6/sv/auto-cpufreq/type \
          /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-s6/type \
          "legacy s6 type definition"
        if find /etc/s6/sv/auto-cpufreq -mindepth 1 -maxdepth 1 \
          ! -name run ! -name type -print -quit | grep -q .; then
          fail_remove "Refusing to remove a legacy s6 service directory containing unexpected artifacts."
        fi
      fi

      if [ -d /etc/s6/sv/auto-cpufreq ]; then
        echo -e "\n* Stopping auto-cpufreq daemon (legacy s6) service"
        s6-rc -d change auto-cpufreq
        s6_stop_status=$?
        if [ "$s6_stop_status" -ne 0 ] && [ "$s6_stop_status" -ne 3 ]; then
          fail_remove "Failed to stop the legacy s6 service (status $s6_stop_status)."
        fi
      else
        echo -e "\n* auto-cpufreq legacy s6 definition is already absent"
      fi

      # s6-service tracks default-bundle membership separately from the service
      # definition. Remove that membership even when a previous retry already
      # deleted /etc/s6/sv/auto-cpufreq.
      legacy_bundle_entry="/etc/s6/adminsv/default/contents.d/auto-cpufreq"
      if [ -e "$legacy_bundle_entry" ]; then
        run_step "Removing auto-cpufreq service (legacy s6) from default bundle" \
          s6-service delete default auto-cpufreq \
          || exit 1
      else
        echo -e "\n* auto-cpufreq legacy s6 default-bundle entry is already absent"
      fi

      echo -e "\n* Removing auto-cpufreq daemon (legacy s6) service definition"
      rm -rf /etc/s6/sv/auto-cpufreq \
        || fail_remove "Failed to remove the legacy s6 service definition."

      # Reload even on a retry after the definition was already removed: the
      # previous attempt may have failed between deletion and database reload.
      run_step "Updating legacy s6 service database" s6-db-reload \
        || exit 1
    fi
  ;;

  *)
    fail_remove "Unsupported init system detected; auto-cpufreq daemon was not removed."
  ;;
esac
