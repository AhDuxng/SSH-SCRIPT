#!/usr/bin/env bash
set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="large_output"

COMMANDS=(
  "find /"
  "git status"
  "docker logs \$(docker ps -q | head -n 1)"
)

ITERATIONS=100
TRIALS=5
TIMEOUT=300
COMMAND_IDLE_TIMEOUT=0
MAXREAD=65535
SEARCH_WINDOW_SIZE=8192
SEED=42

OUTPUT_DIR="w4_results"
LOG_PEXPECT=false
PROMPT="W4PROMPT# "

SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"

SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

CMD=(
  python w4_large_output_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --workloads $WORKLOADS
  --commands "${COMMANDS[@]}"
  --iterations "$ITERATIONS"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --command-idle-timeout "$COMMAND_IDLE_TIMEOUT"
  --maxread "$MAXREAD"
  --search-window-size "$SEARCH_WINDOW_SIZE"
  --seed "$SEED"
  --output-dir "$OUTPUT_DIR"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
)

$SSH3_INSECURE     && CMD+=(--ssh3-insecure)
$BATCH_MODE        && CMD+=(--batch-mode)
$STRICT_HOST_KEY   && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS     && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE && CMD+=(--reopen-on-failure)
$LOG_PEXPECT       && CMD+=(--log-pexpect)

echo "=== W4 Large Output Benchmark ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

exec "${CMD[@]}"
