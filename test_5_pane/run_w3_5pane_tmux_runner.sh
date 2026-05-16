#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Default keeps the same single-Pi style as the other runners. For two Pis,
# override HOSTS explicitly, for example:
#   HOSTS="192.168.8.102 192.168.8.103" ./run_w3_5pane_tmux_runner.sh
HOST="${HOST:-192.168.8.102}"
HOSTS="${HOSTS:-$HOST}"
USER_NAME="${USER_NAME:-trungnt}"
SOURCE_IP="${SOURCE_IP:-192.168.8.100}"
IDENTITY_FILE="${IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

PROTOCOLS="${PROTOCOLS:-ssh ssh3 mosh}"
WORKLOADS="${WORKLOADS:-shell vim nano}"

TRIALS="${TRIALS:-3}"
ITERATIONS="${ITERATIONS:-100}"
WARMUP_ROUNDS="${WARMUP_ROUNDS:-10}"
TIMEOUT="${TIMEOUT:-30}"
SEED="${SEED:-42}"
PROBE_CHARS="${PROBE_CHARS:-QZ}"
PROBE_SEARCH_WINDOW="${PROBE_SEARCH_WINDOW:-1024}"
OPEN_SESSION_RETRIES="${OPEN_SESSION_RETRIES:-3}"
OPEN_RETRY_BACKOFF_MS="${OPEN_RETRY_BACKOFF_MS:-1000}"

OUTPUT_DIR="${OUTPUT_DIR:-w3_results}"
PROMPT="${PROMPT:-__W3_PROMPT__# }"

TMUX_SETUP_SCRIPT="${TMUX_SETUP_SCRIPT:-~/w3_tmux_setup.sh}"
TMUX_SESSION="${TMUX_SESSION:-w3bench}"
TMUX_PANE="${TMUX_PANE:-0.0}"
TMUX_READY_MARKER="${TMUX_READY_MARKER:-__W3_PANE0_READY__}"
TMUX_READY_MARKER_FALLBACK="${TMUX_READY_MARKER_FALLBACK:-__W3_5PANE_PANE0_READY__}"
TMUX_READY_TIMEOUT="${TMUX_READY_TIMEOUT:-60}"
TMUX_READY_POLL_INTERVAL="${TMUX_READY_POLL_INTERVAL:-0.5}"
SETUP_TIMEOUT_SEC="${SETUP_TIMEOUT_SEC:-25}"
REMOTE_SETUP_LOG="${REMOTE_SETUP_LOG:-/tmp/w3_tmux_setup_${TMUX_SESSION}.log}"
ATTACH_BOOT_MARKER="${ATTACH_BOOT_MARKER:-__W3_ATTACH_PANE0_READY__}"

SSH3_ATTACH_MODE="${SSH3_ATTACH_MODE:-auto}"

MOSH_PREDICT="${MOSH_PREDICT:-always}"
SSH3_PATH="${SSH3_PATH:-/ssh3-term}"
SSH3_INSECURE="${SSH3_INSECURE:-true}"
BATCH_MODE="${BATCH_MODE:-false}"
STRICT_HOST_KEY="${STRICT_HOST_KEY:-false}"
SHUFFLE_PAIRS="${SHUFFLE_PAIRS:-false}"
REOPEN_ON_FAILURE="${REOPEN_ON_FAILURE:-true}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

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

