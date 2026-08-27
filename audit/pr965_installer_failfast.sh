#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/source" "$TMP/bin" "$TMP/root"
cp "$ROOT/auto-cpufreq-installer" "$TMP/source/auto-cpufreq-installer"
cat > "$TMP/source/pyproject.toml" <<'EOF'
PyGObject = { version = "3.50.0" }
EOF

sed -i \
  -e "s|APPLICATIONS_PATH=\"/usr/share/applications\"|APPLICATIONS_PATH=\"$TMP/root/usr/share/applications\"|" \
  -e "s|VENV_PATH=\"/opt/auto-cpufreq\"|VENV_PATH=\"$TMP/root/opt/auto-cpufreq\"|" \
  -e "s|SHARE_DIR=\"/usr/local/share/auto-cpufreq/\"|SHARE_DIR=\"$TMP/root/usr/local/share/auto-cpufreq/\"|" \
  -e "s|AUTO_CPUFREQ_FILE=\"/usr/local/bin/auto-cpufreq\"|AUTO_CPUFREQ_FILE=\"$TMP/root/usr/local/bin/auto-cpufreq\"|" \
  -e "s|IMG_FILE=\"/usr/share/pixmaps/auto-cpufreq.png\"|IMG_FILE=\"$TMP/root/usr/share/pixmaps/auto-cpufreq.png\"|" \
  -e "s|ORG_FILE=\"/usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy\"|ORG_FILE=\"$TMP/root/usr/share/polkit-1/actions/org.auto-cpufreq.pkexec.policy\"|" \
  "$TMP/source/auto-cpufreq-installer"

cat > "$TMP/bin/tput" <<'EOF'
#!/usr/bin/env bash
echo 80
EOF
cat > "$TMP/bin/apt" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = update ]; then
  exit 42
fi
exit 0
EOF
cat > "$TMP/bin/apt-cache" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat > "$TMP/bin/python3" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = -m ] && [ "${2:-}" = venv ]; then
  target="${@: -1}"
  /bin/mkdir -p "$target/bin"
  : > "$target/bin/activate"
fi
exit 0
EOF
for command in python git cp chmod desktop-file-install update-desktop-database; do
  cat > "$TMP/bin/$command" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$TMP/bin/$command"
done
chmod +x "$TMP/bin/tput" "$TMP/bin/apt" "$TMP/bin/apt-cache" "$TMP/bin/python3"

if TERM=xterm PATH="$TMP/bin:/usr/bin:/bin" "$TMP/source/auto-cpufreq-installer" --install >/dev/null 2>&1; then
  echo "source installer masked a failed package-manager update" >&2
  exit 1
fi

echo "installer propagated the injected package-manager failure"
