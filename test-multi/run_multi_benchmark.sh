#!/usr/bin/env bash
set -euo pipefail

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
CONNECTIONS=100
SPREAD_SECONDS=1
COMMAND="uname -a"
TIMEOUT=30
CONNECT_TIMEOUT=5
OUTPUT_DIR="multi_results"

SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true
BATCH_MODE=true
STRICT_HOST_KEY=false

CMD=(
  python3 multi_concurrent_ssh_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --connections "$CONNECTIONS"
  --spread-seconds "$SPREAD_SECONDS"
  --command "$COMMAND"
  --timeout "$TIMEOUT"
  --connect-timeout "$CONNECT_TIMEOUT"
  --output-dir "$OUTPUT_DIR"
  --ssh3-path "$SSH3_PATH"
)

$SSH3_INSECURE   && CMD+=(--ssh3-insecure)
$BATCH_MODE      && CMD+=(--batch-mode)
$STRICT_HOST_KEY && CMD+=(--strict-host-key-checking)

echo "=== Multi Concurrent SSH/SSH3 Benchmark ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"