is_true() {
  case "${1:-false}" in
    true|TRUE|1|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

sanitize_token() {
  local value="$1"
  value="${value//[^A-Za-z0-9_.-]/_}"
  value="${value//./_}"
  printf '%s' "${value:-unknown}"
}

octal_escape() {
  local value="$1"
  local out="" escaped="" i
  LC_ALL=C
  for ((i = 0; i < ${#value}; i++)); do
    printf -v escaped '\\%03o' "'${value:i:1}"
    out+="$escaped"
  done
  printf '%s' "$out"
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

TMUX_SETUP_SCRIPT_RESOLVED=""
ATTACH_SCRIPT=""
ATTACH_CMD=""
ATTACH_AFTER_LOGIN_PROTOCOLS=""
SSH3_ATTACH_ENABLED=0
SSH_CTL=()

resolve_tmux_setup_script() {
  local requested_q
  requested_q="$(printf '%q' "$TMUX_SETUP_SCRIPT")"

  local resolve_cmd
  resolve_cmd="for p in ${requested_q} \"\$HOME/w3_tmux_setup.sh\" \"w3_tmux_setup.sh\" \"remote/w3_tmux_setup.sh\"; do [ -f \"\$p\" ] && { printf '%s' \"\$p\"; exit 0; }; done; exit 1"

  TMUX_SETUP_SCRIPT_RESOLVED="$("${SSH_CTL[@]}" "$resolve_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ -z "$TMUX_SETUP_SCRIPT_RESOLVED" ]]; then
    echo "ERROR: cannot find w3_tmux_setup.sh on ${HOST}." >&2
    echo "Checked: $TMUX_SETUP_SCRIPT, ~/w3_tmux_setup.sh, w3_tmux_setup.sh, remote/w3_tmux_setup.sh" >&2
    return 1
  fi
}

setup_remote_tmux() {
  local setup_q log_q
  setup_q="$(printf '%q' "$TMUX_SETUP_SCRIPT_RESOLVED")"
  log_q="$(printf '%q' "$REMOTE_SETUP_LOG")"

  echo "[${HOST}] setup: run ${TMUX_SETUP_SCRIPT_RESOLVED} (session=${TMUX_SESSION})"

  # The Pi-side script ends with `tmux attach`. Running it under nohup lets the
  # session and background panes be created even though this launcher has no TTY.
  local launch_cmd started
  launch_cmd="set -e; export TERM=xterm; chmod +x ${setup_q}; \
nohup env TERM=xterm bash ${setup_q} > ${log_q} 2>&1 < /dev/null & \
echo __W3_SETUP_STARTED__"

  if command -v timeout >/dev/null 2>&1; then
    started="$(timeout "${SETUP_TIMEOUT_SEC}s" "${SSH_CTL[@]}" "$launch_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  else
    started="$("${SSH_CTL[@]}" "$launch_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  fi

  if [[ "$started" != "__W3_SETUP_STARTED__" ]]; then
    echo "[${HOST}] setup: WARN launcher did not confirm start; continue waiting marker..." >&2
  fi
}

wait_pane_ready() {
  echo "[${HOST}] setup: waiting ${TMUX_SESSION}:${TMUX_PANE} marker ${TMUX_READY_MARKER}"
  local start_ts now target_q marker_q fallback_q
  start_ts="$(date +%s)"
  target_q="$(printf '%q' "${TMUX_SESSION}:${TMUX_PANE}")"
  marker_q="$(printf '%q' "$TMUX_READY_MARKER")"
  fallback_q="$(printf '%q' "$TMUX_READY_MARKER_FALLBACK")"

  while true; do
    if "${SSH_CTL[@]}" "tmux capture-pane -p -t ${target_q} | tail -n 200 | (grep -F ${marker_q} >/dev/null || grep -F ${fallback_q} >/dev/null)" >/dev/null 2>&1; then
      echo "[${HOST}] setup: pane ready"
      return 0
    fi
    now="$(date +%s)"
    if (( now - start_ts >= TMUX_READY_TIMEOUT )); then
      echo "ERROR: timeout waiting for pane ready marker '${TMUX_READY_MARKER}' on ${HOST}" >&2
      echo "[${HOST}] setup: remote log tail (${REMOTE_SETUP_LOG}):" >&2
      "${SSH_CTL[@]}" "tail -n 120 ${REMOTE_SETUP_LOG} 2>/dev/null || true" >&2 || true
      return 1
    fi
    sleep "$TMUX_READY_POLL_INTERVAL"
  done
}

build_attach_cmd() {
  local boot_marker_escaped
  boot_marker_escaped="$(octal_escape "$ATTACH_BOOT_MARKER")"
  ATTACH_SCRIPT="set -e; \
tmux has-session -t ${TMUX_SESSION} >/dev/null 2>&1; \
tmux respawn-pane -k -t ${TMUX_SESSION}:${TMUX_PANE} \"bash -lc \\\"stty echo -echoctl 2>/dev/null || true; printf '${boot_marker_escaped}\\\\r\\\\n'; exec bash --noprofile --norc\\\"\" >/dev/null 2>&1; \
tmux select-layout -t ${TMUX_SESSION}:0 tiled >/dev/null 2>&1 || true; \
tmux select-pane -t ${TMUX_SESSION}:${TMUX_PANE} >/dev/null 2>&1; \
exec tmux attach -t ${TMUX_SESSION}"
  ATTACH_CMD="bash -lc $(printf '%q' "$ATTACH_SCRIPT")"
}

probe_ssh3_attach_support() {
  if ! has_proto ssh3; then
    SSH3_ATTACH_ENABLED=0
    return
  fi

  case "$SSH3_ATTACH_MODE" in
    never)
      SSH3_ATTACH_ENABLED=0
      echo "[${HOST}] setup: ssh3 attach mode: never"
      return
      ;;
    force)
      SSH3_ATTACH_ENABLED=1
      echo "[${HOST}] setup: ssh3 attach mode: force"
      return
      ;;
    auto)
      ;;
    *)
      echo "WARN: unknown SSH3_ATTACH_MODE='${SSH3_ATTACH_MODE}', fallback to auto" >&2
      ;;
  esac

  if [[ -z "$REAL_SSH3" ]]; then
    SSH3_ATTACH_ENABLED=0
    return
  fi

  local marker="__W3_SSH3_CMD_PROBE__"
  local target="${USER_NAME}@${HOST}${SSH3_PATH}"
  local -a cmd
  cmd=("$REAL_SSH3")
  if [[ -n "$IDENTITY_FILE" ]]; then
    cmd+=( -privkey "$IDENTITY_FILE" )
  fi
  if is_true "$SSH3_INSECURE"; then
    cmd+=( -insecure )
  fi
  cmd+=( "$target" "printf '${marker}'")

  local probe_out=""
  if command -v timeout >/dev/null 2>&1; then
    probe_out="$(timeout 10s "${cmd[@]}" 2>/dev/null | tr -d '\r' || true)"
  else
    probe_out="$("${cmd[@]}" 2>/dev/null | tr -d '\r' || true)"
  fi

  if [[ "$probe_out" == *"${marker}"* ]]; then
    SSH3_ATTACH_ENABLED=1
    echo "[${HOST}] setup: ssh3 supports remote command, will attach pane 0 for ssh3"
  else
    SSH3_ATTACH_ENABLED=0
    echo "[${HOST}] setup: ssh3 remote-command probe failed, ssh3 will run without forced tmux attach" >&2
  fi
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

