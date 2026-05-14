#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="interactive_shell vim nano"

ITERATIONS=100
WARMUP_ROUNDS=10
TRIALS=1
TIMEOUT=30
SEED=42

OUTPUT_DIR="w3_5pane_results"
LOG_PEXPECT=false

PROMPT="__W3_PROMPT__# "

SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"

REMOTE_VIM_FILE="/tmp/w3_vim_bench.txt"
REMOTE_NANO_FILE="/tmp/w3_nano_bench.txt"

SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

TMUX_PANES=5
TMUX_SESSION_PREFIX="w3bench5"
TERM_NAME="xterm-256color"
TERM_ROWS=45
TERM_COLS=160

CMD=(
    python3 w3_benchmark_5pane.py
    --host              "$HOST"
    --user              "$USER_NAME"
    --source-ip         "$SOURCE_IP"
    --identity-file     "$IDENTITY_FILE"
    --protocols         $PROTOCOLS
    --workloads         $WORKLOADS
    --iterations        "$ITERATIONS"
    --warmup-rounds     "$WARMUP_ROUNDS"
    --trials            "$TRIALS"
    --timeout           "$TIMEOUT"
    --seed              "$SEED"
    --output-dir        "$OUTPUT_DIR"
    --prompt            "$PROMPT"
    --ssh3-path         "$SSH3_PATH"
    --mosh-predict      "$MOSH_PREDICT"
    --remote-vim-file   "$REMOTE_VIM_FILE"
    --remote-nano-file  "$REMOTE_NANO_FILE"
    --tmux-panes        "$TMUX_PANES"
    --tmux-session-prefix "$TMUX_SESSION_PREFIX"
    --term              "$TERM_NAME"
    --term-rows         "$TERM_ROWS"
    --term-cols         "$TERM_COLS"
)

$SSH3_INSECURE      && CMD+=(--ssh3-insecure)
$BATCH_MODE         && CMD+=(--batch-mode)
$STRICT_HOST_KEY    && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS      && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE  && CMD+=(--reopen-on-failure)
$LOG_PEXPECT        && CMD+=(--log-pexpect)

echo "=== W3 Interactive Benchmark: 5-pane tmux load variant ==="
echo ""
echo "Pane layout:"
echo "  Pane 0 -> measurement target"
echo "  Pane 1 -> visible clock/heartbeat load"
echo "  Pane 2 -> visible stdout burst load"
echo "  Pane 3 -> visible ps/top-like CPU load"
echo "  Pane 4 -> visible ping/update load"
echo ""
echo "Command:"
printf '  %q \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

if [[ -f plot_trend.py ]]; then
    python3 plot_trend.py \
      --output-dir "$OUTPUT_DIR" \
      --prefix "w3_5pane" \
      --group-fields protocol workload
else
    echo "plot_trend.py not found, skip plotting."
fi