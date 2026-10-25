#!/usr/bin/env bash
# Ubuntu cloud image test only: systemd PID 1, actual service manager and
# production install/remove service scripts. CPU daemon process is replaced by
# a runtime-only /bin/sleep drop-in because virtual CPUs lack host cpufreq.
set -euo pipefail
export TERM=xterm DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
log() { echo "VM-CHECK: $*"; }
fail() { echo "VM-FAIL: $*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail "Guest root required"
[ "$(ps -p 1 -o comm= | tr -d ' ')" = systemd ] || fail "Expected true systemd PID 1"
[ -e /dev/disk/by-label/AUTOSRC ] || fail "Source ISO not mounted"
mkdir -p /root/work
cp -a /mnt/src/source/. /root/work/
# Files on a read-only ISO inherit the CI runner's UID, not root's. Repair
# ownership of this private disposable copy without changing the checkout.
chown -R 0:0 /root/work
cd /root/work
git rev-parse --verify HEAD >/dev/null || fail "Checkout Git metadata missing"
log "Real source installation in disposable Ubuntu guest"
./auto-cpufreq-installer --install > /var/log/autocpufreq-vm-install.log 2>&1 || {
  tail -n 100 /var/log/autocpufreq-vm-install.log
  fail "Source installer failed"
}
test -L /opt/auto-cpufreq/current || fail "Active release link absent"
test -f /opt/auto-cpufreq/current/share/scripts/auto-cpufreq.service || fail "Service template missing"
systemd-analyze verify /opt/auto-cpufreq/current/share/scripts/auto-cpufreq.service \
  || fail "systemd-analyze rejected real service template"
log "Source installation and systemd unit verified"

# The daemon writes host CPU state and cannot be meaningfully tested on a
# QEMU virtual CPU lacking cpufreq. The drop-in changes ONLY ExecStart,
# preserves the shipping unit, and is removed with the disposable VM.
mkdir -p /run/systemd/system/auto-cpufreq.service.d
cat > /run/systemd/system/auto-cpufreq.service.d/vm-test.conf <<'OVERRIDE'
[Service]
ExecStart=
ExecStart=/usr/bin/sleep infinity
Restart=no
OVERRIDE

log "Real systemd service installer: publish, enable and start"
bash /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-install.sh >/var/log/autocpufreq-vm-daemon-install.log 2>&1 || {
  tail -n 100 /var/log/autocpufreq-vm-daemon-install.log
  fail "Daemon install script failed"
}
unit=/etc/systemd/system/auto-cpufreq.service
test -f "$unit" || fail "Installed service unit absent"
cmp -s "$unit" /opt/auto-cpufreq/current/share/scripts/auto-cpufreq.service || fail "Service unit changed"
systemctl is-enabled --quiet auto-cpufreq || fail "Service not enabled"
systemctl is-active --quiet auto-cpufreq || fail "Service not active"
systemctl show auto-cpufreq.service -p ExecMainPID -p ActiveState -p FragmentPath
log "PASS real systemd enable and start (daemon entrypoint safely stubbed)"

log "Repeated service install should be idempotent"
bash /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-install.sh >/var/log/autocpufreq-vm-reinstall.log 2>&1 || {
  tail -n 100 /var/log/autocpufreq-vm-reinstall.log
  fail "Repeated daemon installation failed"
}
systemctl is-active --quiet auto-cpufreq || fail "Repeated install lost service"
log "PASS repeated enable and start"

log "Reject admin-replaced unit without clobbering it"
printf '\n# foreign administrator customization\n' >> "$unit"
if bash /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-install.sh --preflight >/var/log/autocpufreq-vm-foreign-preflight.log 2>&1; then
  fail "Preflight accepted modified service unit"
fi
grep -Fq "Refusing an unmanaged systemd service file" /var/log/autocpufreq-vm-foreign-preflight.log \
  || fail "Unmanaged unit failed for an unexpected reason"
if bash /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-remove.sh >/var/log/autocpufreq-vm-foreign-remove.log 2>&1; then
  fail "Removal accepted modified service unit"
fi
test -f "$unit" || fail "Foreign unit was deleted"
grep -Fq "foreign administrator customization" "$unit" || fail "Foreign customization lost"
log "PASS replacement refusal and owner-file preservation"

log "Restore managed unit for authorized cleanup"
cp /opt/auto-cpufreq/current/share/scripts/auto-cpufreq.service "$unit"
bash /opt/auto-cpufreq/current/share/scripts/auto-cpufreq-remove.sh >/var/log/autocpufreq-vm-daemon-remove.log 2>&1 || {
  tail -n 100 /var/log/autocpufreq-vm-daemon-remove.log
  fail "Daemon service removal failed"
}
test ! -e "$unit" || fail "Managed service unit remains"
if systemctl is-enabled --quiet auto-cpufreq; then fail "Service still enabled"; fi
if systemctl is-active --quiet auto-cpufreq; then fail "Service still active"; fi
log "PASS real systemd stop, disable and removal"

log "Complete source removal after daemon removal"
./auto-cpufreq-installer --remove >/var/log/autocpufreq-vm-source-remove.log 2>&1 || {
  tail -n 100 /var/log/autocpufreq-vm-source-remove.log
  fail "Source removal after daemon cleanup failed"
}
test ! -e /opt/auto-cpufreq || fail "Versioned source tree remains"
log "PASS source cleanup"
echo "AUTOCFREQ_GUEST_ALL_PASS"
