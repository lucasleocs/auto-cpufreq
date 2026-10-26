#!/usr/bin/env bash
#
# auto-cpufreq daemon removal script
# reference: https://github.com/AdnanHodzic/auto-cpufreq
# Thanks to https://github.com/errornonamer for openrc fix

MID="$((`tput cols` / 2))"
SHARE_DIR=/opt/auto-cpufreq/current/share
[ -d "$SHARE_DIR/scripts" ] || SHARE_DIR=/usr/local/share/auto-cpufreq

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
    # A replacement made while stopping/disabling must not be deleted.
    if ! service_file_is_managed "$4" "$5"; then
      echo "Error: Refusing to remove an unmanaged $1 unit: $4"
      return 1
    fi
    echo -e "\n* Removing auto-cpufreq daemon ($1) unit file"
    rm -f -- "$4" || return $?
}

# Dinit may append "disabled for now; persistent disable failed." after
# an ENOENT diagnostic. Accept that exact text only if the link is truly
# absent; preserve all other failures as errors.
function dinit_disable {
    local output status entry primary note=""
    output="$(dinitctl disable auto-cpufreq 2>&1)"
    status=$?
    [ -z "$output" ] || printf '%s\n' "$output"
    [ "$status" -eq 0 ] && return 0
    primary="${output%%$'\n'*}"
    if [ "$primary" != "$output" ]; then
        note="${output#*$'\n'}"
        [ "$note" = "dinitctl: note: service was disabled for now; persistent disable failed." ] \
            || return "$status"
    fi
    case "$primary" in
      "dinitctl: could not unlink dependency entry "*": No such file or directory")
        entry="${primary#dinitctl: could not unlink dependency entry }"
        entry="${entry%: No such file or directory}"
        if [ "$(basename "$entry")" = "auto-cpufreq" ] \
            && [ -d "$(dirname "$entry")" ] \
            && [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
            return 0
        fi
        ;;
    esac
    return "$status"
}

function service_file_is_managed {
    local target="$1" template="$2"
    # Missing paths are legitimate partial-install/removal retry states.
    if [ ! -e "$target" ] && [ ! -L "$target" ]; then
      return 0
    fi
    [ -f "$target" ] && [ ! -L "$target" ] \
      && [ -f "$template" ] \
      && cmp -s -- "$template" "$target"
}

function directory_contains_only {
    local allowed allowed_name entry
    local directory="$1"
    shift

    for entry in "$directory"/* "$directory"/.[!.]* "$directory"/..?*; do
      [ -e "$entry" ] || [ -L "$entry" ] || continue
      allowed=false
      for allowed_name in "$@"; do
        if [ "${entry##*/}" = "$allowed_name" ]; then
          allowed=true
          break
        fi
      done
      $allowed || return 1
    done
    return 0
}

function runit_service_is_managed {
    local managed_run="$SHARE_DIR/scripts/auto-cpufreq-runit"
    local service_dir="$1"

    [ -e "$service_dir" ] || [ -L "$service_dir" ] || return 0
    [ -d "$service_dir" ] && [ ! -L "$service_dir" ] || return 1
    directory_contains_only "$service_dir" run supervise || return 1

    if [ -e "$service_dir/run" ] || [ -L "$service_dir/run" ]; then
      [ -f "$service_dir/run" ] \
        && [ ! -L "$service_dir/run" ] \
        && cmp -s -- "$managed_run" "$service_dir/run" \
        || return 1
    elif [ -e "$service_dir/supervise" ] || [ -L "$service_dir/supervise" ]; then
      # A supervise-only directory is not proof of an owned service.
      # Match the install-side predicate while allowing empty retry state.
      return 1
    fi

    [ ! -e "$service_dir/supervise" ] \
      && [ ! -L "$service_dir/supervise" ] \
      || [ -d "$service_dir/supervise" ]
}

function s6_service_is_managed {
    local managed_dir="$SHARE_DIR/scripts/auto-cpufreq-s6"
    local service_dir="$1"
    local service_file

    [ -e "$service_dir" ] || [ -L "$service_dir" ] || return 0
    [ -d "$service_dir" ] && [ ! -L "$service_dir" ] || return 1
    directory_contains_only "$service_dir" run type || return 1

    for service_file in run type; do
      if [ -e "$service_dir/$service_file" ] \
        || [ -L "$service_dir/$service_file" ]; then
        [ -f "$service_dir/$service_file" ] \
          && [ ! -L "$service_dir/$service_file" ] \
          && cmp -s -- "$managed_dir/$service_file" "$service_dir/$service_file" \
          || return 1
      fi
    done
    return 0
}

function s6_bundle_entry_is_managed {
    local entry="$1"
    # Artix s6-service stores bundle membership as an empty regular file.
    # Never remove or reuse a symlink, directory or administrative contents.
    if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
      return 0
    fi
    [ -f "$entry" ] && [ ! -L "$entry" ] && [ ! -s "$entry" ]
}

