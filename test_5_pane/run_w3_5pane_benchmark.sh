#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${HOST:-192.168.8.102}"
HOSTS="${HOSTS:-$HOST}"
USER_NAME="${USER_NAME:-trungnt}"
SOURCE_IP="${SOURCE_IP:-192.168.8.100}"
IDENTITY_FILE="${IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

PROTOCOLS="${PROTOCOLS:-ssh ssh3 mosh}"
WORKLOADS="${WORKLOADS:-interactive_shell vim nano}"

TRIALS="${TRIALS:-3}"
ITERATIONS="${ITERATIONS:-100}"
WARMUP_ROUNDS="${WARMUP_ROUNDS:-10}"
TIMEOUT="${TIMEOUT:-20}"
SEED="${SEED:-42}"

# Background panes redraw continuously. Keep probe chars rare and absent from
# the default background text to avoid matching the wrong pane.
PROBE_CHARS="${PROBE_CHARS:-QZ}"
PROBE_SEARCH_WINDOW="${PROBE_SEARCH_WINDOW:-0}"
EDITOR_CLEANUP_BATCH="${EDITOR_CLEANUP_BATCH:-32}"

OUTPUT_DIR="${OUTPUT_DIR:-w3_5pane_results}"
PROMPT="${PROMPT:-__W3_PROMPT__# }"

TMUX_SESSION="${TMUX_SESSION:-w3bench5}"
TMUX_WINDOW="${TMUX_WINDOW:-0}"
PANE0_INDEX="${PANE0_INDEX:-0}"
TMUX_SETUP_SCRIPT="${TMUX_SETUP_SCRIPT:-~/w3_tmux_setup.sh}"
INSTALL_REMOTE_SETUP="${INSTALL_REMOTE_SETUP:-auto}"
RESET_REMOTE_TMUX="${RESET_REMOTE_TMUX:-true}"
HEADLESS_FALLBACK_SETUP="${HEADLESS_FALLBACK_SETUP:-true}"
TMUX_READY_MARKER="${TMUX_READY_MARKER:-__W3_5PANE_PANE0_READY__}"
TMUX_READY_TIMEOUT="${TMUX_READY_TIMEOUT:-60}"
TMUX_READY_POLL_INTERVAL="${TMUX_READY_POLL_INTERVAL:-0.5}"
REMOTE_SETUP_LOG="${REMOTE_SETUP_LOG:-/tmp/w3_tmux_setup_${TMUX_SESSION}.log}"
SETUP_TOKEN="${SETUP_TOKEN:-W3_PANE0_TOKEN_$(date +%Y%m%d_%H%M%S)_$$}"
ATTACH_BOOT_MARKER="${ATTACH_BOOT_MARKER:-__W3_ATTACH_PANE0_READY__}"
RESPAWN_PANE0_ON_ATTACH="${RESPAWN_PANE0_ON_ATTACH:-true}"
PANE0_RC_PATH="${PANE0_RC_PATH:-/tmp/w3_pane0_rc_${TMUX_SESSION//[^A-Za-z0-9_.-]/_}}"

SSH3_PATH="${SSH3_PATH:-/ssh3-term}"
SSH3_INSECURE="${SSH3_INSECURE:-true}"
BATCH_MODE="${BATCH_MODE:-false}"
STRICT_HOST_KEY="${STRICT_HOST_KEY:-false}"
MOSH_PREDICT="${MOSH_PREDICT:-always}"
SHUFFLE_PAIRS="${SHUFFLE_PAIRS:-false}"
REOPEN_ON_FAILURE="${REOPEN_ON_FAILURE:-true}"

REMOTE_VIM_FILE="${REMOTE_VIM_FILE:-/tmp/w3_vim_bench.txt}"
REMOTE_NANO_FILE="${REMOTE_NANO_FILE:-/tmp/w3_nano_bench.txt}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

is_true() {
  case "${1:-false}" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

should_install_setup_if_missing() {
  case "${INSTALL_REMOTE_SETUP:-auto}" in
    auto|AUTO|true|TRUE|1|yes|YES|force|FORCE) return 0 ;;
    *) return 1 ;;
  esac
}

sanitize_token() {
  local value="$1"
  value="${value//[^A-Za-z0-9_.-]/_}"
  value="${value//./_}"
  printf '%s' "${value:-unknown}"
}

