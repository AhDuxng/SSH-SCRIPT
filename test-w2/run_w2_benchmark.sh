#!/usr/bin/env bash
set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="mosh"
WORKLOADS="top tail ping"

ITERATIONS=50
TRIALS=2
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
PING_TARGET=""
MIN_VALID_LATENCY_MS=-5000
MAX_VALID_LATENCY_MS=60000
MAX_INVALID_SAMPLES=100

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
  --min-valid-latency-ms "$MIN_VALID_LATENCY_MS"
  --max-valid-latency-ms "$MAX_VALID_LATENCY_MS"
  --max-invalid-samples "$MAX_INVALID_SAMPLES"
)

if [[ -n "$PING_TARGET" ]]; then
  CMD+=(--ping-target "$PING_TARGET")
fi

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
