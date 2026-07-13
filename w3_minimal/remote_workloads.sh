#!/usr/bin/env bash
set -u

mode="${1:-}"
level="${2:-normal}"
target="${W3_TARGET:-unknown}"
protocol="${W3_PROTOCOL:-unknown}"
profile="${W3_PROFILE:-unknown}"

prefix_raw() {
  local kind="$1"
  while IFS= read -r line; do
    printf 'W3_%s target=%s protocol=%s profile=%s raw=%s\n' "$kind" "$target" "$protocol" "$profile" "$line"
  done
}

case "$mode" in
  log)
    i=0
    while true; do
      printf 'W3_LOG target=%s protocol=%s profile=%s line=%06d ts=%s pid=%s\n' "$target" "$protocol" "$profile" "$i" "$(date +%s%N)" "$$"
      i=$((i + 1))
      sleep 0.10
    done
    ;;

  ping)
    # Network monitoring channel. Use gateway/loopback as a stable default.
    ping -i 0.2 127.0.0.1 | prefix_raw PING
    ;;

  sysmon)
    # System monitoring channel. Prefer vmstat; fallback to top batch mode.
    if command -v vmstat >/dev/null 2>&1; then
      vmstat 1 | prefix_raw SYSMON
    else
      top -b -d 1 | prefix_raw SYSMON
    fi
    ;;

  output)
    i=0
    if [[ "$level" == "heavy" ]]; then
      # Large continuous output load.
      while true; do
        printf 'W3_HEAVY_OUTPUT target=%s protocol=%s profile=%s line=%06d ts=%s ' "$target" "$protocol" "$profile" "$i" "$(date +%s%N)"
        head -c 4096 /dev/zero | tr '\0' 'X'
        printf '\n'
        i=$((i + 1))
      done
    else
      # Moderate background output.
      while true; do
        printf 'W3_BG_OUTPUT target=%s protocol=%s profile=%s line=%06d ts=%s payload=abcdefghijklmnopqrstuvwxyz0123456789\n' "$target" "$protocol" "$profile" "$i" "$(date +%s%N)"
        i=$((i + 1))
        sleep 0.05
      done
    fi
    ;;

  *)
    echo "Usage: $0 {log|ping|sysmon|output [normal|heavy]}" >&2
    exit 2
    ;;
esac
