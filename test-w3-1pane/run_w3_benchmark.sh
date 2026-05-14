#!/usr/bin/env bash
# run_w3_benchmark.sh — One W3 (interactive keystroke latency) run.
#
# Usage:
#   ./run_w3_benchmark.sh <scenario>                 # timestamped subfolder auto-generated
#   ./run_w3_benchmark.sh <scenario> <run_tag>       # explicit subfolder name
#
# Output path: w3_results/<scenario>/<run_tag>/
#   where <run_tag> defaults to $(date +%Y%m%d_%H%M%S) in local time.
#
# Does NOT touch the network — the scenario label is just for folder scoping
# (e.g. "default" for Tailscale/VPN natural network, "low/medium/high" for tc
# netem profiles applied externally).
set -euo pipefail

SCENARIO="${1:?usage: $0 <scenario-label> [run_tag]}"
RUN_TAG="${2:-$(date +%Y%m%d_%H%M%S)}"

HOST="100.66.79.93"
USER_NAME="pi"
SOURCE_IP="100.70.166.91"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

PROTOCOLS="mosh"
WORKLOADS="interactive_shell vim nano"

ITERATIONS=100
WARMUP_ROUNDS=10
TRIALS=3
TIMEOUT=20
SEED=42

OUTPUT_DIR="w3_results/${SCENARIO}/${RUN_TAG}"
LOG_PEXPECT=false

PROMPT="__W3_PROMPT__# "

SSH3_PATH=":4433/ssh3-term"
SSH3_INSECURE=true

BATCH_MODE=false
STRICT_HOST_KEY=false
MOSH_PREDICT="never"

REMOTE_VIM_FILE="/tmp/w3_vim_bench.txt"
REMOTE_NANO_FILE="/tmp/w3_nano_bench.txt"

SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

mkdir -p "$OUTPUT_DIR"

BASELINE_FILE="$OUTPUT_DIR/baseline.txt"
{
  echo "# Baseline snapshot | scenario=$SCENARIO | run_tag=$RUN_TAG"
  echo "# Captured at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Client -> $USER_NAME@$HOST (source $SOURCE_IP)"
  echo
  echo "## uname -a (client)"
  uname -a || true
  echo
  echo "## ssh / ssh3 / mosh versions"
  ssh -V 2>&1 || true
  (ssh3 --version 2>&1 || ssh3 -version 2>&1 || echo "ssh3 not found") | head -5
  mosh --version 2>&1 | head -3 || true
  echo
  echo "## ping RTT (20 x 200ms)"
  ping -c 20 -i 0.2 -W 2 "$HOST" | tail -4 || true
  echo
  echo "## uptime (client)"
  uptime || true
} >"$BASELINE_FILE" 2>&1

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

$SSH3_INSECURE      && CMD+=(--ssh3-insecure)
$BATCH_MODE         && CMD+=(--batch-mode)
$STRICT_HOST_KEY    && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS      && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE  && CMD+=(--reopen-on-failure)
$LOG_PEXPECT        && CMD+=(--log-pexpect)

echo "=== W3 Interactive Benchmark | scenario=$SCENARIO | run_tag=$RUN_TAG ==="
echo "Output: $OUTPUT_DIR"
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

"${CMD[@]}"

python plot_trend.py \
  --output-dir "$OUTPUT_DIR" \
  --prefix "w3" \
  --group-fields protocol workload
