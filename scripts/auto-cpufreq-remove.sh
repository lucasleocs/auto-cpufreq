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

case "$(ps h -o comm 1)" in
  dinit)
    command -v dinitctl > /dev/null 2>&1 || fail_remove "dinit detected but dinitctl is unavailable."

    # --ignore-unstarted also treats an unloaded/missing service as stopped,
    # so an interrupted removal can safely retry this step.
    run_step "Stopping auto-cpufreq daemon (dinit) service" dinitctl stop --ignore-unstarted auto-cpufreq \
      || exit 1

    # dinitctl disable fails when the persistent boot dependency is already
    # absent. Treat only that state as a completed retry step; other dinit
    # failures must still abort removal.
    if [ -e /etc/dinit.d/auto-cpufreq ]; then
      echo -e "\n* Disabling auto-cpufreq daemon (dinit) at boot"
      if dinit_disable_output="$(LC_ALL=C dinitctl disable auto-cpufreq 2>&1)"; then
        [ -z "$dinit_disable_output" ] || printf '%s\n' "$dinit_disable_output"
      elif [[ "$dinit_disable_output" == *"service not currently enabled"* ]]; then
        echo "* auto-cpufreq daemon (dinit) is already disabled at boot"
      else
        [ -z "$dinit_disable_output" ] || printf '%s\n' "$dinit_disable_output" >&2
        fail_remove "Failed to disable the dinit service at boot."
      fi
    else
      echo -e "\n* auto-cpufreq dinit definition is already absent; skipping disable"
    fi

    echo -e "\n* Removing auto-cpufreq daemon (dinit) service definition"
    rm -f /etc/dinit.d/auto-cpufreq \
      || fail_remove "Failed to remove the dinit service definition."
  ;;

  init|openrc-init)
    if ! command -v rc-service > /dev/null 2>&1 || ! command -v rc-update > /dev/null 2>&1; then
      fail_remove "OpenRC-style init detected, but rc-service or rc-update is unavailable."
    fi

    # OpenRC's conditional options make both an absent service and an already
    # stopped service successful no-ops while preserving real stop failures.
    run_step "Stopping auto-cpufreq daemon (OpenRC) service" \
      rc-service --ifexists --ifstarted auto-cpufreq stop \
      || exit 1

    # The install helper adds auto-cpufreq only to the default runlevel. Remove
    # that exact boot dependency, and treat only the precise already-absent
    # state as a completed retry step; unrelated rc-update failures stay fatal.
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
    rm -f /etc/init.d/auto-cpufreq \
      || fail_remove "Failed to remove the OpenRC service definition."
  ;;

  runit)
    command -v sv > /dev/null 2>&1 || fail_remove "runit detected but sv is unavailable."

    rm_sv() {
      local service_root="$1"
      local active_root="$2"
      local service_dir="$service_root/sv/auto-cpufreq"
      local active_link="$active_root/service/auto-cpufreq"

      if [ -e "$active_link" ] || [ -L "$active_link" ]; then
        run_step "Stopping auto-cpufreq daemon (runit) service" sv stop "$active_link" \
          || exit 1
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

    systemd_property() {
      local property="$1"
      local value

      if ! value="$(systemctl show auto-cpufreq.service --no-pager --property="$property" --value 2>/dev/null)"; then
        return 1
      fi

      printf '%s\n' "$value"
    }

    systemd_load_state() {
      local value
      local unit_files

      if value="$(systemd_property LoadState)"; then
        printf '%s\n' "$value"
        return 0
      fi

      # systemctl show has returned different statuses for missing units across
      # systemd versions. An empty successful list-unit-files query proves that
      # the unit is absent without turning unrelated systemctl failures into
      # successful removal.
      if ! unit_files="$(systemctl list-unit-files auto-cpufreq.service --no-legend --no-pager 2>/dev/null)"; then
        return 1
      fi
      if [ -z "$unit_files" ]; then
        printf 'not-found\n'
        return 0
      fi

      return 1
    }

    load_state="$(systemd_load_state)" \
      || fail_remove "Failed to inspect the systemd service state."

    if [ "$load_state" = "not-found" ]; then
      echo -e "\n* auto-cpufreq systemd unit is already absent; skipping stop/disable"
    else
      active_state="$(systemd_property ActiveState)" \
        || fail_remove "Failed to inspect whether the systemd service is active."

      case "$active_state" in
        active|activating|reloading|deactivating)
          run_step "Stopping auto-cpufreq daemon (systemd) service" systemctl stop auto-cpufreq.service \
            || exit 1
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

    echo -e "\n* Removing auto-cpufreq daemon (systemd) unit file"
    rm -f /etc/systemd/system/auto-cpufreq.service \
      || fail_remove "Failed to remove the systemd service unit."

    run_step "Reloading systemd manager configuration" systemctl daemon-reload \
      || exit 1

    echo -e "\n* Resetting failed systemd unit state"
    systemctl reset-failed auto-cpufreq.service > /dev/null 2>&1 || true
  ;;

  s6-svscan)
    if command -v s6 > /dev/null 2>&1; then
      echo -e "\n* Stopping auto-cpufreq daemon (s6) service"
      s6 live stop auto-cpufreq
      s6_stop_status=$?
      if [ "$s6_stop_status" -ne 0 ] && [ "$s6_stop_status" -ne 3 ]; then
        fail_remove "Failed to stop the s6 service (status $s6_stop_status)."
      fi
      if [ "$s6_stop_status" -eq 3 ]; then
        echo "* auto-cpufreq is already absent from the live s6 database"
      fi

      echo -e "\n* Removing auto-cpufreq daemon (s6) service definition"
      rm -rf /etc/s6/sv/auto-cpufreq \
        || fail_remove "Failed to remove the s6 service definition."

      # s6-frontend keeps service sets separate from the store. Synchronizing
      # after deleting the definition removes auto-cpufreq from every set; a
      # commit plus live install then updates boot and live databases while
      # preserving unrelated service state as much as possible.
      run_step "Synchronizing the s6 service repository" s6 repository sync \
        || exit 1
      run_step "Committing the updated s6 service set" s6 set commit \
        || exit 1
      run_step "Installing the updated s6 live database" s6 live install \
        || exit 1
    else
      # Keep compatibility with pre-s6-frontend Artix installations. The
      # legacy path is also retry-safe after the service definition vanished.
      command -v s6-service > /dev/null 2>&1 || fail_remove "s6 detected but neither s6-frontend nor s6-service is available."
      command -v s6-db-reload > /dev/null 2>&1 || fail_remove "s6 detected but s6-db-reload is unavailable."
      command -v s6-rc > /dev/null 2>&1 || fail_remove "s6 detected but s6-rc is unavailable."

      if [ -d /etc/s6/sv/auto-cpufreq ]; then
        echo -e "\n* Stopping auto-cpufreq daemon (legacy s6) service"
        s6-rc -d change auto-cpufreq
        s6_stop_status=$?
        if [ "$s6_stop_status" -ne 0 ] && [ "$s6_stop_status" -ne 3 ]; then
          fail_remove "Failed to stop the legacy s6 service (status $s6_stop_status)."
        fi

        run_step "Removing auto-cpufreq service (legacy s6) from default bundle" \
          s6-service delete default auto-cpufreq \
          || exit 1
      else
        echo -e "\n* auto-cpufreq legacy s6 definition is already absent"
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
