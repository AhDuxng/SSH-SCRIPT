#!/usr/bin/env bash
# setup_w4_fixtures.sh — Run ONCE on the benchmark server (Pi) before W4 benchmarks.
#
# Creates three fixed-size text files containing real filesystem paths from `find /`.
# These files are used as workloads by run_w4_benchmark.sh instead of base64-of-zeros,
# giving realistic compressibility (~4:1 gzip) while keeping server_exec_time < 5ms
# (cat from page cache after warmup).
#
# Usage (from client):
#   ssh pi@<HOST> 'bash -s' < setup_w4_fixtures.sh
#
# Or directly on the server:
#   bash setup_w4_fixtures.sh
set -euo pipefail

SMALL_FILE="/tmp/w4_paths_small.txt"
MEDIUM_FILE="/tmp/w4_paths_medium.txt"
LARGE_FILE="/tmp/w4_paths_large.txt"

SMALL_BYTES=524288    # 512 KiB
MEDIUM_BYTES=2621440  # 2.5 MiB
LARGE_BYTES=10485760  # 10 MiB

echo "=== W4 fixture setup ==="
echo "Generating filesystem path lists from 'find /' ..."
echo "(This may take 10-30s on first run due to SD card I/O)"
echo

find / 2>/dev/null | head -c "$SMALL_BYTES"  > "$SMALL_FILE"
echo "Created $SMALL_FILE ($(wc -c < "$SMALL_FILE") bytes)"

find / 2>/dev/null | head -c "$MEDIUM_BYTES" > "$MEDIUM_FILE"
echo "Created $MEDIUM_FILE ($(wc -c < "$MEDIUM_FILE") bytes)"

find / 2>/dev/null | head -c "$LARGE_BYTES"  > "$LARGE_FILE"
echo "Created $LARGE_FILE ($(wc -c < "$LARGE_FILE") bytes)"

echo
echo "=== Verification ==="
wc -c "$SMALL_FILE" "$MEDIUM_FILE" "$LARGE_FILE"

echo
echo "=== Sample content (first 3 lines of each) ==="
echo "--- small ---"
head -3 "$SMALL_FILE"
echo "--- medium ---"
head -3 "$MEDIUM_FILE"
echo "--- large ---"
head -3 "$LARGE_FILE"

echo
echo "Done. Files are ready for W4 benchmarks."
echo "Re-run this script if the server filesystem changes significantly."