function openrc_disable {
    local current_membership current_runlevel current_runlevels
    local current_separator current_service delete_status
    local membership runlevel runlevels separator service
    local still_registered

    membership="$(rc-update show)" || return $?
    while read -r service separator runlevels; do
      [ "$service" = "auto-cpufreq" ] && [ "$separator" = "|" ] || continue
      # Remove only memberships that still exist. OpenRC reports failure when
      # asked to delete an already-absent service, which must remain retry-safe.
      for runlevel in $runlevels; do
        rc-update del auto-cpufreq "$runlevel"
        delete_status=$?
        [ "$delete_status" -eq 0 ] && continue

        # Another removal may complete between show and del. Suppress only
        # that completed step; retain real OpenRC failures.
        current_membership="$(rc-update show)" || return $?
        still_registered=false
        while read -r current_service current_separator current_runlevels; do
          [ "$current_service" = "auto-cpufreq" ] \
            && [ "$current_separator" = "|" ] || continue
          for current_runlevel in $current_runlevels; do
            [ "$current_runlevel" = "$runlevel" ] && still_registered=true
          done
        done <<< "$current_membership"
        $still_registered && return "$delete_status"
      done
    done <<< "$membership"
    return 0
}

case "$(ps h -o comm 1)" in
  dinit)
    service_file=/etc/dinit.d/auto-cpufreq
    managed_file="$SHARE_DIR/scripts/auto-cpufreq-dinit"
    if [ -e "$service_file" ] || [ -L "$service_file" ]; then
      if ! service_file_is_managed "$service_file" "$managed_file"; then
        echo "Error: Refusing to remove an unmanaged dinit unit: $service_file"
        exit 1
      fi
      auto_cpufreq_remove "dinit" "dinitctl stop --ignore-unstarted auto-cpufreq" "dinit_disable" "$service_file" "$managed_file" || exit $?
    else
      echo -e "\n* auto-cpufreq dinit service is already removed"
    fi
  ;;
  init)
    service_file=/etc/init.d/auto-cpufreq
    managed_file="$SHARE_DIR/scripts/auto-cpufreq-openrc"
    if [ -e "$service_file" ] || [ -L "$service_file" ]; then
      if ! service_file_is_managed "$service_file" "$managed_file"; then
        echo "Error: Refusing to remove an unmanaged OpenRC unit: $service_file"
        exit 1
      fi
      auto_cpufreq_remove "openrc" "rc-service --ifexists --ifstarted auto-cpufreq stop" "openrc_disable" "$service_file" "$managed_file" || exit $?
    else
      echo -e "\n* auto-cpufreq OpenRC service is already removed"
    fi
  ;;
  runit)
    # First argument is the "sv" path, second argument is the "service" path
    rm_sv() {
      local active_link="$2/service/auto-cpufreq"
      local service_dir="$1/sv/auto-cpufreq"

      # Verify every persistent entry before stopping the service. Refusing an
      # altered definition keeps host-owned runit configuration recoverable.
      if ! runit_service_is_managed "$service_dir"; then
        echo "Error: Refusing to remove an unmanaged runit service path: $service_dir"
        return 1
      fi
      if [ -e "$active_link" ] || [ -L "$active_link" ]; then
        if [ ! -L "$active_link" ] \
          || [ "$(readlink "$active_link")" != "$service_dir" ]; then
          echo "Error: Refusing to remove an unmanaged runit service link: $active_link"
          return 1
        fi
        echo -e "\n* Stopping auto-cpufreq daemon (runit) service"
        if [ -d "$service_dir" ]; then
          sv stop "$active_link" || return $?
          # An administrator may have replaced the service during sv stop.
          if ! runit_service_is_managed "$service_dir"; then
            echo "Error: Runit service changed during stop: $service_dir" >&2
            return 1
          fi
        fi
        echo -e "\n* Disabling auto-cpufreq daemon (runit) at boot"
        rm -f -- "$active_link" || return $?
      fi
      if [ -d "$service_dir" ]; then
        # sv stop waits for the daemon, not its runsv supervisor. Once
        # detached from runsvdir, wait for an initialized supervisor to exit.
        # A partial attempt may leave an empty supervise directory without
        # its FIFO: sv shutdown cannot address that state and returns 1.
        if [ -e "$service_dir/supervise" ] || [ -L "$service_dir/supervise" ]; then
          if [ -p "$service_dir/supervise/ok" ]; then
            if ! sv shutdown "$service_dir"; then
              # runsvdir can retire the supervisor after the FIFO check.
              # Continue only if both the activation link and FIFO are gone;
              # do not suppress errors while supervision remains reachable.
              if [ -e "$active_link" ] || [ -L "$active_link" ] \
                  || [ -e "$service_dir/supervise/ok" ] \
                  || [ -L "$service_dir/supervise/ok" ]; then
                echo "Error: Runit supervisor shutdown failed while supervision remains." >&2
                return 1
              fi
            fi
          elif [ -e "$service_dir/supervise/ok" ] || [ -L "$service_dir/supervise/ok" ]; then
            echo "Error: Unexpected runit supervisor state: $service_dir/supervise/ok" >&2
            return 1
          fi
        fi
        if ! runit_service_is_managed "$service_dir"; then
          echo "Error: Refusing to remove a changed runit service: $service_dir" >&2
          return 1
        fi
        echo -e "\n* Removing auto-cpufreq daemon (runit) unit file"
        # Remove only entries owned by this service. If another entry appears
        # after validation, rmdir fails instead of recursively deleting it.
        # Retain the identifying run script if supervise cleanup fails.
        rm -rf -- "$service_dir/supervise" || return $?
        rm -f -- "$service_dir/run" || return $?
        rmdir -- "$service_dir" || return $?
      fi
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
      managed_systemd_unit="$SHARE_DIR/scripts/auto-cpufreq.service"
      if [ -L "$systemd_unit" ] \
        || [ ! -f "$managed_systemd_unit" ] \
        || ! cmp -s -- "$managed_systemd_unit" "$systemd_unit"; then
        echo "Error: Refusing to remove a replaced or unmanaged systemd unit: $systemd_unit"
        exit 1
      fi
      echo -e "\n* Stopping auto-cpufreq daemon (systemd) service"
      systemctl stop auto-cpufreq || exit $?
      # disable reloads systemd, which may unload the now-inactive unit.
      # stop already clears its failed state, so no reset-failed is needed.
      echo -e "\n* Disabling auto-cpufreq daemon (systemd) at boot"
      systemctl disable auto-cpufreq || exit $?
      # The service unit can be replaced while stop/disable is running.
      # Do not unlink a new administrator-owned unit after the preflight.
      if ! service_file_is_managed "$systemd_unit" "$managed_systemd_unit"; then
        echo "Error: Refusing to remove a replaced or unmanaged systemd unit: $systemd_unit"
        exit 1
      fi
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
    s6_removing_dir=/etc/s6/sv/.auto-cpufreq-removing
    s6_bundle_entry=/etc/s6/adminsv/default/contents.d/auto-cpufreq
    # Use the same stable directory inode as installation; the lock vanishes
    # with the process, while a pending removal survives for the next retry.
    exec 8</etc/s6/sv || exit $?
    if ! flock -n 8; then
      echo "Error: Cannot lock the s6 source directory; flock is required and no other auto-cpufreq s6 operation may be running."
      exit 1
    fi
    if ! s6_service_is_managed "$s6_removing_dir"; then
      echo "Error: Inspect the preserved s6 removal directory before retrying: $s6_removing_dir"
      exit 1
    fi
    if { [ -e "$s6_removing_dir" ] || [ -L "$s6_removing_dir" ]; } \
      && { [ -e "$s6_service_dir" ] || [ -L "$s6_service_dir" ]; }; then
      echo "Error: Both an s6 definition and an unfinished removal exist. Preserve and inspect both paths: $s6_service_dir $s6_removing_dir"
      exit 1
    fi
    # Do not remove the bundle membership before proving that the source
    # definition still consists only of files deployed by auto-cpufreq.
    if ! s6_service_is_managed "$s6_service_dir"; then
      echo "Error: Refusing to remove an unmanaged s6 service path: $s6_service_dir"
      exit 1
    fi
    if ! s6_bundle_entry_is_managed "$s6_bundle_entry"; then
      echo "Error: Refusing to remove an unmanaged s6 bundle entry: $s6_bundle_entry" >&2
      exit 1
    fi
    if [ -e "$s6_bundle_entry" ] || [ -L "$s6_bundle_entry" ]; then
      echo -e "\n* Disabling auto-cpufreq daemon (s6) at boot"
      s6-service delete default auto-cpufreq 8<&- || exit $?
    fi
    if [ -d "$s6_service_dir" ]; then
      echo -e "\n* Removing auto-cpufreq daemon (s6) unit file"
      # Retire the whole definition before deleting any part of it. The s6
      # compiler ignores dot directories, including partially deleted ones.
      # Rename stays on the same filesystem and preserves unexpected entries.
      mv -T -- "$s6_service_dir" "$s6_removing_dir" || exit $?
    fi

    # Retain the pending directory until the database accepts the removal.
    # A crash or failed reload can then be retried without installing anew.
    echo -e "\n* Update daemon service bundle (s6)"
    s6-db-reload 8<&- || exit $?
    if [ -d "$s6_removing_dir" ]; then
      if ! s6_service_is_managed "$s6_removing_dir"; then
        echo "Error: Inspect the preserved s6 removal directory before retrying: $s6_removing_dir"
        exit 1
      fi
      rm -f -- "$s6_removing_dir/run" "$s6_removing_dir/type" || exit $?
      rmdir -- "$s6_removing_dir" || exit $?
    fi
  ;;
  *)
    echo -e "\n* Unsupported init system detected, could not remove the daemon"
    echo -e "\n* Please open an issue on https://github.com/AdnanHodzic/auto-cpufreq\n"
    exit 1
  ;;
esac