drop_proto_from_list() {
  local list="$1"
  local name="$2"
  printf ' %s ' "$list" | sed "s/ ${name} / /g" | xargs || true
}

has_proto() {
  local name="$1"
  [[ " $PROTOCOLS " == *" $name "* ]]
}

drop_proto() {
  local name="$1"
  PROTOCOLS="$(drop_proto_from_list "$PROTOCOLS" "$name")"
}

REAL_SSH="$(command -v ssh || true)"
REAL_MOSH="$(command -v mosh || true)"
REAL_SSH3="${SSH3_BIN:-$(command -v ssh3 || true)}"

if [[ -z "$REAL_SSH" ]]; then
  echo "ERROR: ssh not found in PATH." >&2
  exit 1
fi

if has_proto mosh && [[ -z "$REAL_MOSH" ]]; then
  echo "WARN: mosh not found, removing mosh from PROTOCOLS." >&2
  drop_proto mosh
fi

if has_proto ssh3 && [[ -z "$REAL_SSH3" ]]; then
  echo "WARN: ssh3 not found, removing ssh3 from PROTOCOLS." >&2
  drop_proto ssh3
fi

if [[ -z "$PROTOCOLS" ]]; then
  echo "ERROR: no available protocol after dependency checks." >&2
  exit 1
fi

SSH_OPTS=()
if [[ -n "$SOURCE_IP" ]]; then
  SSH_OPTS+=( -b "$SOURCE_IP" )
fi
if is_true "$STRICT_HOST_KEY"; then
  SSH_OPTS+=( -o StrictHostKeyChecking=yes )
else
  SSH_OPTS+=( -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null )
fi
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_OPTS+=( -i "$IDENTITY_FILE" )
fi
if is_true "$BATCH_MODE"; then
  SSH_OPTS+=( -o BatchMode=yes )
fi

SETUP_TOKEN="$(sanitize_token "$SETUP_TOKEN")"
TMUX_SETUP_SCRIPT_RESOLVED=""
ATTACH_CMD=""
SSH_CTL=()

install_default_tmux_setup() {
  echo "[${HOST}] setup: installing default ~/w3_tmux_setup.sh"
  local install_cmd
  install_cmd="$(cat <<'REMOTE_INSTALL_CMD'
cat > "$HOME/w3_tmux_setup.sh" <<'REMOTE_W3_TMUX_SETUP'
#!/usr/bin/env bash
set -euo pipefail

SESSION="${W3_TMUX_SESSION:-w3bench5}"
READY_MARKER="${W3_READY_MARKER:-__W3_5PANE_PANE0_READY__}"
PANE0_RC="${W3_PANE0_RC:-/tmp/w3_pane0_rc_${SESSION}}"
RESET_SESSION="${W3_RESET_TMUX:-true}"

case "$RESET_SESSION" in
  true|TRUE|1|yes|YES|y|Y)
    tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
    ;;
esac

command -v tmux >/dev/null 2>&1 || {
  echo "tmux is required on the remote host" >&2
  exit 127
}

cat > "$PANE0_RC" <<PANE0_RC_EOF
stty echo -echoctl 2>/dev/null || true
alias exit='tmux detach-client -s ${SESSION} 2>/dev/null || builtin exit'
printf '%s\n' '${READY_MARKER}'
PANE0_RC_EOF
chmod 600 "$PANE0_RC" 2>/dev/null || true

printf -v pane0_cmd 'bash --rcfile %q -i' "$PANE0_RC"
tmux new-session -d -s "$SESSION" -n w3 "$pane0_cmd"

tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); printf \"pane1 heartbeat %06d %s\\n\" \"\$i\" \"\$(date +%H:%M:%S)\"; sleep 0.20; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); printf \"pane2 stream %06d abcdefghijk lmnoprstuvwxy 0123456789\\n\" \"\$i\"; sleep 0.01; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); clear; printf \"pane3 refresh %06d %s\\n\" \"\$i\" \"\$(date +%H:%M:%S)\"; n=0; while [ \$n -lt 12 ]; do n=\$((n+1)); printf \"pane3 row %02d value %04d\\n\" \"\$n\" \"\$((i*n))\"; done; sleep 0.25; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'log=/tmp/w3pane4_${SESSION}.log; : > \"\$log\"; (i=0; while :; do i=\$((i+1)); printf \"pane4 tail %06d %s abcdefghijk lmnoprstuvwxy\\n\" \"\$i\" \"\$(date +%H:%M:%S)\" >> \"\$log\"; sleep 0.05; done) & exec tail -f \"\$log\"'"

