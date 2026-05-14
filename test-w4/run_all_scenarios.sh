#!/usr/bin/env bash
# run_all_scenarios.sh — Orchestrate W4 low/medium/high runs end-to-end.
#
# Key difference vs a naive loop: we prime sudo ONCE (locally and on the Pi)
# and keep the timestamp fresh via a background loop, so subsequent
# set_network.sh calls between scenarios do NOT re-prompt for the password.
#
# For each scenario:
#   1. clear tc on client AND server, sleep SETTLE_SEC
#   2. apply scenario on client AND server, sleep SETTLE_SEC
#   3. run W4 benchmark -> writes w4_results/<scenario>/
#
# Usage:
#   ./run_all_scenarios.sh                # runs low, medium, high
#   ./run_all_scenarios.sh low medium     # subset / reorder
#
# Overridable via env vars:
#   CLIENT_IFACE (default enp43s0)  SERVER_IFACE (default eth0)
#   SETTLE_SEC   (default 30)
#   LOCAL_SET_NETWORK   (default ../set_network.sh)
#   REMOTE_SET_NETWORK  (default ~/set_network.sh)

set -euo pipefail

# --- Connection / interface config (keep in sync with run_w4_benchmark.sh) ---
HOST="10.42.0.206"
USER_NAME="pi"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

CLIENT_IFACE="${CLIENT_IFACE:-enp43s0}"
SERVER_IFACE="${SERVER_IFACE:-eth0}"

LOCAL_SET_NETWORK="${LOCAL_SET_NETWORK:-../set_network.sh}"
REMOTE_SET_NETWORK="${REMOTE_SET_NETWORK:-~/set_network.sh}"

SETTLE_SEC="${SETTLE_SEC:-30}"

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
  # -n: never prompt (the keepalive loop keeps the timestamp warm).
  # PATH prepended because tc lives in /sbin on most distros.
  ssh_pi "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH sudo -n bash $REMOTE_SET_NETWORK $SERVER_IFACE $profile"
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
    *) echo "ERROR: unknown scenario '$scenario' (allowed: low, medium, high)" >&2; exit 2 ;;
  esac
done

trap cleanup EXIT

log "=== W4 orchestrator ==="
log "Scenarios: ${SCENARIOS[*]}"
log "Client iface=$CLIENT_IFACE  Server iface=$SERVER_IFACE  settle=${SETTLE_SEC}s"
log "Local  : $LOCAL_SET_NETWORK"
log "Remote : $REMOTE_SET_NETWORK (on $USER_NAME@$HOST)"
log "ssh mux: $SSH_SOCK"
echo

prime_sudo
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

  log "--- step 3/3: run benchmark for $scenario"
  ./run_w4_benchmark.sh "$scenario"
  log "--- benchmark for $scenario done"
done

echo
log "=== All scenarios done. Results:"
for scenario in "${SCENARIOS[@]}"; do
  log "  w4_results/$scenario/"
done
# cleanup trap handles final clear + mux close
