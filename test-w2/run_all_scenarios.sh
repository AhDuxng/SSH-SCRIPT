#!/usr/bin/env bash
# run_all_scenarios.sh — Orchestrate W2 low/medium/high runs end-to-end.
#
# Primes sudo ONCE (locally and on the Pi) and keeps the timestamp fresh via
# a background keepalive loop, so no password is re-prompted between scenarios.
#
# For each scenario:
#   1. clear tc on client AND server, sleep SETTLE_SEC
#   2. apply scenario on client AND server, sleep SETTLE_SEC
#   3. ping RTT preflight check
#   4. run W2 benchmark -> writes w2_results/<scenario>/
#   5. (between scenarios) sleep SETTLE_SEC before next clear
#
# Usage:
#   ./run_all_scenarios.sh                # runs low, medium, high
#   ./run_all_scenarios.sh low medium     # subset / reorder
#
# Overridable via env vars:
#   CLIENT_IFACE (default enp43s0)   SERVER_IFACE (default eth0)
#   SETTLE_SEC   (default 30)
#   LOCAL_SET_NETWORK  (default ../set_network.sh)

set -euo pipefail

# --- Connection / interface config -------------------------------------------
HOST="10.42.0.206"
USER_NAME="pi"
SOURCE_IP="10.42.0.1"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

CLIENT_IFACE="${CLIENT_IFACE:-enp43s0}"
SERVER_IFACE="${SERVER_IFACE:-eth0}"

LOCAL_SET_NETWORK="${LOCAL_SET_NETWORK:-../set_network.sh}"

SETTLE_SEC="${SETTLE_SEC:-30}"

# --- Benchmark params (keep in sync with run_w2_benchmark.sh) ----------------
PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="top tail ping"
ITERATIONS=100
TRIALS=10
TIMEOUT=30
SEED=42
SSH3_PATH=":4433/ssh3-term"
MOSH_PREDICT="never"
PROMPT="__W2_PROMPT__# "
MIN_VALID_LATENCY_MS=-5000
MAX_VALID_LATENCY_MS=60000
MAX_INVALID_SAMPLES=100
CLOCK_OFFSET_MODE="estimate"
CLOCK_OFFSET_PROBES=10
NEGATIVE_LATENCY_TOLERANCE_MS=50
TOP_INTERVAL=1.0

# --- SSH ControlMaster (one TCP+auth handshake; all subsequent ssh reuse it) --
SSH_SOCK_DIR="$(mktemp -d /tmp/w2-ssh-ctl.XXXXXX)"
SSH_SOCK="$SSH_SOCK_DIR/ctl"
SSH_MUX_OPTS=(
  -o ControlMaster=auto
  -o "ControlPath=$SSH_SOCK"
  -o ControlPersist=2h
  -o StrictHostKeyChecking=no
  -o BatchMode=no
  -i "$IDENTITY_FILE"
)

ssh_pi() {
  ssh "${SSH_MUX_OPTS[@]}" "$USER_NAME@$HOST" "$@"
}

ssh_pi_tty() {
  ssh -t "${SSH_MUX_OPTS[@]}" "$USER_NAME@$HOST" "$@"
}