tmux set-window-option -t "$SESSION:0" synchronize-panes off >/dev/null
tmux select-layout -t "$SESSION:0" tiled >/dev/null 2>&1 || true
tmux select-pane -t "$SESSION:0.0" >/dev/null
REMOTE_W3_TMUX_SETUP
chmod +x "$HOME/w3_tmux_setup.sh"
printf '%s' "$HOME/w3_tmux_setup.sh"
REMOTE_INSTALL_CMD
)"
  TMUX_SETUP_SCRIPT_RESOLVED="$("${SSH_CTL[@]}" "$install_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ -z "$TMUX_SETUP_SCRIPT_RESOLVED" ]]; then
    echo "ERROR: failed to install default w3_tmux_setup.sh on ${HOST}." >&2
    return 1
  fi
}

resolve_tmux_setup_script() {
  if [[ "${INSTALL_REMOTE_SETUP:-auto}" == "force" || "${INSTALL_REMOTE_SETUP:-auto}" == "FORCE" ]]; then
    install_default_tmux_setup
    return
  fi

  local requested_q
  requested_q="$(printf '%q' "$TMUX_SETUP_SCRIPT")"

  local resolve_cmd
  resolve_cmd="for p in ${requested_q} \"\$HOME/w3_tmux_setup.sh\" \"w3_tmux_setup.sh\" \"remote/w3_tmux_setup.sh\"; do [ -f \"\$p\" ] && { printf '%s' \"\$p\"; exit 0; }; done; exit 1"

  TMUX_SETUP_SCRIPT_RESOLVED="$("${SSH_CTL[@]}" "$resolve_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ -n "$TMUX_SETUP_SCRIPT_RESOLVED" ]]; then
    return 0
  fi

  if should_install_setup_if_missing; then
    install_default_tmux_setup
    return
  fi

  echo "ERROR: cannot find w3_tmux_setup.sh on ${HOST}." >&2
  echo "Checked: $TMUX_SETUP_SCRIPT, ~/w3_tmux_setup.sh, w3_tmux_setup.sh, remote/w3_tmux_setup.sh" >&2
  return 1
}

run_remote_tmux_setup() {
  local setup_q log_q session_q marker_q rc_q reset_q
  setup_q="$(printf '%q' "$TMUX_SETUP_SCRIPT_RESOLVED")"
  log_q="$(printf '%q' "$REMOTE_SETUP_LOG")"
  session_q="$(printf '%q' "$TMUX_SESSION")"
  marker_q="$(printf '%q' "$TMUX_READY_MARKER")"
  rc_q="$(printf '%q' "$PANE0_RC_PATH")"
  reset_q="$(printf '%q' "$RESET_REMOTE_TMUX")"

  echo "[${HOST}] setup: run ${TMUX_SETUP_SCRIPT_RESOLVED} (session=${TMUX_SESSION})"
  local launch_cmd started
  launch_cmd="set -e; chmod +x ${setup_q}; nohup env TERM=xterm-256color W3_TMUX_SESSION=${session_q} W3_READY_MARKER=${marker_q} W3_PANE0_RC=${rc_q} W3_RESET_TMUX=${reset_q} bash ${setup_q} > ${log_q} 2>&1 < /dev/null & printf '%s\n' __W3_SETUP_STARTED__"
  started="$("${SSH_CTL[@]}" "$launch_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ "$started" != "__W3_SETUP_STARTED__" ]]; then
    echo "[${HOST}] setup: WARN setup launcher did not confirm start; will still wait for tmux." >&2
  fi
}