args=("$@")
argc=${#args[@]}
if (( argc == 0 )); then
  exec "$REAL_SSH"
fi

host_idx=-1
i=0
while (( i < argc )); do
  a="${args[$i]}"
  if [[ "$a" == "--" ]]; then
    ((i+=1))
    break
  fi
  if [[ "$a" == -* ]]; then
    case "$a" in
      -b|-c|-D|-E|-e|-F|-I|-i|-J|-L|-l|-m|-O|-o|-p|-Q|-R|-S|-W|-w)
        ((i+=2))
        continue
        ;;
      *)
        ((i+=1))
        continue
        ;;
    esac
  fi
  host_idx=$i
  break
done

if (( host_idx < 0 )); then
  exec "$REAL_SSH" "${args[@]}"
fi

if (( host_idx < argc - 1 )); then
  exec "$REAL_SSH" "${args[@]}"
fi

host="${args[$host_idx]}"
prefix=("${args[@]:0:host_idx}")
exec "$REAL_SSH" "${prefix[@]}" "$host" "$ATTACH_CMD"
SSH_WRAPPER

cat >"${WRAP_DIR}/mosh" <<'MOSH_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
REAL_MOSH="${W3_REAL_MOSH:?W3_REAL_MOSH is not set}"

exec "$REAL_MOSH" "$@"
MOSH_WRAPPER

cat >"${WRAP_DIR}/ssh3" <<'SSH3_WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
REAL_SSH3="${W3_REAL_SSH3:?W3_REAL_SSH3 is not set}"
ATTACH_CMD="${W3_ATTACH_CMD:?W3_ATTACH_CMD is not set}"
ATTACH_ENABLED="${W3_SSH3_ATTACH_ENABLED:-0}"

args=("$@")
if [[ "$ATTACH_ENABLED" == "1" ]]; then
  exec "$REAL_SSH3" "${args[@]}" "$ATTACH_CMD"
fi