# --- Scenario list -----------------------------------------------------------
if [[ $# -gt 0 ]]; then
  SCENARIOS=("$@")
else
  SCENARIOS=(low medium high)
fi

# --- Helpers -----------------------------------------------------------------
log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }

apply_client() {
  local profile="$1"
  log "client($CLIENT_IFACE) -> $profile"
  sudo -n bash "$LOCAL_SET_NETWORK" "$CLIENT_IFACE" "$profile"
}

apply_server() {
  local profile="$1"
  log "server($USER_NAME@$HOST, iface=$SERVER_IFACE) -> $profile"
  case "$profile" in
    low)
      ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH \
        sudo -n tc qdisc del dev $SERVER_IFACE root 2>/dev/null || true; \
        sudo -n tc qdisc add dev $SERVER_IFACE root handle 1: tbf rate 100mbit burst 32kbit latency 400ms; \
        sudo -n tc qdisc add dev $SERVER_IFACE parent 1:1 handle 10: netem delay 10ms loss 0%"
      ;;
    medium)
      ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH \
        sudo -n tc qdisc del dev $SERVER_IFACE root 2>/dev/null || true; \
        sudo -n tc qdisc add dev $SERVER_IFACE root handle 1: tbf rate 40mbit burst 32kbit latency 400ms; \
        sudo -n tc qdisc add dev $SERVER_IFACE parent 1:1 handle 10: netem delay 50ms 4ms distribution normal loss 1.5%"
      ;;
    high)
      ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH \
        sudo -n tc qdisc del dev $SERVER_IFACE root 2>/dev/null || true; \
        sudo -n tc qdisc add dev $SERVER_IFACE root handle 1: tbf rate 10mbit burst 32kbit latency 400ms; \
        sudo -n tc qdisc add dev $SERVER_IFACE parent 1:1 handle 10: netem delay 100ms 16ms distribution normal loss 3%"
      ;;
    clear)
      ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH \
        sudo -n tc qdisc del dev $SERVER_IFACE root 2>/dev/null || true"
      ;;
  esac
}

apply_both() {
  local profile="$1"
  apply_client "$profile"
  apply_server "$profile"
}

sleep_with_dots() {
  local seconds="$1"
  local msg="$2"
  log "$msg (sleeping ${seconds}s)"
  local i=0
  while [[ $i -lt $seconds ]]; do
    sleep 1
    i=$((i + 1))
    if [[ $((i % 5)) -eq 0 ]]; then
      printf '   ...%ds\n' "$i"
    fi
  done
}

# --- Sudo priming + keepalive ------------------------------------------------
LOCAL_KEEPALIVE_PID=""
REMOTE_KEEPALIVE_PID=""

prime_sudo() {
  log "Priming LOCAL sudo (may prompt once)..."
  sudo -v

  log "Priming REMOTE sudo on $USER_NAME@$HOST (may prompt once)..."
  ssh_pi_tty "sudo -v"

  ( while true; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &
  LOCAL_KEEPALIVE_PID=$!
  log "local sudo keepalive pid=$LOCAL_KEEPALIVE_PID"

  ( while true; do
      ssh_pi "sudo -n true" 2>/dev/null || exit
      sleep 50
    done
  ) &
  REMOTE_KEEPALIVE_PID=$!
  log "remote sudo keepalive pid=$REMOTE_KEEPALIVE_PID"
}

# --- Cleanup trap ------------------------------------------------------------
cleanup() {
  local rc=$?
  log "=== cleanup (exit=$rc) ==="

  [[ -n "$LOCAL_KEEPALIVE_PID" ]] && { kill "$LOCAL_KEEPALIVE_PID" 2>/dev/null || true; wait "$LOCAL_KEEPALIVE_PID" 2>/dev/null || true; }
  [[ -n "$REMOTE_KEEPALIVE_PID" ]] && { kill "$REMOTE_KEEPALIVE_PID" 2>/dev/null || true; wait "$REMOTE_KEEPALIVE_PID" 2>/dev/null || true; }

  log "FINAL: clear tc on both ends"
  apply_both clear 2>/dev/null || log "WARN: final clear failed (check manually)"

  ssh -O exit "${SSH_MUX_OPTS[@]}" "$USER_NAME@$HOST" 2>/dev/null || true
  rm -rf "$SSH_SOCK_DIR"
}

# --- Pre-flight --------------------------------------------------------------
if [[ ! -f "$LOCAL_SET_NETWORK" ]]; then
  echo "ERROR: local set_network.sh not found at $LOCAL_SET_NETWORK" >&2
  exit 2
fi

for scenario in "${SCENARIOS[@]}"; do
  case "$scenario" in
    low|medium|high) ;;
    *) echo "ERROR: unknown scenario '$scenario' (allowed: low, medium, high)" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

trap cleanup EXIT

log "=== W2 orchestrator ==="
log "Scenarios : ${SCENARIOS[*]}"
log "Client    : $CLIENT_IFACE   Server: $SERVER_IFACE   settle: ${SETTLE_SEC}s"
log "Host      : $USER_NAME@$HOST"
echo