run_headless_tmux_setup() {
  local log_q session_q marker_q rc_q reset_q
  log_q="$(printf '%q' "$REMOTE_SETUP_LOG")"
  session_q="$(printf '%q' "$TMUX_SESSION")"
  marker_q="$(printf '%q' "$TMUX_READY_MARKER")"
  rc_q="$(printf '%q' "$PANE0_RC_PATH")"
  reset_q="$(printf '%q' "$RESET_REMOTE_TMUX")"

  echo "[${HOST}] setup: fallback headless tmux setup (session=${TMUX_SESSION})"
  "${SSH_CTL[@]}" "env TERM=xterm-256color W3_TMUX_SESSION=${session_q} W3_READY_MARKER=${marker_q} W3_PANE0_RC=${rc_q} W3_RESET_TMUX=${reset_q} bash -s > ${log_q} 2>&1" <<'REMOTE_HEADLESS_TMUX_SETUP'
set -euo pipefail

SESSION="${W3_TMUX_SESSION:-w3bench5}"
READY_MARKER="${W3_READY_MARKER:-__W3_5PANE_PANE0_READY__}"
PANE0_RC="${W3_PANE0_RC:-/tmp/w3_pane0_rc_${SESSION}}"
RESET_SESSION="${W3_RESET_TMUX:-true}"

case "$RESET_SESSION" in
  true|TRUE|1|yes|YES|y|Y)
    tmux kill-session -t "$SESSION" >/dev/null 2>&1 || true
    ;;
esac

command -v tmux >/dev/null 2>&1 || {
  echo "tmux is required on the remote host" >&2
  exit 127
}

cat > "$PANE0_RC" <<PANE0_RC_EOF
stty echo -echoctl 2>/dev/null || true
alias exit='tmux detach-client -s ${SESSION} 2>/dev/null || builtin exit'
printf '%s\n' '${READY_MARKER}'
PANE0_RC_EOF
chmod 600 "$PANE0_RC" 2>/dev/null || true

printf -v pane0_cmd 'bash --rcfile %q -i' "$PANE0_RC"
tmux new-session -d -s "$SESSION" -n w3 "$pane0_cmd"

tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); printf \"pane1 heartbeat %06d %s\\n\" \"\$i\" \"\$(date +%H:%M:%S)\"; sleep 0.20; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); printf \"pane2 stream %06d abcdefghijk lmnoprstuvwxy 0123456789\\n\" \"\$i\"; sleep 0.01; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'i=0; while :; do i=\$((i+1)); clear; printf \"pane3 refresh %06d %s\\n\" \"\$i\" \"\$(date +%H:%M:%S)\"; n=0; while [ \$n -lt 12 ]; do n=\$((n+1)); printf \"pane3 row %02d value %04d\\n\" \"\$n\" \"\$((i*n))\"; done; sleep 0.25; done'"
tmux split-window -d -t "$SESSION:0.0" "bash -lc 'log=/tmp/w3pane4_${SESSION}.log; : > \"\$log\"; (i=0; while :; do i=\$((i+1)); printf \"pane4 tail %06d %s abcdefghijk lmnoprstuvwxy\\n\" \"\$i\" \"\$(date +%H:%M:%S)\" >> \"\$log\"; sleep 0.05; done) & exec tail -f \"\$log\"'"

tmux set-window-option -t "$SESSION:0" synchronize-panes off >/dev/null
tmux select-layout -t "$SESSION:0" tiled >/dev/null 2>&1 || true
tmux select-pane -t "$SESSION:0.0" >/dev/null
REMOTE_HEADLESS_TMUX_SETUP
}

wait_tmux_ready() {
  local window="${TMUX_SESSION}:${TMUX_WINDOW}"
  local pane0="${window}.${PANE0_INDEX}"
  local window_q pane0_q marker_q log_q start_ts now pane_count marker_seen
  window_q="$(printf '%q' "$window")"
  pane0_q="$(printf '%q' "$pane0")"
  marker_q="$(printf '%q' "$TMUX_READY_MARKER")"
  log_q="$(printf '%q' "$REMOTE_SETUP_LOG")"

  echo "[${HOST}] setup: waiting for ${pane0} and 5 visible panes"
  start_ts="$(date +%s)"
  while true; do
    pane_count="$("${SSH_CTL[@]}" "tmux list-panes -t ${window_q} -F '#{pane_index}' 2>/dev/null | wc -l" 2>/dev/null | tr -dc '0-9' || true)"
    [[ "$pane_count" =~ ^[0-9]+$ ]] || pane_count=0
    marker_seen="no"
    if "${SSH_CTL[@]}" "tmux capture-pane -p -J -S -80 -t ${pane0_q} 2>/dev/null | grep -F -- ${marker_q} >/dev/null" >/dev/null 2>&1; then
      marker_seen="yes"
    fi
    if (( pane_count >= 5 )); then
      echo "[${HOST}] setup: pane ready (panes=${pane_count}, marker=${marker_seen})"
      return 0
    fi
    now="$(date +%s)"
    if (( now - start_ts >= 2 )); then
      if "${SSH_CTL[@]}" "grep -qi 'not a terminal' ${log_q} 2>/dev/null" >/dev/null 2>&1; then
        echo "[${HOST}] setup: detected non-interactive tmux failure in ${REMOTE_SETUP_LOG}" >&2
        return 1
      fi
    fi
    if (( now - start_ts >= TMUX_READY_TIMEOUT )); then
      echo "ERROR: timeout waiting for tmux 5-pane session on ${HOST}" >&2
      echo "[${HOST}] setup: remote log tail (${REMOTE_SETUP_LOG}):" >&2
      "${SSH_CTL[@]}" "tail -n 120 $(printf '%q' "$REMOTE_SETUP_LOG") 2>/dev/null || true" >&2 || true
      return 1
    fi
    sleep "$TMUX_READY_POLL_INTERVAL"
  done
}

