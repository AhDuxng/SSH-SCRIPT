#!/usr/bin/env bash
set -u

mode="${1:-}"
role="${W3_ROLE:-$mode}"
rate_bps="${2:-0}"
trial_id="${3:-unknown}"
ready_file="${4:-}"
target="${W3_TARGET:-unknown}"
protocol="${W3_PROTOCOL:-unknown}"
profile="${W3_PROFILE:-unknown}"

# Bao cho client biet workload da bat dau va tao ready-file cho Mosh.
mark_ready() {
  if [[ -n "$ready_file" ]]; then
    : > "$ready_file"
  fi
  printf 'W3_CHANNEL_READY role=%s trial=%s\n' "$role" "$trial_id"
}

# Gan metadata thi nghiem vao output cua cong cu he thong.
prefix_raw() {
  local kind="$1"
  while IFS= read -r line; do
    printf 'W3_%s trial=%s target=%s protocol=%s profile=%s raw=%s\n' \
      "$kind" "$trial_id" "$target" "$protocol" "$profile" "$line"
  done
}

case "$mode" in
  log)
    mark_ready
    i=0
    while true; do
      printf 'W3_LOG trial=%s target=%s protocol=%s profile=%s line=%06d\n' \
        "$trial_id" "$target" "$protocol" "$profile" "$i"
      i=$((i + 1))
      sleep 0.10
    done
    ;;

  ping)
    mark_ready
    ping -i 0.2 127.0.0.1 | prefix_raw PING
    ;;

  sysmon)
    mark_ready
    if command -v vmstat >/dev/null 2>&1; then
      vmstat 1 | prefix_raw SYSMON
    else
      top -b -d 1 | prefix_raw SYSMON
    fi
    ;;

  output)
    if ! [[ "$rate_bps" =~ ^[0-9]+$ ]] || [[ "$rate_bps" -le 0 ]]; then
      echo "output rate must be a positive integer (bytes/s)" >&2
      exit 2
    fi
    mark_ready
    exec python3 - "$rate_bps" "$trial_id" <<'PY'
import os
import sys
import time

rate = int(sys.argv[1])
chunk_size = min(4096, rate)
payload = (b"X" * max(1, chunk_size - 1)) + b"\n"
written = 0
started = time.monotonic()
out = sys.stdout.buffer

while True:
    out.write(payload)
    out.flush()
    written += len(payload)
    deadline = started + (written / rate)
    delay = deadline - time.monotonic()
    if delay > 0:
        time.sleep(delay)
PY
    ;;

  *)
    echo "Usage: $0 {log|ping|sysmon|output RATE_BPS} [trial_id] [ready_file]" >&2
    exit 2
    ;;
esac