exec "$REAL_SSH3" "${args[@]}"
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
  ATTACH_AFTER_LOGIN_PROTOCOLS=""
  SSH3_ATTACH_ENABLED=0
  local host_protocols="$PROTOCOLS"

  echo ""
  echo "=== W3 5-pane tmux benchmark: ${USER_NAME}@${HOST} ==="
  echo "[${HOST}] protocols requested: ${PROTOCOLS}"
  echo "[${HOST}] workloads: ${WORKLOADS}"

  resolve_tmux_setup_script || return 1
  setup_remote_tmux || return 1
  wait_pane_ready || return 1
  build_attach_cmd
  probe_ssh3_attach_support

  if [[ " ${host_protocols} " == *" mosh "* ]]; then
    ATTACH_AFTER_LOGIN_PROTOCOLS="${ATTACH_AFTER_LOGIN_PROTOCOLS} mosh"
  fi
  if [[ " ${host_protocols} " == *" ssh3 "* && "$SSH3_ATTACH_ENABLED" != "1" ]]; then
    echo "[${HOST}] setup: ssh3 remote-command attach unavailable; will attach tmux after ssh3 login"
    ATTACH_AFTER_LOGIN_PROTOCOLS="${ATTACH_AFTER_LOGIN_PROTOCOLS} ssh3"
  fi
  if [[ -z "$host_protocols" ]]; then
    echo "ERROR: no protocols left for ${HOST} after attach checks." >&2
    return 1
  fi

  export W3_ATTACH_SCRIPT="$ATTACH_SCRIPT"
  export W3_ATTACH_CMD="$ATTACH_CMD"
  export W3_ATTACH_BOOT_MARKER="$ATTACH_BOOT_MARKER"
  export W3_ATTACH_AFTER_LOGIN_PROTOCOLS="$(printf '%s' "$ATTACH_AFTER_LOGIN_PROTOCOLS" | xargs || true)"
  export W3_SSH3_ATTACH_ENABLED="$SSH3_ATTACH_ENABLED"

  local host_output_dir="$OUTPUT_DIR"
  if (( HOST_COUNT > 1 )); then
    host_output_dir="${OUTPUT_DIR}/$(sanitize_token "$HOST")"
  fi
  mkdir -p "$host_output_dir"

  echo "[${HOST}] setup script resolved: ${TMUX_SETUP_SCRIPT_RESOLVED}"
  echo "[${HOST}] attach command: ${ATTACH_CMD}"
  echo "[${HOST}] protocols effective: ${host_protocols}"
  echo "[${HOST}] attach after login: ${W3_ATTACH_AFTER_LOGIN_PROTOCOLS:-none}"
  echo "[${HOST}] output dir: ${host_output_dir}"
  echo "[${HOST}] run: executing benchmark..."

  local -a cmd
  cmd=(
    "$PYTHON_BIN" w3_5pane_benchmark.py
      --host "$HOST"
      --user "$USER_NAME"
      --source-ip "$SOURCE_IP"
      --identity-file "$IDENTITY_FILE"
      --protocols $host_protocols
      --workloads $WORKLOADS
      --trials "$TRIALS"
      --iterations "$ITERATIONS"
      --warmup-rounds "$WARMUP_ROUNDS"
      --timeout "$TIMEOUT"
      --open-session-retries "$OPEN_SESSION_RETRIES"
      --open-retry-backoff-ms "$OPEN_RETRY_BACKOFF_MS"
      --seed "$SEED"
      --probe-chars "$PROBE_CHARS"
      --probe-search-window "$PROBE_SEARCH_WINDOW"
      --output-dir "$host_output_dir"
      --prompt "$PROMPT"
      --tmux-session "$TMUX_SESSION"
      --tmux-pane "$TMUX_PANE"
      --ssh3-path "$SSH3_PATH"
      --mosh-predict "$MOSH_PREDICT"
  )

  is_true "$SSH3_INSECURE"     && cmd+=(--ssh3-insecure)
  is_true "$BATCH_MODE"        && cmd+=(--batch-mode)
  is_true "$STRICT_HOST_KEY"   && cmd+=(--strict-host-key-checking)
  is_true "$SHUFFLE_PAIRS"     && cmd+=(--shuffle-pairs)
  is_true "$REOPEN_ON_FAILURE" && cmd+=(--reopen-on-failure)

  printf '[%s] command: ' "$HOST"
  printf '%q ' "${cmd[@]}"
  printf '\n'

  local run_log="${host_output_dir}/w3_runner_$(date +%Y%m%d_%H%M%S).log"
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