send_token_to_pane0() {
  local window="${TMUX_SESSION}:${TMUX_WINDOW}"
  local pane0="${window}.${PANE0_INDEX}"
  local window_q pane0_q token_q token_cmd token_cmd_q
  window_q="$(printf '%q' "$window")"
  pane0_q="$(printf '%q' "$pane0")"
  token_q="$(printf '%q' "$SETUP_TOKEN")"
  token_cmd="printf '%s\n' ${token_q}"
  token_cmd_q="$(printf '%q' "$token_cmd")"

  echo "[${HOST}] setup: send token only to ${pane0}: ${SETUP_TOKEN}"
  "${SSH_CTL[@]}" "set -e; tmux set-window-option -t ${window_q} synchronize-panes off >/dev/null; tmux select-pane -t ${pane0_q} >/dev/null; tmux send-keys -t ${pane0_q} -l ${token_cmd_q}; tmux send-keys -t ${pane0_q} C-m" >/dev/null

  local start_ts now
  start_ts="$(date +%s)"
  while true; do
    if "${SSH_CTL[@]}" "tmux capture-pane -p -J -S -120 -t ${pane0_q} | grep -F -- ${token_q} >/dev/null" >/dev/null 2>&1; then
      break
    fi
    now="$(date +%s)"
    if (( now - start_ts >= TMUX_READY_TIMEOUT )); then
      echo "ERROR: token did not appear in ${pane0}." >&2
      return 1
    fi
    sleep "$TMUX_READY_POLL_INTERVAL"
  done

  local pane target_q leaked=0
  for pane in 1 2 3 4; do
    target_q="$(printf '%q' "${window}.${pane}")"
    if "${SSH_CTL[@]}" "tmux capture-pane -p -J -S -120 -t ${target_q} 2>/dev/null | grep -F -- ${token_q} >/dev/null" >/dev/null 2>&1; then
      echo "ERROR: token was also found in ${window}.${pane}; pane targeting is not isolated." >&2
      leaked=1
    fi
  done
  if (( leaked != 0 )); then
    return 1
  fi
  echo "[${HOST}] setup: token confirmed in pane 0 only"
}

