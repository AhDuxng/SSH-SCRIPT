#!/usr/bin/env bash
# run_w3_loop.sh — Repeat W3 benchmark N times with a gap between runs.
#
# Purpose: capture keystroke latency across different time-of-day conditions
# on a VPN link (peak vs off-peak hours). Each run writes to its own
# timestamped folder so nothing is overwritten.
#
# Usage:
#   ./run_w3_loop.sh                          # defaults: scenario=default, 15 runs, 30 min gap
#   ./run_w3_loop.sh <scenario>               # e.g. ./run_w3_loop.sh default
#   ./run_w3_loop.sh <scenario> <runs>        # e.g. ./run_w3_loop.sh default 20
#   ./run_w3_loop.sh <scenario> <runs> <gap_min>
#
# The gap is measured from the moment one run FINISHES to the moment the next
# STARTS, matching the requested "30 phút 1 lần kể từ lúc chạy xong" semantic.
# The final iteration does not sleep afterwards.
#
# Each run's folder: w3_results/<scenario>/<YYYYmmdd_HHMMSS>/
#   + benchmark outputs from w3_interactive_benchmark.py
#   + baseline.txt (ping/versions captured at run start)
#   + run_console.log (stdout+stderr of the wrapper)
# Loop-level log: w3_results/<scenario>/_loop_<LOOP_TAG>.log
#
# Safe to Ctrl-C at any time. If interrupted between runs, re-invoke with a
# smaller --runs count; completed folders are NOT touched.

set -euo pipefail

SCENARIO="${1:-default}"
TOTAL_RUNS="${2:-15}"
GAP_MINUTES="${3:-30}"

if ! [[ "$TOTAL_RUNS" =~ ^[0-9]+$ ]] || [[ "$TOTAL_RUNS" -lt 1 ]]; then
  echo "ERROR: total runs must be a positive integer (got '$TOTAL_RUNS')" >&2
  exit 2
fi
if ! [[ "$GAP_MINUTES" =~ ^[0-9]+$ ]]; then
  echo "ERROR: gap_min must be a non-negative integer (got '$GAP_MINUTES')" >&2
  exit 2
fi

GAP_SEC=$((GAP_MINUTES * 60))

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOOP_TAG="$(date +%Y%m%d_%H%M%S)"
RESULTS_ROOT="w3_results/${SCENARIO}"
LOOP_LOG="${RESULTS_ROOT}/_loop_${LOOP_TAG}.log"
mkdir -p "$RESULTS_ROOT"

log() {
  printf '[%(%Y-%m-%d %H:%M:%S)T] %s\n' -1 "$*" | tee -a "$LOOP_LOG"
}

sleep_with_progress() {
  local seconds="$1"
  local msg="$2"
  local next_at
  next_at=$(date -d "@$(( $(date +%s) + seconds ))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null \
            || date -r "$(( $(date +%s) + seconds ))" '+%Y-%m-%d %H:%M:%S')
  log "$msg — sleeping ${seconds}s (until $next_at)"
  local i=0
  local step=60
  while [[ $i -lt $seconds ]]; do
    local chunk=$step
    [[ $((seconds - i)) -lt $step ]] && chunk=$((seconds - i))
    sleep "$chunk"
    i=$((i + chunk))
    local remaining=$((seconds - i))
    if [[ $remaining -gt 0 ]]; then
      log "  ...waited $((i / 60)) min, ${remaining}s remaining"
    fi
  done
}

cleanup() {
  local rc=$?
  log "=== loop ended (exit=$rc) ==="
}
trap cleanup EXIT

log "=== W3 loop ==="
log "scenario=$SCENARIO  runs=$TOTAL_RUNS  gap=${GAP_MINUTES} min"
log "loop log: $LOOP_LOG"
log "results : $RESULTS_ROOT/<YYYYmmdd_HHMMSS>/"
echo | tee -a "$LOOP_LOG"

for (( run = 1; run <= TOTAL_RUNS; run++ )); do
  RUN_TAG="$(date +%Y%m%d_%H%M%S)"
  RUN_DIR="${RESULTS_ROOT}/${RUN_TAG}"
  RUN_LOG="${RUN_DIR}/run_console.log"

  log "---------- run ${run}/${TOTAL_RUNS} | tag=${RUN_TAG} ----------"
  mkdir -p "$RUN_DIR"

  # Run the benchmark; capture console into the run's own folder. We DO NOT
  # let a single failure kill the loop — VPN can drop, sessions can timeout,
  # but the remaining runs are still valuable samples.
  set +e
  ./run_w3_benchmark.sh "$SCENARIO" "$RUN_TAG" > >(tee "$RUN_LOG") 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    log "run ${run}/${TOTAL_RUNS} OK (rc=0)"
  else
    log "run ${run}/${TOTAL_RUNS} FAILED (rc=$rc) — continuing; see $RUN_LOG"
  fi

  if [[ $run -lt $TOTAL_RUNS ]]; then
    sleep_with_progress "$GAP_SEC" "gap before run $((run + 1))"
  fi
  echo | tee -a "$LOOP_LOG"
done

log "=== all $TOTAL_RUNS runs done ==="
log "results under: $RESULTS_ROOT/"
