#!/usr/bin/env bash
# setup_w1_fixtures.sh - create tiny static fixtures for W1 on the server.
#
# From the client:
#   ssh user@host 'bash -s' < setup_w1_fixtures.sh
#
# Directly on the server:
#   bash setup_w1_fixtures.sh
set -euo pipefail

FIXTURE_DIR="${FIXTURE_DIR:-/tmp}"
SMALL_FILE="$FIXTURE_DIR/w1_fixture_small.txt"
MEDIUM_FILE="$FIXTURE_DIR/w1_fixture_medium.txt"
LARGE_FILE="$FIXTURE_DIR/w1_fixture_large.txt"

SMALL_BYTES="${SMALL_BYTES:-128}"
MEDIUM_BYTES="${MEDIUM_BYTES:-512}"
LARGE_BYTES="${LARGE_BYTES:-900}"

make_fixture() {
  local bytes="$1"
  local output="$2"
  local label="$3"

  : > "$output"
  local i=1
  while [[ "$(wc -c < "$output")" -lt "$bytes" ]]; do
    printf 'w1:%s:%04d:abcdefghijklmnopqrstuvwxyz:0123456789\n' "$label" "$i" >> "$output"
    i=$((i + 1))
  done
  truncate -s "$bytes" "$output"
}

echo "=== W1 tiny static fixture setup ==="
echo "Directory: $FIXTURE_DIR"
echo

mkdir -p "$FIXTURE_DIR"
make_fixture "$SMALL_BYTES" "$SMALL_FILE" "small"
make_fixture "$MEDIUM_BYTES" "$MEDIUM_FILE" "medium"
make_fixture "$LARGE_BYTES" "$LARGE_FILE" "large"

echo "=== Verification ==="
wc -c "$SMALL_FILE" "$MEDIUM_FILE" "$LARGE_FILE"
sha256sum "$SMALL_FILE" "$MEDIUM_FILE" "$LARGE_FILE"

echo
echo "=== Warm page cache ==="
cat "$SMALL_FILE" "$MEDIUM_FILE" "$LARGE_FILE" >/dev/null

echo
echo "=== Commands for W1 ==="
printf 'cat %s\n' "$SMALL_FILE" "$MEDIUM_FILE" "$LARGE_FILE"

echo
echo "Done. Tiny static W1 fixtures are ready."