build_attach_cmd() {
  local session_q window_q pane0_q rc_q boot_q
  local rc_line1_q rc_line2_q rc_line3_q pane_shell_cmd pane_shell_cmd_q
  local respawn_cmd=""
  session_q="$(printf '%q' "$TMUX_SESSION")"
  window_q="$(printf '%q' "${TMUX_SESSION}:${TMUX_WINDOW}")"
  pane0_q="$(printf '%q' "${TMUX_SESSION}:${TMUX_WINDOW}.${PANE0_INDEX}")"
  rc_q="$(printf '%q' "$PANE0_RC_PATH")"
  boot_q="$(printf '%q' "$ATTACH_BOOT_MARKER")"
  rc_line1_q="$(printf '%q' "stty echo -echoctl 2>/dev/null || true")"
  rc_line2_q="$(printf '%q' "alias exit='tmux detach-client -s ${TMUX_SESSION} 2>/dev/null || builtin exit'")"
  rc_line3_q="$(printf '%q' "printf '%s\\n' ${boot_q}")"
  pane_shell_cmd="exec bash --rcfile ${rc_q} -i"
  pane_shell_cmd_q="$(printf '%q' "$pane_shell_cmd")"

  if is_true "$RESPAWN_PANE0_ON_ATTACH"; then
    respawn_cmd="tmux respawn-pane -k -t ${pane0_q} ${pane_shell_cmd_q} >/dev/null 2>&1;"
  fi

  local attach_script
  attach_script="set -e; export TERM=\${TERM:-xterm-256color}; tmux has-session -t ${session_q} >/dev/null; printf '%s\n' ${rc_line1_q} ${rc_line2_q} ${rc_line3_q} > ${rc_q}; chmod 600 ${rc_q} 2>/dev/null || true; tmux set-window-option -t ${window_q} synchronize-panes off >/dev/null 2>&1 || true; ${respawn_cmd} tmux select-layout -t ${window_q} tiled >/dev/null 2>&1 || true; tmux select-pane -t ${pane0_q} >/dev/null; exec tmux attach -d -t ${session_q}"
  ATTACH_CMD="bash -lc $(printf '%q' "$attach_script")"
}

WRAP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WRAP_DIR"
}
trap cleanup EXIT

cat >"${WRAP_DIR}/ssh" <<'SSH_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
REAL_SSH="${W3_REAL_SSH:?W3_REAL_SSH is not set}"
ATTACH_CMD="${W3_ATTACH_CMD:?W3_ATTACH_CMD is not set}"
exec "$REAL_SSH" "$@" "$ATTACH_CMD"
SSH_WRAPPER

cat >"${WRAP_DIR}/mosh" <<'MOSH_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
REAL_MOSH="${W3_REAL_MOSH:?W3_REAL_MOSH is not set}"
REAL_SSH="${W3_REAL_SSH:?W3_REAL_SSH is not set}"
ATTACH_CMD="${W3_ATTACH_CMD:?W3_ATTACH_CMD is not set}"

real_ssh_q="$(printf '%q' "$REAL_SSH")"
rewritten=()
for arg in "$@"; do
  case "$arg" in
    --ssh=ssh*)
      ssh_cmd="${arg#--ssh=}"
      rewritten+=( "--ssh=${real_ssh_q}${ssh_cmd#ssh}" )
      ;;
    *)
      rewritten+=( "$arg" )
      ;;
  esac
done

exec "$REAL_MOSH" "${rewritten[@]}" "$ATTACH_CMD"
MOSH_WRAPPER

cat >"${WRAP_DIR}/ssh3" <<'SSH3_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
REAL_SSH3="${W3_REAL_SSH3:?W3_REAL_SSH3 is not set}"
ATTACH_CMD="${W3_ATTACH_CMD:?W3_ATTACH_CMD is not set}"
exec "$REAL_SSH3" "$@" "$ATTACH_CMD"
SSH3_WRAPPER

chmod +x "${WRAP_DIR}/ssh" "${WRAP_DIR}/mosh" "${WRAP_DIR}/ssh3"

export W3_REAL_SSH="$REAL_SSH"
export W3_REAL_MOSH="${REAL_MOSH:-$REAL_SSH}"
export W3_REAL_SSH3="${REAL_SSH3:-$REAL_SSH}"
export PATH="${WRAP_DIR}:${PATH}"

read -r -a HOST_ARRAY <<< "$HOSTS"
HOST_COUNT="${#HOST_ARRAY[@]}"
FAILED_HOSTS=()

if (( HOST_COUNT == 0 )); then
  echo "ERROR: HOSTS is empty." >&2
  exit 1
fi

