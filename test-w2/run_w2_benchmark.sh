#!/usr/bin/env bash
set -euo pipefail

HOST="100.66.79.93"
USER_NAME="pi"
SOURCE_IP="100.70.166.91"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="top tail ping"

ITERATIONS=50
TRIALS=10
TIMEOUT=30
SEED=42

<<<<<<< HEAD
OUTPUT_DIR="w2_results_low"
LOG_PEXPECT=true
=======
OUTPUT_DIR="w2_results"
LOG_PEXPECT=false
>>>>>>> master
PROMPT="__W2_PROMPT__# "

SSH3_PATH=":4433/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false

MOSH_PREDICT="never"

TOP_INTERVAL=1.0
PING_TARGET=""

MIN_VALID_LATENCY_MS=-5000
MAX_VALID_LATENCY_MS=60000
MAX_INVALID_SAMPLES=100

SHUFFLE_PAIRS=true

CLOCK_OFFSET_MODE="estimate"
CLOCK_OFFSET_PROBES=5
NEGATIVE_LATENCY_TOLERANCE_MS=50

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
  --clock-offset-mode "$CLOCK_OFFSET_MODE"
  --clock-offset-probes "$CLOCK_OFFSET_PROBES"
  --negative-latency-tolerance-ms "$NEGATIVE_LATENCY_TOLERANCE_MS"
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
$SHUFFLE_PAIRS     && CMD+=(--shuffle-pairs)

# Always reopen session on failure to continue remaining trials
CMD+=(--reopen-on-failure)

echo "=== W2 Continuous Monitoring Benchmark ==="
echo "Host              : $HOST"
echo "User              : $USER_NAME"
echo "Source IP         : $SOURCE_IP"
echo "Protocols         : $PROTOCOLS"
echo "Workloads         : $WORKLOADS"
echo "Trials            : $TRIALS"
echo "Iterations        : $ITERATIONS"
echo "Shuffle pairs     : $SHUFFLE_PAIRS"
echo "Clock offset mode : $CLOCK_OFFSET_MODE"
echo "Clock probes      : $CLOCK_OFFSET_PROBES"
echo "Neg latency tol   : $NEGATIVE_LATENCY_TOLERANCE_MS ms"
echo "Output dir        : $OUTPUT_DIR"
echo ""
echo "Command:"
printf '  %q \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

python plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w2" \
  --group-fields protocol workload
