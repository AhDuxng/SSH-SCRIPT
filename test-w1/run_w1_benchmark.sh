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

SCENARIO="${1:?Usage: $0 <scenario>}"

# --- Connection config (USB LAN — controlled environment) --------------------
# HOST="192.168.8.102"
HOST="100.106.17.78"
# HOST="100.66.79.93"
USER_NAME="trungnt"
SOURCE_IP="100.70.166.91"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

# --- Benchmark parameters (matching w1/ methodology) -------------------------
PROTOCOLS="ssh ssh3 mosh"
ITERATIONS=100
WARMUP=3
TRIALS=10
TIMEOUT=20
SEED=42
MOSH_PREDICT="always"

# --- SSH3 config -------------------------------------------------------------
SSH3_PATH="/ssh3-term"
SSH3_INSECURE="--ssh3-insecure"

# --- Output ------------------------------------------------------------------
OUTPUT_DIR="w1_results_trungnt/${SCENARIO}"
mkdir -p "$OUTPUT_DIR"

# --- Commands ----------------------------------------------------------------
COMMANDS=(
  "ls"
  "df -h"
  "grep -n root /etc/passwd"
  "git status"
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
  $SSH3_INSECURE
  --min-recv-pct 0
  --shuffle-pairs
  --reopen-on-failure
  --batch-mode
  --commands "${COMMANDS[@]}"
)

echo "=== W1 Benchmark: scenario=$SCENARIO ==="
echo "Host: $USER_NAME@$HOST (source: $SOURCE_IP)"
echo "Protocols: $PROTOCOLS"
echo "Trials=$TRIALS, Iterations=$ITERATIONS, Warmup=$WARMUP"
echo "Mosh predict: $MOSH_PREDICT"
echo "Output: $OUTPUT_DIR/"
echo

"${CMD[@]}"
