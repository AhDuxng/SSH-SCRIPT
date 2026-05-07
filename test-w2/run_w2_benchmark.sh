#!/usr/bin/env bash
set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="top tail ping"

ITERATIONS=100
TRIALS=15
TIMEOUT=30
SEED=42

OUTPUT_DIR="w2_results"
LOG_PEXPECT=true
PROMPT="__W2_PROMPT__# "

SSH3_PATH=":443/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"
TOP_INTERVAL=1.0

CMD=(
  python w2_continuous_monitoring_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --workloads $WORKLOADS
  --iterations "$ITERATIONS"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --seed "$SEED"
  --output-dir "$OUTPUT_DIR"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
  --top-interval "$TOP_INTERVAL"
)

$SSH3_INSECURE     && CMD+=(--ssh3-insecure)
$BATCH_MODE        && CMD+=(--batch-mode)
$STRICT_HOST_KEY   && CMD+=(--strict-host-key-checking)
$LOG_PEXPECT       && CMD+=(--log-pexpect)

CMD+=(--reopen-on-failure)

echo "=== W2 Continuous Monitoring Benchmark ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

exec "${CMD[@]}"