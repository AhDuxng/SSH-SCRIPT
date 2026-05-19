#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${HOST:-192.168.8.102}"
USER_NAME="${USER_NAME:-trungnt}"
SOURCE_IP="${SOURCE_IP:-192.168.8.100}"
IDENTITY_FILE="${IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

PROTOCOLS="${PROTOCOLS:-ssh ssh3 mosh}"
COMMANDS=(
  "find /"
  "git status"
  "docker logs \$(docker ps -q | head -n 1)"
)

ITERATIONS="${ITERATIONS:-100}"
TRIALS="${TRIALS:-5}"
TIMEOUT="${TIMEOUT:-20}"
SAMPLE_TIMEOUT="${SAMPLE_TIMEOUT:-60}"
COMMAND_IDLE_TIMEOUT="${COMMAND_IDLE_TIMEOUT:-15}"
MAX_OUTPUT_LINES="${MAX_OUTPUT_LINES:-1000}"
MAXREAD="${MAXREAD:-65535}"
SEED="${SEED:-42}"

OUTPUT_DIR="${OUTPUT_DIR:-w4_results}"
PROMPT="${PROMPT:-__W4_PROMPT__# }"

SSH3_PATH="${SSH3_PATH:-/ssh3-term}"
SSH3_INSECURE="${SSH3_INSECURE:-true}"
BATCH_MODE="${BATCH_MODE:-false}"
STRICT_HOST_KEY="${STRICT_HOST_KEY:-false}"
MOSH_PREDICT="${MOSH_PREDICT:-always}"
SHUFFLE_PAIRS="${SHUFFLE_PAIRS:-false}"
REOPEN_ON_FAILURE="${REOPEN_ON_FAILURE:-true}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [[ ! -f w4_large_output_benchmark.py ]]; then
  echo "ERROR: w4_large_output_benchmark.py not found in $SCRIPT_DIR" >&2
  exit 1
fi

CMD=(
  "$PYTHON_BIN" w4_large_output_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --commands "${COMMANDS[@]}"
  --iterations "$ITERATIONS"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --sample-timeout "$SAMPLE_TIMEOUT"
  --command-idle-timeout "$COMMAND_IDLE_TIMEOUT"
  --max-output-lines "$MAX_OUTPUT_LINES"
  --maxread "$MAXREAD"
  --seed "$SEED"
  --output-dir "$OUTPUT_DIR"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
)

[[ "$SSH3_INSECURE" == "true" ]] && CMD+=(--ssh3-insecure)
[[ "$BATCH_MODE" == "true" ]] && CMD+=(--batch-mode)
[[ "$STRICT_HOST_KEY" == "true" ]] && CMD+=(--strict-host-key-checking)
[[ "$SHUFFLE_PAIRS" == "true" ]] && CMD+=(--shuffle-pairs)
[[ "$REOPEN_ON_FAILURE" == "true" ]] && CMD+=(--reopen-on-failure)

echo "=== W4 Real Large Output Benchmark ==="
echo "Host      : $USER_NAME@$HOST"
echo "Protocols : $PROTOCOLS"
echo "Max lines : $MAX_OUTPUT_LINES per command sample"
echo "Commands  :"
for command in "${COMMANDS[@]}"; do
  printf '  - %s\n' "$command"
done
echo "Command:"
printf '  %q' "${CMD[@]}"
echo
echo

"${CMD[@]}"

if [[ -f plot_trend.py ]]; then
  "$PYTHON_BIN" plot_trend.py \
    --output-dir "$OUTPUT_DIR" \
    --prefix "w4" \
    --group-fields protocol workload
fi
