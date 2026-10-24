#!/usr/bin/env bash
# Real source installation / service lifecycle in disposable GitHub runner VM.
# WARNING: Installs APT dependencies, real /opt + /usr/local resources,
# and temporarily starts the daemon on the DISPOSABLE VM.
# NEVER run on a personal machine. Only this guarded CI runner.
set -Eeuo pipefail

if [[ ${GITHUB_ACTIONS:-} != true || ${CI:-} != true || ${AUTO_CPUFREQ_DISPOSABLE_VM:-} != YES ]]; then
  echo "REFUSED: only an explicitly authorized disposable GitHub Actions VM" >&2
  exit 90
fi
if [[ $(id -u) == 0 ]]; then
  echo "Run as CI non-root user; privileged calls use sudo" >&2
  exit 91
fi
if [[ $(cat /proc/1/comm) != systemd ]]; then
  echo "REFUSED: systemd must be the runner PID1" >&2
  exit 92
fi
if [[ -e /opt/auto-cpufreq || -L /opt/auto-cpufreq ||
      -e /usr/local/bin/auto-cpufreq ||
      -e /etc/systemd/system/auto-cpufreq.service ]]; then
  echo "REFUSED: runner already has an auto-cpufreq installation" >&2
  exit 93
fi
if [[ -f /etc/auto-cpufreq.conf ]] && grep -qi 'enable_rapl_envelopes[[:space:]]*=[[:space:]]*true' /etc/auto-cpufreq.conf; then
  echo "REFUSED: RAPL active-write opt-in present in runner" >&2
  exit 94
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAPL_DIR=/run/auto-cpufreq-rapl
JOURNAL="$RAPL_DIR/power-state.json"
unit=/etc/systemd/system/auto-cpufreq.service
marker=/usr/local/bin/auto-cpufreq-remove
RESULT_FILE="${RUNNER_TEMP:?}/rapl-vm-e2e-result.txt"
printf 'started\n' > "$RESULT_FILE"

cleanup() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "E2E failed (exit $rc). Collecting ephemeral VM diagnostics..." >&2
    sudo systemctl --no-pager --full status auto-cpufreq.service || true
    sudo journalctl --no-pager -u auto-cpufreq.service -n 70 || true
    sudo ls -la "$RAPL_DIR" /opt/auto-cpufreq /usr/local/bin/auto-cpufreq* || true
  fi
  # A disposable runner is destroyed at job completion. No unsafe shortcut:
  # do not delete the recovery helper/release if restoration is unresolved.
}
trap cleanup EXIT

echo "==== REAL SOURCE INSTALL ===="
cd "$REPO"
sudo -n bash ./auto-cpufreq-installer --install
test -x /usr/local/bin/auto-cpufreq
test -L /opt/auto-cpufreq/current
test -x /opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl
sudo -n /usr/local/bin/auto-cpufreq --version
sudo -n /opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl
echo "source-install: PASS" >> "$RESULT_FILE"

echo "==== REAL CLI --update WITH LOCAL GIT TAG AND REAL PIP BUILD ===="
# The only substituted network dependency is the GitHub "latest release"
# response, replaced by a deterministic local tag. The real Click callback,
# new_update(), git fetch, and *actual* source installer execute.
UPDATE_FIXTURE="$(mktemp -d "$RUNNER_TEMP/auto-cpufreq-vm-update.XXXXXXXX")"
git clone --quiet --local "$REPO" "$UPDATE_FIXTURE/seed"
git -C "$UPDATE_FIXTURE/seed" tag -f v3.2.1 HEAD
git clone --quiet --bare "$UPDATE_FIXTURE/seed" "$UPDATE_FIXTURE/release.git"
initial_release="$(readlink -f /opt/auto-cpufreq/current)"
sudo -n env VM_TAG_REMOTE="$UPDATE_FIXTURE/release.git" \
  /opt/auto-cpufreq/current/venv/bin/python - <<'PY'
import builtins
import os
import sys
from auto_cpufreq import core
from auto_cpufreq.bin import auto_cpufreq as cli
assert os.geteuid() == 0
assert os.path.isdir(os.environ["VM_TAG_REMOTE"])
core.GITHUB = "file://" + os.environ["VM_TAG_REMOTE"].removesuffix(".git")
cli.check_for_update = lambda: "v3.2.1"
builtins.input = lambda prompt="": "y"
sys.argv = ["auto-cpufreq", "--update"]
cli.main.main(args=["--update"], prog_name="auto-cpufreq",
              standalone_mode=False)
PY
test -L /opt/auto-cpufreq/current
updated_release="$(readlink -f /opt/auto-cpufreq/current)"
test "$updated_release" != "$initial_release"
test -f "$initial_release/.complete"
test -f "$updated_release/.complete"
sudo -n /usr/local/bin/auto-cpufreq --version | grep -E 'auto-cpufreq version: 3\\.2\\.1'
echo "real-cli-update-local-tag: PASS" >> "$RESULT_FILE"

