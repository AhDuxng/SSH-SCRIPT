#!/usr/bin/env bash
# run_w4_benchmark.sh — One-scenario W4 (large output delivery) run.
#
# Usage:
#   ./run_w4_benchmark.sh <scenario>
#
# <scenario> is a label (low / medium / high) used for output scoping and
# recorded into w4_line_log.csv / w4_session_setup.csv / w4_meta.json.
#
# Does NOT touch tc netem — apply the netem profile on both endpoints first
# (or let run_all_scenarios.sh orchestrate).
set -euo pipefail

SCENARIO="${1:?usage: $0 <scenario-label, e.g. low|medium|high>}"

HOST="100.66.79.93"
# HOST="10.42.0.206"
USER_NAME="pi"
SOURCE_IP="100.70.166.91"
# SOURCE_IP="10.42.0.1"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="large_output"

# Deterministic, fixed-size, CPU-cheap outputs. base64 expands 3B -> 4 chars
# plus a newline every 76 chars, so the delivered stdout size is ~1.353x the
# input size. Pick 3 scales: small/medium/large.
COMMANDS=(
  "head -c 524288 /dev/zero | base64"    # ~692 KiB delivered
  "head -c 2097152 /dev/zero | base64"   # ~2.77 MiB delivered
  "head -c 8388608 /dev/zero | base64"   # ~11.1 MiB delivered
)

# For high-loss / bandwidth-constrained links, each 11 MiB sample over 10 Mbps
# takes ~9s. Keep ITERATIONS modest so the whole matrix (3 proto x 3 cmd x
# TRIALS x ITERATIONS) finishes in a reasonable time. Tune as needed.
ITERATIONS=10
WARMUP=2
TRIALS=5
TIMEOUT=300
COMMAND_IDLE_TIMEOUT=30
MAXREAD=65535
SEARCH_WINDOW_SIZE=8192
SEED=42

OUTPUT_DIR="w4_results/${SCENARIO}"
PROMPT="W4PROMPT# "

SSH3_PATH=":4433/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"

SHUFFLE_PAIRS=true
REOPEN_ON_FAILURE=true

mkdir -p "$OUTPUT_DIR"

BASELINE_FILE="$OUTPUT_DIR/baseline.txt"
echo "=== W4 Benchmark | scenario=$SCENARIO ==="
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
      "$USER_NAME@$HOST" \
      "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH tc qdisc show" 2>&1 \
      || echo "(tc show on server failed)"
  echo
  echo "## uptime (client)"
  uptime || true
} >"$BASELINE_FILE" 2>&1

echo "=== Baseline done. Head of $BASELINE_FILE:"
head -20 "$BASELINE_FILE" || true
echo "..."
echo

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
  --warmup "$WARMUP"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --command-idle-timeout "$COMMAND_IDLE_TIMEOUT"
  --maxread "$MAXREAD"
  --search-window-size "$SEARCH_WINDOW_SIZE"
  --seed "$SEED"
  --output-dir "$OUTPUT_DIR"
  --scenario "$SCENARIO"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
)

$SSH3_INSECURE     && CMD+=(--ssh3-insecure)
$BATCH_MODE        && CMD+=(--batch-mode)
$STRICT_HOST_KEY   && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS     && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE && CMD+=(--reopen-on-failure)

echo "=== W4 Large Output Benchmark | scenario=$SCENARIO ==="
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

python plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w4" \
  --group-fields protocol workload command
