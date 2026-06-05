#!/usr/bin/env bash
# setup_w4_fixtures.sh - run on the benchmark server before W4.
#
# It creates a static 2 MiB ASCII text fixture in /tmp. W4 then runs `cat` on
# this file through ssh/ssh3/mosh, so received_pct is compared against fixed byte
# counts instead of moving outputs like `ps aux` or live `docker logs`.
#
# From the client:
#   ssh user@host 'bash -s' < setup_w4_fixtures.sh
#
# Directly on the server:
#   bash setup_w4_fixtures.sh
set -euo pipefail

FIXTURE_DIR="${FIXTURE_DIR:-/tmp}"
SOURCE_FILE="$FIXTURE_DIR/w4_paths_source.txt"
FIXTURE_FILE="$FIXTURE_DIR/w4_paths_2mb.txt"

FIXTURE_BYTES="${FIXTURE_BYTES:-2097152}" # 2 MiB

generate_source() {
  local target_bytes="$1"

  echo "Generating ASCII path corpus from find / ..."
  set +o pipefail
  LC_ALL=C find / 2>/dev/null | head -c "$target_bytes" | tr -cd '\11\12\15\40-\176' > "$SOURCE_FILE"
  set -o pipefail

  if [[ ! -s "$SOURCE_FILE" ]]; then
    echo "WARN: find produced no usable paths; generating deterministic fallback text." >&2
    : > "$SOURCE_FILE"
    local i=1
    while [[ "$(wc -c < "$SOURCE_FILE")" -lt "$target_bytes" ]]; do
      printf '/tmp/w4/fallback/path/%08d.txt\n' "$i" >> "$SOURCE_FILE"
      i=$((i + 1))
    done
    truncate -s "$target_bytes" "$SOURCE_FILE"
  fi
}

copy_exact_bytes() {
  local bytes="$1"
  local output="$2"
  local current=0

  : > "$output"
  while [[ "$current" -lt "$bytes" ]]; do
    local before="$current"
    local remaining=$((bytes - current))
    head -c "$remaining" "$SOURCE_FILE" >> "$output"
    current="$(wc -c < "$output")"
    if [[ "$current" -le "$before" ]]; then
      echo "ERROR: could not grow $output from $SOURCE_FILE" >&2
      exit 1
    fi
  done
  truncate -s "$bytes" "$output"
}

echo "=== W4 static fixture setup ==="
echo "Directory: $FIXTURE_DIR"
echo "Fixture bytes: $FIXTURE_BYTES"
echo

mkdir -p "$FIXTURE_DIR"
generate_source "$FIXTURE_BYTES"

copy_exact_bytes "$FIXTURE_BYTES" "$FIXTURE_FILE"

echo
echo "=== Verification ==="
wc -c "$FIXTURE_FILE"
sha256sum "$FIXTURE_FILE"

echo
echo "=== Warm page cache ==="
cat "$FIXTURE_FILE" >/dev/null

echo
echo "=== Commands for W4 ==="
printf 'cat %s\n' "$FIXTURE_FILE"

echo
echo "Done. Static 2 MiB W4 fixture is ready."
