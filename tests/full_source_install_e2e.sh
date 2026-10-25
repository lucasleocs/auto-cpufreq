#!/usr/bin/env bash
# Lab-only full installer integration. Must run only inside disposable Docker.
set -euo pipefail
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
log() { printf '\n===== %s =====\n' "$*"; }
run_install() {
  local logfile="$1"
  shift
  if ! "$@" > "$logfile" 2>&1; then
    tail -n 100 "$logfile" >&2
    fail "source installer operation failed ($logfile)"
  fi
}

[ -f /.dockerenv ] || fail "Docker containment marker absent"
[ "$(id -u)" = 0 ] || fail "Container UID must be root"
[ "${AUTO_CPUFREQ_E2E_CONTAINER:-}" = enabled ] || fail "Explicit test-container flag missing"
[ -d /src/.git ] || fail "Readonly checkout with Git history required"
[ ! -e /opt/auto-cpufreq ] || fail "Container has pre-existing source install"
[ ! -e /usr/local/bin/auto-cpufreq ] || fail "Container has pre-existing CLI"
export TERM=xterm DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1

log "Install fixture prerequisites in disposable Ubuntu container"
apt-get update -qq
apt-get install -y -qq ca-certificates desktop-file-utils ncurses-bin util-linux
command -v desktop-file-install >/dev/null || fail "desktop-file-install unavailable"

log "Copy readonly checkout into private writable working tree"
mkdir -p /work/source
cp -a /src/. /work/source/
cd /work/source
git --version >/dev/null || true  # Installer installs Git before resolving source metadata.

log "Seed simulated legacy source tree and public integration"
mkdir -p /opt/auto-cpufreq/venv /usr/local/share/auto-cpufreq/scripts
mkdir -p /usr/local/bin /usr/share/pixmaps /usr/share/polkit-1/actions /usr/share/applications
printf 'old venv\n' >/opt/auto-cpufreq/venv/old-marker
printf 'old script\n' >/usr/local/share/auto-cpufreq/scripts/old-marker
printf '#!/bin/sh\nprintf "old cli\\n"\n' >/usr/local/bin/auto-cpufreq
chmod 755 /usr/local/bin/auto-cpufreq
printf 'old icon\n' >/usr/share/pixmaps/auto-cpufreq.png
printf 'old policy\n' >/usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy
printf '#!/bin/sh\nexit 0\n' >/usr/local/bin/auto-cpufreq-gtk
chmod 755 /usr/local/bin/auto-cpufreq-gtk
printf 'old desktop\n' >/usr/share/applications/auto-cpufreq-gtk.desktop

log "Real first source install migrating legacy fixture"
run_install /tmp/first.log ./auto-cpufreq-installer --install
current=/opt/auto-cpufreq/current
releases=/opt/auto-cpufreq/releases
test -L "$current" || fail "Current symlink not created"
first_target=$(readlink "$current")
test -f "$current/.complete" || fail "First generation incomplete"
test -x "$current/venv/bin/auto-cpufreq" || fail "Installed CLI missing"
test ! -e /opt/auto-cpufreq/venv || fail "Legacy venv not retired"
test ! -e /usr/local/share/auto-cpufreq || fail "Legacy share not retired"
test -f /usr/share/applications/auto-cpufreq-gtk.desktop || fail "Desktop file missing"
test -f /usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy || fail "Policy file missing"
test -x /usr/local/bin/auto-cpufreq-gtk || fail "GTK launcher missing"
version_before=$(/usr/local/bin/auto-cpufreq --version)
case "$version_before" in
  *"auto-cpufreq version: "*) ;;
  *) fail "Installed wrapper returned unexpected version: $version_before" ;;
esac
echo "PASS real installation and simulated legacy migration"
printf '%s\n' "$version_before"

log "Capture selected generation and public integration hashes"
(
  cd /
  sha256sum \
    usr/local/bin/auto-cpufreq \
    usr/local/bin/auto-cpufreq-gtk \
    usr/share/pixmaps/auto-cpufreq.png \
    usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy \
    usr/share/applications/auto-cpufreq-gtk.desktop
) >/tmp/public-before.sha256
count_before=$(find "$releases" -mindepth 1 -maxdepth 1 -type d | wc -l)
[ "$count_before" -eq 1 ] || fail "Expected one source generation"

log "Inject real desktop publication failure after candidate activation"
mkdir -p /tmp/fail-bin
cat >/tmp/fail-bin/desktop-file-install <<'SH'
#!/bin/sh
echo "injected desktop-file-install failure" >&2
exit 83
SH
chmod +x /tmp/fail-bin/desktop-file-install
if PATH="/tmp/fail-bin:$PATH" ./auto-cpufreq-installer --install >/tmp/failure.log 2>&1; then
  fail "Installer wrongly reported success despite injected failure"
fi
grep -Fq "Failed to install the auto-cpufreq desktop entry" /tmp/failure.log || {
  tail -n 100 /tmp/failure.log
  fail "Post-activation failure injection did not execute"
}
[ "$(readlink "$current")" = "$first_target" ] || fail "Previous generation was not restored"
test -f "$current/.complete" || fail "Old generation lost"
(
  cd /
  sha256sum -c /tmp/public-before.sha256
) || fail "Public files differ from baseline after rollback"
[ "$(find "$releases" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq "$count_before" ] \
  || fail "Failed release was not discarded"
[ "$(/usr/local/bin/auto-cpufreq --version)" = "$version_before" ] || fail "CLI version changed"
echo "PASS actual post-activation rollback and public files restored"

log "Real successful second installation into a new versioned release"
run_install /tmp/upgrade.log ./auto-cpufreq-installer --install
second_target=$(readlink "$current")
[ "$second_target" != "$first_target" ] || fail "Upgrade failed to activate a new generation"
test -f "$current/.complete" || fail "New release incomplete"
test -f "/opt/auto-cpufreq/$first_target/.complete" || fail "Previous release was not retained"
[ "$(find "$releases" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 2 ] || fail "Expected two retained releases"
[ "$(/usr/local/bin/auto-cpufreq --version)" = "$version_before" ] || fail "Same-commit upgrade version changed"
echo "PASS versioned upgrade"

log "Real source removal in disposable container (no daemon installed)"
run_install /tmp/remove.log ./auto-cpufreq-installer --remove
test ! -e /opt/auto-cpufreq || fail "Source releases remain after removal"
test ! -e /usr/local/share/auto-cpufreq || fail "Legacy share remains"
for path in \
  /usr/local/bin/auto-cpufreq \
  /usr/local/bin/auto-cpufreq-gtk \
  /usr/share/pixmaps/auto-cpufreq.png \
  /usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy \
  /usr/share/applications/auto-cpufreq-gtk.desktop; do
  test ! -e "$path" || fail "Public integration remains after removal: $path"
done
echo "PASS complete source removal"

log "Second removal is a safe no-op"
run_install /tmp/remove-again.log ./auto-cpufreq-installer --remove
test ! -e /opt/auto-cpufreq || fail "Repeated removal recreated installation"
echo "PASS idempotent removal"
log "All disposable full-installer scenarios passed"
