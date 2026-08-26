#!/usr/bin/env bash
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SYS="$TMP/sys"
SCRIPT="$TMP/cpufreqctl.sh"
mkdir -p "$SYS"
cp "$ROOT/scripts/cpufreqctl.sh" "$SCRIPT"
sed -i "s|FLROOT=/sys/devices/system/cpu|FLROOT=$SYS|" "$SCRIPT"
# The legacy implementation derives a count from /proc/cpuinfo. Force the
# equivalent three-online-CPU count so it would iterate 0,1,2 and miss CPU3.
sed -i 's|^cpucount=.*|cpucount=3|' "$SCRIPT"
chmod +x "$SCRIPT"

echo '0,2-3' > "$SYS/online"
for cpu in 0 2 3; do
  mkdir -p "$SYS/cpu$cpu/cpufreq"
  echo schedutil > "$SYS/cpu$cpu/cpufreq/scaling_governor"
  echo 3000000 > "$SYS/cpu$cpu/cpufreq/scaling_max_freq"
done

"$SCRIPT" --governor --set=powersave
for cpu in 0 2 3; do
  value="$(cat "$SYS/cpu$cpu/cpufreq/scaling_governor")"
  [ "$value" = powersave ] || {
    echo "governor was not applied to online CPU$cpu" >&2
    exit 1
  }
done

"$SCRIPT" --frequency-max --set=1800000
for cpu in 0 2 3; do
  value="$(cat "$SYS/cpu$cpu/cpufreq/scaling_max_freq")"
  [ "$value" = 1800000 ] || {
    echo "maximum frequency was not applied to online CPU$cpu" >&2
    exit 1
  }
done