run_for_host() {
  HOST="$1"
  SSH_CTL=("$REAL_SSH" "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}")
  TMUX_SETUP_SCRIPT_RESOLVED=""

  echo ""
  echo "=== W3 5-pane benchmark: ${USER_NAME}@${HOST} ==="
  echo "[${HOST}] protocols: ${PROTOCOLS}"
  echo "[${HOST}] workloads: ${WORKLOADS}"
  echo "[${HOST}] tmux: session=${TMUX_SESSION}, pane0=${TMUX_WINDOW}.${PANE0_INDEX}"
  echo "[${HOST}] probe chars/window: ${PROBE_CHARS}/${PROBE_SEARCH_WINDOW}"

  resolve_tmux_setup_script || return 1
  run_remote_tmux_setup || return 1
  if ! wait_tmux_ready; then
    if is_true "$HEADLESS_FALLBACK_SETUP"; then
      echo "[${HOST}] setup: retry with headless fallback because w3_tmux_setup.sh did not create 5 panes"
      if ! run_headless_tmux_setup; then
        echo "ERROR: headless fallback failed on ${HOST}; remote log tail (${REMOTE_SETUP_LOG}):" >&2
        "${SSH_CTL[@]}" "tail -n 120 $(printf '%q' "$REMOTE_SETUP_LOG") 2>/dev/null || true" >&2 || true
        return 1
      fi
      wait_tmux_ready || return 1
    else
      return 1
    fi
  fi
  send_token_to_pane0 || return 1
  build_attach_cmd

  export W3_ATTACH_CMD="$ATTACH_CMD"

  local host_output_dir="$OUTPUT_DIR"
  if (( HOST_COUNT > 1 )); then
    host_output_dir="${OUTPUT_DIR}/$(sanitize_token "$HOST")"
  fi
  mkdir -p "$host_output_dir"

  echo "[${HOST}] setup script: ${TMUX_SETUP_SCRIPT_RESOLVED}"
  echo "[${HOST}] attach command: ${ATTACH_CMD}"
  echo "[${HOST}] output dir: ${host_output_dir}"

  local -a cmd
  cmd=(
    "$PYTHON_BIN" w3_5pane_benchmark.py
      --host "$HOST"
      --user "$USER_NAME"
      --source-ip "$SOURCE_IP"
      --identity-file "$IDENTITY_FILE"
      --protocols $PROTOCOLS
      --workloads $WORKLOADS
      --trials "$TRIALS"
      --iterations "$ITERATIONS"
      --warmup-rounds "$WARMUP_ROUNDS"
      --timeout "$TIMEOUT"
      --seed "$SEED"
      --probe-chars "$PROBE_CHARS"
      --probe-search-window "$PROBE_SEARCH_WINDOW"
      --editor-cleanup-batch "$EDITOR_CLEANUP_BATCH"
      --output-dir "$host_output_dir"
      --prompt "$PROMPT"
      --ssh3-path "$SSH3_PATH"
      --mosh-predict "$MOSH_PREDICT"
      --remote-vim-file "$REMOTE_VIM_FILE"
      --remote-nano-file "$REMOTE_NANO_FILE"
  )

  is_true "$SSH3_INSECURE"     && cmd+=(--ssh3-insecure)
  is_true "$BATCH_MODE"        && cmd+=(--batch-mode)
  is_true "$STRICT_HOST_KEY"   && cmd+=(--strict-host-key-checking)
  is_true "$SHUFFLE_PAIRS"     && cmd+=(--shuffle-pairs)
  is_true "$REOPEN_ON_FAILURE" && cmd+=(--reopen-on-failure)

  printf '[%s] command: ' "$HOST"
  printf '%q ' "${cmd[@]}"
  printf '\n'

  local run_log="${host_output_dir}/w3_5pane_runner_$(date +%Y%m%d_%H%M%S).log"
  echo "[${HOST}] log file: ${run_log}"

  (
    "${cmd[@]}"
  ) 2>&1 | tee "$run_log" &
  local bench_pid=$!

  while kill -0 "$bench_pid" >/dev/null 2>&1; do
    echo "[${HOST}] benchmark is still running... $(date +%H:%M:%S)"
    sleep 20
  done

  local bench_status=0
  wait "$bench_pid" || bench_status=$?
  if (( bench_status != 0 )); then
    return "$bench_status"
  fi

  if [[ -f plot_trend.py ]]; then
    "$PYTHON_BIN" plot_trend.py \
      --output-dir "$host_output_dir" \
      --prefix "w3" \
      --group-fields protocol workload || return 1
  fi
}

for host in "${HOST_ARRAY[@]}"; do
  if ! run_for_host "$host"; then
    FAILED_HOSTS+=("$host")
    echo "[${host}] FAILED" >&2
  fi
done

if (( ${#FAILED_HOSTS[@]} > 0 )); then
  echo "ERROR: benchmark failed for host(s): ${FAILED_HOSTS[*]}" >&2
  exit 1
fi
