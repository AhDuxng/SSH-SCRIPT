#!/usr/bin/env bash
# run_all_scenarios.sh — Orchestrate W4 LAN high/medium/low runs end-to-end.
#
# Run this script on the Pi client that is connected to the Pi server over LAN.
# The default/Tailscale scenario is intentionally not part of this orchestrator;
# run it from your workstation with:
#   ./run_w4_benchmark.sh default
#
# Key difference vs a naive loop: we prime sudo ONCE (locally and on the server)
# and keep the timestamp fresh via a background loop, so subsequent
# set_network.sh calls between scenarios do NOT re-prompt for the password.
#
# For each scenario:
#   1. prepare the static 100 KiB W4 fixture on the server
#   2. clear tc on client AND server, sleep SETTLE_SEC
#   3. apply scenario on client AND server, sleep SETTLE_SEC
#   4. run W4 benchmark -> writes OUTPUT_ROOT/<scenario>/
#
# Usage:
#   ./run_all_scenarios.sh                # runs high, medium, low over LAN
#   ./run_all_scenarios.sh low high       # subset / reorder
#
# Overridable via env vars:
#   CLIENT_IFACE (default eth0)  SERVER_IFACE (default eth0)
#   SETTLE_SEC   (default 30)
#   LOCAL_SET_NETWORK   (default ../set_network.sh)
#   REMOTE_SET_NETWORK  (default ~/set_network.sh)

set -euo pipefail

# --- LAN connection / interface config (keep in sync with run_w4_benchmark.sh) ---
LAN_HOST="${LAN_HOST:-192.168.8.102}"
LAN_SOURCE_IP="${LAN_SOURCE_IP:-192.168.8.100}"
LAN_IDENTITY_FILE="${LAN_IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

HOST="${HOST:-$LAN_HOST}"
USER_NAME="${USER_NAME:-trungnt}"
SOURCE_IP="${SOURCE_IP:-$LAN_SOURCE_IP}"
IDENTITY_FILE="${IDENTITY_FILE:-$LAN_IDENTITY_FILE}"

CLIENT_IFACE="${CLIENT_IFACE:-eth0}"
SERVER_IFACE="${SERVER_IFACE:-eth0}"

LOCAL_SET_NETWORK="${LOCAL_SET_NETWORK:-../set_network.sh}"
REMOTE_SET_NETWORK="${REMOTE_SET_NETWORK:-~/set_network.sh}"

SETTLE_SEC="${SETTLE_SEC:-30}"
OUTPUT_ROOT="${OUTPUT_ROOT:-w4_results_trungnt/100KB}"
FIXTURE_DIR="${FIXTURE_DIR:-/tmp}"
FIXTURE_FILE="${FIXTURE_FILE:-$FIXTURE_DIR/w4_paths_100kb.txt}"
FIXTURE_BYTES="${FIXTURE_BYTES:-102400}"
FIXTURE_SCRIPT="${FIXTURE_SCRIPT:-setup_w4_fixtures.sh}"
SETUP_FIXTURES="${SETUP_FIXTURES:-true}"
RESUME="${RESUME:-false}"

# --- ssh ControlMaster (connection multiplexing to Pi) -----------------------
# One TCP + auth handshake; every subsequent ssh to the Pi reuses the socket.
SSH_SOCK_DIR="$(mktemp -d /tmp/w4-ssh-ctl.XXXXXX)"
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
  # Normal (non-tty) remote command, uses mux socket.
  ssh "${SSH_MUX_OPTS[@]}" "$USER_NAME@$HOST" "$@"
}

ssh_pi_tty() {
  # TTY allocated — for sudo password prompt the first time only.
  ssh -t "${SSH_MUX_OPTS[@]}" "$USER_NAME@$HOST" "$@"
}