prime_sudo
echo

# --- Main loop ---------------------------------------------------------------
TOTAL=${#SCENARIOS[@]}
IDX=0

for scenario in "${SCENARIOS[@]}"; do
  IDX=$((IDX + 1))
  OUTPUT_DIR="w2_results/$scenario"

  echo
  log "==================== SCENARIO $IDX/$TOTAL: $scenario ===================="

  log "--- step 1/4: clear tc on both ends"
  apply_both clear
  sleep_with_dots "$SETTLE_SEC" "post-clear settle"

  log "--- step 2/4: apply $scenario on both ends"
  apply_both "$scenario"
  sleep_with_dots "$SETTLE_SEC" "post-apply settle"

  log "--- preflight: ping RTT check"
  case "$scenario" in
    low)    expected_rtt_ms=20  ;;
    medium) expected_rtt_ms=100 ;;
    high)   expected_rtt_ms=200 ;;
  esac
  avg_rtt="$(ping -c 10 -i 0.2 -W 5 "$HOST" 2>/dev/null \
    | awk -F'/' '/^rtt/ {print $5}')"
  if [[ -z "$avg_rtt" ]]; then
    log "ERROR: ping to $HOST failed — tc may have broken connectivity"
    exit 3
  fi
  verdict="$(awk -v m="$avg_rtt" -v e="$expected_rtt_ms" \
    'BEGIN { d=(m-e); if (d<0) d=-d; pct=(d*100)/e; printf "%.1f %s", pct, (pct>50?"FAIL":"OK") }')"
  pct="${verdict%% *}"
  status="${verdict##* }"
  log "expected RTT ≈ ${expected_rtt_ms}ms, measured ≈ ${avg_rtt}ms (deviation ${pct}%) [$status]"
  if [[ "$status" != "OK" ]]; then
    log "ERROR: RTT off by >50% — check CLIENT_IFACE=$CLIENT_IFACE and SERVER_IFACE=$SERVER_IFACE"
    exit 3
  fi

  log "--- step 3/4: run W2 benchmark for $scenario -> $OUTPUT_DIR"
  mkdir -p "$OUTPUT_DIR"

  python w2_continuous_monitoring_benchmark.py \
    --host "$HOST" \
    --user "$USER_NAME" \
    --source-ip "$SOURCE_IP" \
    --identity-file "$IDENTITY_FILE" \
    --protocols $PROTOCOLS \
    --workloads $WORKLOADS \
    --iterations "$ITERATIONS" \
    --trials "$TRIALS" \
    --timeout "$TIMEOUT" \
    --seed "$SEED" \
    --output-dir "$OUTPUT_DIR" \
    --prompt "$PROMPT" \
    --ssh3-path "$SSH3_PATH" \
    --ssh3-insecure \
    --mosh-predict "$MOSH_PREDICT" \
    --top-interval "$TOP_INTERVAL" \
    --clock-offset-mode "$CLOCK_OFFSET_MODE" \
    --clock-offset-probes "$CLOCK_OFFSET_PROBES" \
    --negative-latency-tolerance-ms "$NEGATIVE_LATENCY_TOLERANCE_MS" \
    --min-valid-latency-ms "$MIN_VALID_LATENCY_MS" \
    --max-valid-latency-ms "$MAX_VALID_LATENCY_MS" \
    --max-invalid-samples "$MAX_INVALID_SAMPLES" \
    --shuffle-pairs \
    --reopen-on-failure

  python plot_trend.py \
    --output-dir "$OUTPUT_DIR" \
    --prefix "w2" \
    --group-fields protocol workload

  log "--- benchmark for $scenario done. Results: $OUTPUT_DIR"

  log "--- step 4/4: clear tc after benchmark"
  apply_both clear

  if [[ $IDX -lt $TOTAL ]]; then
    sleep_with_dots "$SETTLE_SEC" "post-benchmark settle before next scenario"
  fi
done

echo
log "=== All scenarios done. Results:"
for scenario in "${SCENARIOS[@]}"; do
  log "  w2_results/$scenario/"
done
# cleanup trap handles final clear + mux close
