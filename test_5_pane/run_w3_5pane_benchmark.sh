#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="tmux_pane0"

ITERATIONS=100
WARMUP_ROUNDS=10
TRIALS=3
TIMEOUT=20
SEED=42

OUTPUT_DIR="w3_results"
PROMPT="__W3_PROMPT__# "

TMUX_SETUP="w3_tmux_setup.sh"
TMUX_SESSION="w3bench5"
TMUX_READY_MARKER="__W3_5PANE_PANE0_READY__"
TMUX_READY_TIMEOUT=60
TMUX_PANE="0.0"
TMUX_READY_POLL_INTERVAL=0.5

SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="always"

SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

CMD=(
  "$PYTHON_BIN" w3_5pane_benchmark.py
    --host            "$HOST"
    --user            "$USER_NAME"
    --source-ip       "$SOURCE_IP"
    --identity-file   "$IDENTITY_FILE"
    --protocols       $PROTOCOLS
    --workloads       $WORKLOADS
    --iterations      "$ITERATIONS"
    --warmup-rounds   "$WARMUP_ROUNDS"
    --trials          "$TRIALS"
    --timeout         "$TIMEOUT"
    --seed            "$SEED"
    --output-dir      "$OUTPUT_DIR"
    --prompt          "$PROMPT"
    --tmux-setup-script "$TMUX_SETUP"
    --tmux-session    "$TMUX_SESSION"
    --tmux-pane       "$TMUX_PANE"
    --tmux-ready-marker "$TMUX_READY_MARKER"
    --tmux-ready-timeout "$TMUX_READY_TIMEOUT"
    --tmux-ready-poll-interval "$TMUX_READY_POLL_INTERVAL"
    --ssh3-path       "$SSH3_PATH"
    --mosh-predict    "$MOSH_PREDICT"
)

$SSH3_INSECURE       && CMD+=(--ssh3-insecure)
$BATCH_MODE          && CMD+=(--batch-mode)
$STRICT_HOST_KEY     && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS       && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE   && CMD+=(--reopen-on-failure)

echo "=== W3 Interactive Benchmark ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

"$PYTHON_BIN" plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w3" \
  --group-fields protocol workload
