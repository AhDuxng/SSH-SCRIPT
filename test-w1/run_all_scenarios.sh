#!/usr/bin/env bash
# run_all_scenarios.sh — Orchestrate low/medium/high benchmark runs end-to-end.
#
# For each scenario the flow is:
#   1. clear tc on client AND server, sleep SETTLE_SEC
#   2. apply scenario on client AND server, sleep SETTLE_SEC
#   3. run W1 benchmark (writes w1_results/<scenario>/...)
#
# After all scenarios: clear tc on both ends as a final safety net.
#
# Usage:
#   ./run_all_scenarios.sh                # runs low, medium, high
#   ./run_all_scenarios.sh low medium     # subset / reorder
#
# Requirements:
#   - local set_network.sh at $LOCAL_SET_NETWORK (default: ../set_network.sh)
#   - remote set_network.sh at $REMOTE_SET_NETWORK on the Pi (default: ~/set_network.sh)
#   - passwordless sudo for `tc` on both sides (or accept prompts interactively)
#   - ssh key auth to the Pi already set up

set -euo pipefail

# --- Connection / interface config (keep in sync with run_w1_benchmark.sh) ----
HOST="10.42.0.206"
USER_NAME="pi"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

CLIENT_IFACE="${CLIENT_IFACE:-enp43s0}"   # override: CLIENT_IFACE=eth0 ./run_all_scenarios.sh
SERVER_IFACE="${SERVER_IFACE:-eth0}"     # override: SERVER_IFACE=eth0 ./run_all_scenarios.sh

LOCAL_SET_NETWORK="${LOCAL_SET_NETWORK:-../set_network.sh}"
REMOTE_SET_NETWORK="${REMOTE_SET_NETWORK:-~/set_network.sh}"

SETTLE_SEC="${SETTLE_SEC:-30}"

# --- Scenario list -----------------------------------------------------------
if [[ $# -gt 0 ]]; then
  SCENARIOS=("$@")
else
  SCENARIOS=(low medium high)
fi

# --- Helpers -----------------------------------------------------------------
log() {
  printf '[%(%H:%M:%S)T] %s\n' -1 "$*"
}

apply_client() {
  local profile="$1"
  log "client($CLIENT_IFACE) -> $profile"
  sudo bash "$LOCAL_SET_NETWORK" "$CLIENT_IFACE" "$profile"
}

apply_server() {
  local profile="$1"
  log "server($USER_NAME@$HOST, iface=$SERVER_IFACE) -> $profile"
  # -t for sudo password prompt if NOPASSWD is not configured.
  # Prepend sbin paths because `tc` (iproute2) is typically under /sbin
  # and non-login ssh shells often don't include it in PATH.
  ssh -t -o StrictHostKeyChecking=no -i "$IDENTITY_FILE" \
    "$USER_NAME@$HOST" \
    "PATH=/usr/sbin:/sbin:/usr/bin:/bin:\$PATH bash $REMOTE_SET_NETWORK $SERVER_IFACE $profile"
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

final_cleanup() {
  log "=== FINAL CLEANUP: clearing tc on both ends ==="
  apply_both clear || log "WARN: final clear failed (manual check recommended)"
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

# Install final-cleanup trap ONLY after validation passes, so a bad invocation
# does not poke tc on the remote host unnecessarily.
trap final_cleanup EXIT

log "=== W1 orchestrator ==="
log "Scenarios: ${SCENARIOS[*]}"
log "Client iface=$CLIENT_IFACE  Server iface=$SERVER_IFACE  settle=${SETTLE_SEC}s"
log "Local script : $LOCAL_SET_NETWORK"
log "Remote script: $REMOTE_SET_NETWORK (on $USER_NAME@$HOST)"
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
  ./run_w1_benchmark.sh "$scenario"
  log "--- benchmark for $scenario done"
done

echo
log "=== All scenarios done. Results:"
for scenario in "${SCENARIOS[@]}"; do
  log "  w1_results/$scenario/"
done

# trap handles final clear
