#!/usr/bin/env bash
set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa" 

PROTOCOLS="ssh ssh3 mosh"                          
WORKLOADS="interactive_shell vim nano"   

ITERATIONS=100         
WARMUP_ROUNDS=10       
TRIALS=3           
TIMEOUT=20           
SEED=42                

OUTPUT_DIR="w3_results"
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

CMD=(
    python w3_interactive_benchmark.py
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
    --ssh3-path       "$SSH3_PATH"
    --mosh-predict    "$MOSH_PREDICT"
    --remote-vim-file  "$REMOTE_VIM_FILE"
    --remote-nano-file "$REMOTE_NANO_FILE"
)

$SSH3_INSECURE       && CMD+=(--ssh3-insecure)
$BATCH_MODE          && CMD+=(--batch-mode)
$STRICT_HOST_KEY     && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS       && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE   && CMD+=(--reopen-on-failure)
$LOG_PEXPECT         && CMD+=(--log-pexpect)

echo "=== W3 Interactive Benchmark ==="
echo "Lệnh thực thi:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

python plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w3" \
  --group-fields protocol workload
