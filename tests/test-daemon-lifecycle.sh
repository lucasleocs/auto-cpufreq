#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
LOG="$TMP/calls"

cat > "$TMP/bin/tput" <<'EOF'
#!/usr/bin/env bash
echo 80
EOF
cat > "$TMP/bin/seq" <<'EOF'
#!/usr/bin/env bash
/usr/bin/seq "$@"
EOF
cat > "$TMP/bin/ps" <<'EOF'
#!/usr/bin/env bash
echo "${TEST_INIT:-systemd}"
EOF
cat > "$TMP/bin/cp" <<'EOF'
#!/usr/bin/env bash
echo "cp $*" >> "$TEST_CALL_LOG"
exit 0
EOF
cat > "$TMP/bin/rm" <<'EOF'
#!/usr/bin/env bash
echo "rm $*" >> "$TEST_CALL_LOG"
exit 0
EOF
cat > "$TMP/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >> "$TEST_CALL_LOG"
if [ "$*" = "${FAIL_ACTION:-}" ]; then
  exit 23
fi
exit 0
EOF
chmod +x "$TMP/bin"/*

run_script() {
  TEST_INIT="$1" FAIL_ACTION="$2" TEST_CALL_LOG="$LOG" \
    PATH="$TMP/bin:/usr/bin:/bin" "$3" >/dev/null 2>&1
}

: > "$LOG"
if run_script systemd "start auto-cpufreq" "$ROOT/scripts/auto-cpufreq-install.sh"; then
  echo "install masked service start failure" >&2
  exit 1
fi
if grep -q '^systemctl enable auto-cpufreq$' "$LOG"; then
  echo "install continued to enable after start failed" >&2
  exit 1
fi

: > "$LOG"
if run_script systemd "stop auto-cpufreq" "$ROOT/scripts/auto-cpufreq-remove.sh"; then
  echo "remove masked service stop failure" >&2
  exit 1
fi
if grep -q '^systemctl disable auto-cpufreq$' "$LOG"; then
  echo "remove continued to disable after stop failed" >&2
  exit 1
fi
if grep -q '^rm /etc/systemd/system/auto-cpufreq.service$' "$LOG"; then
  echo "remove deleted the unit after stop failed" >&2
  exit 1
fi

if run_script unsupported "" "$ROOT/scripts/auto-cpufreq-install.sh"; then
  echo "unsupported init reported successful install" >&2
  exit 1
fi
if run_script unsupported "" "$ROOT/scripts/auto-cpufreq-remove.sh"; then
  echo "unsupported init reported successful removal" >&2
  exit 1
fi
