#!/usr/bin/env bash

set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="mosh"                         # ssh | ssh3 | mosh
WORKLOADS="interactive_shell vim nano"       # interactive_shell | vim | nano

ITERATIONS=100
WARMUP_ROUNDS=10
TRIALS=1      
TIMEOUT=20
SEED=42

OUTPUT_DIR="w3_5pane_results"
LOG_PEXPECT=true

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
TMUX_SESSION="w3bench5"
TMUX_SETUP_SCRIPT="w3_tmux_setup.sh"    # file local (cùng thư mục)
REMOTE_TMUX_SETUP="/tmp/w3_tmux_setup.sh"

CMD=(
    python w3_5pane_benchmark.py
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
    --tmux-session      "$TMUX_SESSION"
    --tmux-setup-script "$TMUX_SETUP_SCRIPT"
    --remote-tmux-setup "$REMOTE_TMUX_SETUP"
)

$SSH3_INSECURE      && CMD+=(--ssh3-insecure)
$BATCH_MODE         && CMD+=(--batch-mode)
$STRICT_HOST_KEY    && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS      && CMD+=(--shuffle-pairs)
$LOG_PEXPECT        && CMD+=(--log-pexpect)

echo "=== W3 Interactive Benchmark — 5-pane tmux load variant ==="
echo ""
echo "Cấu hình load nền:"
echo "  Pane 0 → measurement target (interactive shell)"
echo "  Pane 1 → heartbeat       ~5 lines/s"
echo "  Pane 2 → burst stdout    ~750 lines/s"
echo "  Pane 3 → ls /etc loop    every 0.4 s"
echo "  Pane 4 → log-writer      ~20 events/s  +  tail -f"
echo ""
echo "Lệnh thực thi:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

exec "${CMD[@]}"