#!/usr/bin/env bash
# run_w1_benchmark.sh — Run W1 command-loop benchmark for a single scenario.
#
# Usage:
#   ./run_w1_benchmark.sh <scenario>
#   ./run_w1_benchmark.sh low
#
# The scenario label is written into CSV/JSON for later aggregation.
# Network impairment must be applied externally (e.g., via run_all_scenarios.sh).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SCENARIO="${1:-${SCENARIO:-default}}"
USER_NAME="${USER_NAME:-trungnt}"

DEFAULT_HOST="${DEFAULT_HOST:-100.106.17.78}"
DEFAULT_SOURCE_IP="${DEFAULT_SOURCE_IP:-100.70.166.91}"
DEFAULT_IDENTITY_FILE="${DEFAULT_IDENTITY_FILE:-$HOME/.ssh/id_ed25519}"

LAN_HOST="${LAN_HOST:-192.168.8.102}"
LAN_SOURCE_IP="${LAN_SOURCE_IP:-192.168.8.100}"
LAN_IDENTITY_FILE="${LAN_IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

case "$SCENARIO" in
  default)
    HOST="${HOST:-$DEFAULT_HOST}"
    SOURCE_IP="${SOURCE_IP:-$DEFAULT_SOURCE_IP}"
    IDENTITY_FILE="${IDENTITY_FILE:-$DEFAULT_IDENTITY_FILE}"
    ;;
  low|medium|high)
    HOST="${HOST:-$LAN_HOST}"
    SOURCE_IP="${SOURCE_IP:-$LAN_SOURCE_IP}"
    IDENTITY_FILE="${IDENTITY_FILE:-$LAN_IDENTITY_FILE}"
    ;;
  *)
    echo "ERROR: unknown scenario '$SCENARIO' (allowed: default, low, medium, high)" >&2
    exit 2
    ;;
esac

# --- Benchmark parameters (matching w1/ methodology) -------------------------
PROTOCOLS="${PROTOCOLS:-ssh ssh3 mosh}"
ITERATIONS="${ITERATIONS:-15}"
WARMUP="${WARMUP:-3}"
TRIALS="${TRIALS:-10}"
TIMEOUT="${TIMEOUT:-20}"
SEED="${SEED:-42}"
MOSH_PREDICT="${MOSH_PREDICT:-always}"
FIXTURE_DIR="${FIXTURE_DIR:-/tmp}"

# --- SSH3 config -------------------------------------------------------------
SSH3_PATH="${SSH3_PATH:-/ssh3-term}"
SSH3_INSECURE="${SSH3_INSECURE:-true}"

# --- Output ------------------------------------------------------------------
OUTPUT_ROOT="${OUTPUT_ROOT:-w1_results_trungnt}"
OUTPUT_DIR="${OUTPUT_DIR:-$OUTPUT_ROOT/${SCENARIO}}"
mkdir -p "$OUTPUT_DIR"

# --- Commands ----------------------------------------------------------------
COMMANDS=(
  "cat $FIXTURE_DIR/w1_fixture_small.txt"
  "cat $FIXTURE_DIR/w1_fixture_medium.txt"
  "cat $FIXTURE_DIR/w1_fixture_large.txt"
)

# --- Build command -----------------------------------------------------------
CMD=(
  python3 w1_command_loop_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --iterations "$ITERATIONS"
  --warmup "$WARMUP"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --seed "$SEED"
  --scenario "$SCENARIO"
  --output-dir "$OUTPUT_DIR"
  --mosh-predict "$MOSH_PREDICT"
  --ssh3-path "$SSH3_PATH"
  --min-recv-pct 0
  --shuffle-pairs
  --reopen-on-failure
  --batch-mode
  --commands "${COMMANDS[@]}"
)

[[ "$SSH3_INSECURE" == "true" ]] && CMD+=(--ssh3-insecure)

echo "=== W1 Benchmark: scenario=$SCENARIO ==="
echo "Host: $USER_NAME@$HOST (source: $SOURCE_IP)"
echo "Protocols: $PROTOCOLS"
echo "Fixtures: $FIXTURE_DIR/w1_fixture_{small,medium,large}.txt"
echo "Trials=$TRIALS, Iterations=$ITERATIONS, Warmup=$WARMUP"
echo "Mosh predict: $MOSH_PREDICT"
echo "Output: $OUTPUT_DIR/"
echo
echo "Commands:"
for command in "${COMMANDS[@]}"; do
  printf '  - %s\n' "$command"
done
echo

"${CMD[@]}"