echo "==== REAL MANAGED UNIT INSTALL / SYSTEMD ===="
if [[ -r /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors ]] \
   && grep -Eq '(^|[[:space:]])(performance|powersave)([[:space:]]|$)' \
      /sys/devices/system/cpu/cpu0/cpufreq/scaling_available_governors; then
  # This branch runs only on a VM with genuine cpufreq governor support.
  sudo -n /usr/local/bin/auto-cpufreq --install
  sudo systemctl is-active --quiet auto-cpufreq.service
  echo "daemon-real-start: PASS" >> "$RESULT_FILE"
else
  # Ordinary GitHub-hosted VMs do not expose CPU frequency governors.
  # Exercise the REAL Click --install callback and source service-publishing
  # scripts, bypassing ONLY gov_check and the actual daemon start. Never
  # silently interpret this as a successful real governor/daemon test.
  echo "VM has no cpufreq governors: using explicitly labeled unit-only test."
  mkdir -p "$RUNNER_TEMP/rapl-e2e-bin"
  cat > "$RUNNER_TEMP/rapl-e2e-bin/systemctl" <<'EOS'
#!/bin/sh
if [ "$1" = start ] && [ "$2" = auto-cpufreq ]; then
  echo "DISPOSABLE VM: suppressed CPU policy daemon start (no cpufreq)"
  exit 0
fi
exec /usr/bin/systemctl "$@"
EOS
  chmod 755 "$RUNNER_TEMP/rapl-e2e-bin/systemctl"
  sudo -n env PATH="$RUNNER_TEMP/rapl-e2e-bin:$PATH" \
    /opt/auto-cpufreq/current/venv/bin/python - <<'PY'
from auto_cpufreq.bin import auto_cpufreq as cli
import sys
# The legacy CLI checks len(sys.argv) in addition to Click's parsed args.
sys.argv = ["auto-cpufreq", "--install"]
cli.gov_check = lambda: None  # VM-only: no CPU frequency driver
cli.main.main(args=["--install"], prog_name="auto-cpufreq",
              standalone_mode=False)
PY
  echo "daemon-unit-installed-with-start-suppressed: PASS" >> "$RESULT_FILE"
fi
test -f "$unit"
test -f "$marker"
sudo systemctl show auto-cpufreq.service -p ExecStopPost -p RuntimeDirectory -p NeedDaemonReload
test "$(sudo systemctl show -P NeedDaemonReload auto-cpufreq.service)" = no
# Prevent prolonged normal CPU policy execution even on supported CI VMs.
sudo -n systemctl stop auto-cpufreq.service
sudo -n test ! -e "$JOURNAL"
echo "managed-unit-installed-and-stopped: PASS" >> "$RESULT_FILE"

echo "==== STOP-TIME RECOVERY BARRIER AGAINST MALFORMED JOURNAL ===="
sudo -n install -d -m 0700 "$RAPL_DIR"
printf '{malformed-journal\n' | sudo -n tee "$JOURNAL" >/dev/null
sudo -n chmod 0600 "$JOURNAL"
set +e
sudo -n /usr/local/bin/auto-cpufreq --remove > "$RUNNER_TEMP/rapl-blocked-remove.log" 2>&1
blocked=$?
set -e
if [[ $blocked -eq 0 ]]; then
  echo "FAIL: daemon removal succeeded despite malformed RAPL journal" >&2
  cat "$RUNNER_TEMP/rapl-blocked-remove.log"
  exit 95
fi
echo "CLI --remove exited with $blocked; captured diagnostics:"
cat "$RUNNER_TEMP/rapl-blocked-remove.log"
if [[ ! -f "$unit" || ! -f "$marker" || ! -L /opt/auto-cpufreq/current ]] \
   || ! sudo -n test -f "$JOURNAL"; then
  echo "FAIL: incomplete RAPL recovery was followed by destructive removal:" >&2
  sudo -n ls -la "$unit" "$marker" "$JOURNAL" /opt/auto-cpufreq/current || true
  exit 96
fi
echo "refusal-to-remove-unresolved-rapl: PASS" >> "$RESULT_FILE"

echo "==== SUCCESSFUL REMOVAL / CLEANUP ===="
# This record was created by this script, not by the daemon. Delete ONLY
# that synthetic fixture, after the failed-removal assertion above.
test "$(sudo -n cat "$JOURNAL")" = '{malformed-journal'
sudo -n rm -- "$JOURNAL"
sudo -n /opt/auto-cpufreq/current/venv/bin/auto-cpufreq-restore-rapl
sudo -n /usr/local/bin/auto-cpufreq --remove
test ! -f "$marker"
test ! -e "$unit"
test -L /opt/auto-cpufreq/current
sudo -n bash ./auto-cpufreq-installer --remove
test ! -e /opt/auto-cpufreq
echo "successful-remove: PASS" >> "$RESULT_FILE"

echo "==== E2E RESULT ===="
cat "$RESULT_FILE"
echo "FULL DISPOSABLE-VM SOURCE INSTALL / SERVICE / RECOVERY BARRIER: PASS"
