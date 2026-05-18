#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"

COMMANDS=(
  "find /"
  "git status"
  "docker logs \$(docker ps -q | head -n 1)"
)

ITERATIONS=1
TRIALS=3
TIMEOUT=60
SAMPLE_TIMEOUT=300
COMMAND_IDLE_TIMEOUT=30
MAX_OUTPUT_LINES=100
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
MOSH_PREDICT="always"

SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

ACTION="${1:-run}"

print_usage() {
  cat <<'USAGE'
Usage:
  ./run_w4_benchmark.sh [action]

Actions:
  run      Run benchmark then plot (default)
  bench    Run benchmark only
  plot     Plot only from existing results
  list     List benchmark-related files in this folder
  results  List files in w4_results
  help     Show this help
USAGE
}

list_benchmark_files() {
  echo "Benchmark files in $(pwd):"
  find . -maxdepth 1 -type f \
    \( -name "*benchmark*.py" -o -name "run_*benchmark*.sh" -o -name "plot_*.py" \) \
    | sort
}

list_result_files() {
  echo "Result files in $OUTPUT_DIR:"
  find "$OUTPUT_DIR" -maxdepth 1 -type f 2>/dev/null | sort || true
}

build_cmd() {
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
}

run_benchmark() {
  build_cmd
  echo "=== W4 Large Output Benchmark ==="
  echo "Command:"
  printf '  %s \\\n' "${CMD[@]}"
  echo ""
  "${CMD[@]}"
}

plot_results() {
  "$PYTHON_BIN" plot_trend.py \
    --output-dir "$OUTPUT_DIR" \
    --prefix "w4" \
    --group-fields protocol workload
}

case "$ACTION" in
  run)
    run_benchmark
    plot_results
    ;;
  bench)
    run_benchmark
    ;;
  plot)
    plot_results
    ;;
  list)
    list_benchmark_files
    ;;
  results)
    list_result_files
    ;;
  help|-h|--help)
    print_usage
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    print_usage
    exit 1
    ;;
esac