# --- Scenario list -----------------------------------------------------------
if [[ $# -gt 0 ]]; then
  SCENARIOS=("$@")
else
  SCENARIOS=(high medium low)
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
  # -n: never prompt (the keepalive loop keeps the timestamp warm).
  # PATH prepended because tc lives in /sbin on most distros.
  ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH sudo -n bash $REMOTE_SET_NETWORK $SERVER_IFACE $profile"
}

apply_both() {
  local profile="$1"
  apply_client "$profile"
  apply_server "$profile"
}

setup_fixtures() {
  if [[ "$SETUP_FIXTURES" != "true" ]]; then
    log "Skipping W4 fixture setup (SETUP_FIXTURES=$SETUP_FIXTURES)"
    return
  fi
  if [[ ! -f "$FIXTURE_SCRIPT" ]]; then
    echo "ERROR: fixture setup script not found at $FIXTURE_SCRIPT" >&2
    exit 2
  fi
  local fixture_dir_q
  local fixture_file_q
  local fixture_bytes_q
  printf -v fixture_dir_q '%q' "$FIXTURE_DIR"
  printf -v fixture_file_q '%q' "$FIXTURE_FILE"
  printf -v fixture_bytes_q '%q' "$FIXTURE_BYTES"
  log "Preparing static W4 fixture on $USER_NAME@$HOST: $FIXTURE_FILE (${FIXTURE_BYTES} bytes)"
  ssh_pi "FIXTURE_DIR=$fixture_dir_q FIXTURE_FILE=$fixture_file_q FIXTURE_BYTES=$fixture_bytes_q bash -s" < "$FIXTURE_SCRIPT"
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
  # -t required so sudo can prompt; mux socket is already open from any prior
  # ssh, but the very first call opens it.
  ssh_pi_tty "sudo -v"

  # Local keepalive: refresh every 50s (default sudo timeout is 5 min).
  ( while true; do sudo -n true 2>/dev/null || exit; sleep 50; done ) &
  LOCAL_KEEPALIVE_PID=$!
  log "local sudo keepalive pid=$LOCAL_KEEPALIVE_PID"

  # Remote keepalive: run `sudo -n true` over the mux socket every 50s.
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

  if [[ -n "$LOCAL_KEEPALIVE_PID" ]]; then
    kill "$LOCAL_KEEPALIVE_PID" 2>/dev/null || true
    wait "$LOCAL_KEEPALIVE_PID" 2>/dev/null || true
  fi
  if [[ -n "$REMOTE_KEEPALIVE_PID" ]]; then
    kill "$REMOTE_KEEPALIVE_PID" 2>/dev/null || true
    wait "$REMOTE_KEEPALIVE_PID" 2>/dev/null || true
  fi

  # Best-effort final tc clear (sudo is already primed).
  log "FINAL: clear tc on both ends"
  apply_both clear 2>/dev/null || log "WARN: final clear failed (check manually)"

  # Close ssh mux + scrub temp dir.
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
    *) echo "ERROR: unknown LAN scenario '$scenario' (allowed: low, medium, high). Run default via ./run_w4_benchmark.sh default from your workstation." >&2; exit 2 ;;
  esac
done

trap cleanup EXIT

log "=== W4 orchestrator ==="
log "Scenarios: ${SCENARIOS[*]}"
log "Client iface=$CLIENT_IFACE  Server iface=$SERVER_IFACE  settle=${SETTLE_SEC}s"
log "LAN target=$USER_NAME@$HOST  LAN source IP=$SOURCE_IP"
log "Output root=$OUTPUT_ROOT  Fixture file=$FIXTURE_FILE  Fixture bytes=$FIXTURE_BYTES"
log "Resume=$RESUME"
log "Local  : $LOCAL_SET_NETWORK"
log "Remote : $REMOTE_SET_NETWORK (on $USER_NAME@$HOST)"
log "ssh mux: $SSH_SOCK"
echo

prime_sudo
setup_fixtures
echo

# --- Main loop ---------------------------------------------------------------
START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "Orchestrator started at $START_TS"

for scenario in "${SCENARIOS[@]}"; do
  echo
  log "==================== SCENARIO: $scenario ===================="

  log "--- step 1/3: clear tc on both ends"
  apply_both clear
  sleep_with_dots "$SETTLE_SEC" "post-clear settle"

  log "--- step 2/3: apply $scenario on both ends"
  apply_both "$scenario"
  sleep_with_dots "$SETTLE_SEC" "post-apply settle"

  # --- preflight: verify tc netem actually shaped the path ---
  log "--- preflight: ping RTT check"
  case "$scenario" in
    low)    expected_rtt_ms=20  ;;
    medium) expected_rtt_ms=100 ;;
    high)   expected_rtt_ms=200 ;;
    *)      expected_rtt_ms=0   ;;
  esac
  if [[ $expected_rtt_ms -gt 0 ]]; then
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
      log "ERROR: RTT off by >50% — tc may not be shaping the path to $HOST."
      log "       Check CLIENT_IFACE=$CLIENT_IFACE and SERVER_IFACE=$SERVER_IFACE"
      log "       (must be the iface that traffic to $HOST actually uses)."
      exit 3
    fi
  fi

  log "--- step 3/3: run benchmark for $scenario"
  HOST="$HOST" \
  USER_NAME="$USER_NAME" \
  SOURCE_IP="$SOURCE_IP" \
  IDENTITY_FILE="$IDENTITY_FILE" \
  OUTPUT_ROOT="$OUTPUT_ROOT" \
  FIXTURE_DIR="$FIXTURE_DIR" \
  FIXTURE_FILE="$FIXTURE_FILE" \
  FIXTURE_BYTES="$FIXTURE_BYTES" \
  RESUME="$RESUME" \
    ./run_w4_benchmark.sh "$scenario"
  log "--- benchmark for $scenario done"
done

echo
log "=== All scenarios done. Results:"
for scenario in "${SCENARIOS[@]}"; do
  log "  $OUTPUT_ROOT/$scenario/"
done
# cleanup trap handles final clear + mux close
