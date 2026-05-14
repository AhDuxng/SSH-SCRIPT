#!/usr/bin/env bash
# run_w1_benchmark.sh — One-scenario benchmark run.
#
# Usage:
#   ./run_w1_benchmark.sh <scenario>
#
# <scenario> is a free-form label (e.g. low / medium / high) used to:
#   - scope the output directory to w1_results/<scenario>/
#   - record the label into w1_line_log.csv / w1_session_setup.csv / w1_meta.json
# so that low / medium / high runs do NOT clobber each other and can be merged
# later by plot_trend.py on the `scenario` column.
#
# This script does NOT apply tc netem — that is the orchestrator's job
# (run_all_scenarios.sh). Here we assume the netem profile is already active
# on both endpoints, and we only capture a baseline snapshot and measure.
set -euo pipefail

SCENARIO="${1:?usage: $0 <scenario-label, e.g. low|medium|high>}"

HOST="100.66.79.93"
# HOST="10.42.0.206"
USER_NAME="pi"
SOURCE_IP="100.70.166.91"
# SOURCE_IP="10.42.0.1"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="command_loop"

COMMANDS=(
  "ls"
  "df -h"
  "ps aux"
  "grep -n root /etc/passwd"
)

ITERATIONS=50
WARMUP=3
TRIALS=10
TIMEOUT=20
SEED=42

OUTPUT_DIR="w1_results/${SCENARIO}"
PROMPT="__W1_PROMPT__# "

SSH3_PATH=":4433/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"

SHUFFLE_PAIRS=true
REOPEN_ON_FAILURE=true

mkdir -p "$OUTPUT_DIR"

BASELINE_FILE="$OUTPUT_DIR/baseline.txt"
echo "=== W1 Benchmark | scenario=$SCENARIO ==="
echo "=== Collecting baseline snapshot -> $BASELINE_FILE"

{
  echo "# Baseline snapshot for scenario=$SCENARIO"
  echo "# Captured at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Client -> $USER_NAME@$HOST (source $SOURCE_IP)"
  echo
  echo "## uname -a (client)"
  uname -a || true
  echo
  echo "## ssh version"
  ssh -V 2>&1 || true
  echo
  echo "## ssh3 version"
  (ssh3 --version 2>&1 || ssh3 -version 2>&1 || echo "ssh3 not found") | head -5
  echo
  echo "## mosh version"
  mosh --version 2>&1 | head -3 || true
  echo
  echo "## ping RTT (20 x 200ms)"
  ping -c 20 -i 0.2 -W 2 "$HOST" | tail -4 || true
  echo
  echo "## tc qdisc (server side, via ssh)"
  ssh -o StrictHostKeyChecking=no -o BatchMode=yes -i "$IDENTITY_FILE" \
      "$USER_NAME@$HOST" "tc qdisc show" 2>&1 || echo "(tc show on server failed)"
  echo
  echo "## uptime (client)"
  uptime || true
} >"$BASELINE_FILE" 2>&1

echo "=== Baseline done. Head of $BASELINE_FILE:"
head -20 "$BASELINE_FILE" || true
echo "..."
echo

CMD=(
  python w1_command_loop_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols $PROTOCOLS
  --workloads $WORKLOADS
  --iterations "$ITERATIONS"
  --warmup "$WARMUP"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --seed "$SEED"
  --output-dir "$OUTPUT_DIR"
  --scenario "$SCENARIO"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
  --commands "${COMMANDS[@]}"
)

$SSH3_INSECURE     && CMD+=(--ssh3-insecure)
$BATCH_MODE        && CMD+=(--batch-mode)
$STRICT_HOST_KEY   && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS     && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE && CMD+=(--reopen-on-failure)

echo "=== W1 Command Loop Benchmark | scenario=$SCENARIO ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

python plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w1" \
  --group-fields protocol workload command
