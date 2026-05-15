#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

PROTOCOLS="ssh ssh3 mosh"
WORKLOADS="interactive_shell vim nano"

TRIALS=3
ITERATIONS=100
WARMUP_ROUNDS=10
TIMEOUT=20
SEED=42

OUTPUT_DIR="w3_results"
PROMPT="__W3_PROMPT__# "

TMUX_SETUP_SCRIPT="~/w3_tmux_setup.sh"
TMUX_SESSION="w3bench5"
TMUX_PANE="0.0"
TMUX_READY_MARKER="__W3_5PANE_PANE0_READY__"
TMUX_READY_TIMEOUT=60
TMUX_READY_POLL_INTERVAL=0.5
SETUP_TIMEOUT_SEC=25
REMOTE_SETUP_LOG="/tmp/w3_tmux_setup_${TMUX_SESSION}.log"

SSH3_ATTACH_MODE="auto"

MOSH_PREDICT="always"
SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true
BATCH_MODE=false
STRICT_HOST_KEY=false
SHUFFLE_PAIRS=false
REOPEN_ON_FAILURE=true

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

has_proto() {
  local name="$1"
  [[ " $PROTOCOLS " == *" $name "* ]]
}

drop_proto() {
  local name="$1"
  PROTOCOLS="$(printf ' %s ' "$PROTOCOLS" | sed "s/ ${name} / /g" | xargs || true)"
}

REAL_SSH="$(command -v ssh || true)"
REAL_MOSH="$(command -v mosh || true)"
REAL_SSH3="$(command -v ssh3 || true)"

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
if [[ "${STRICT_HOST_KEY}" == "true" ]]; then
  SSH_OPTS+=( -o StrictHostKeyChecking=yes )
else
  SSH_OPTS+=( -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null )
fi
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_OPTS+=( -i "$IDENTITY_FILE" )
fi
if [[ "${BATCH_MODE}" == "true" ]]; then
  SSH_OPTS+=( -o BatchMode=yes )
fi

SSH_CTL=("$REAL_SSH" "${SSH_OPTS[@]}" "${USER_NAME}@${HOST}")

TMUX_SETUP_SCRIPT_RESOLVED=""
ATTACH_CMD=""
SSH3_ATTACH_ENABLED=0

resolve_tmux_setup_script() {
  local requested_q
  requested_q="$(printf '%q' "$TMUX_SETUP_SCRIPT")"

  local resolve_cmd
  resolve_cmd="for p in ${requested_q} \"\$HOME/w3_tmux_setup.sh\" \"w3_tmux_setup.sh\" \"remote/w3_tmux_setup.sh\"; do [ -f \"\$p\" ] && { printf '%s' \"\$p\"; exit 0; }; done; exit 1"

  TMUX_SETUP_SCRIPT_RESOLVED="$("${SSH_CTL[@]}" "$resolve_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  if [[ -z "$TMUX_SETUP_SCRIPT_RESOLVED" ]]; then
    echo "ERROR: cannot find w3_tmux_setup.sh on remote." >&2
    echo "Checked: $TMUX_SETUP_SCRIPT, ~/w3_tmux_setup.sh, w3_tmux_setup.sh, remote/w3_tmux_setup.sh" >&2
    exit 1
  fi
}

setup_remote_tmux() {
  local setup_q session_q log_q
  setup_q="$(printf '%q' "$TMUX_SETUP_SCRIPT_RESOLVED")"
  session_q="$(printf '%q' "$TMUX_SESSION")"
  log_q="$(printf '%q' "$REMOTE_SETUP_LOG")"

  echo "[setup] run ${TMUX_SETUP_SCRIPT_RESOLVED} on remote (session=${TMUX_SESSION})..."

  # Launch setup asynchronously to avoid blocking if the remote script performs
  # interactive operations. Prefer util-linux script(1) to provide a pseudo-tty.
  local launch_cmd started
  launch_cmd="set -e; export TERM=\${TERM:-xterm-256color}; chmod +x ${setup_q}; \
if command -v script >/dev/null 2>&1; then \
  nohup script -qfec \"NO_ATTACH=1 bash ${setup_q} ${session_q}\" /dev/null > ${log_q} 2>&1 < /dev/null & \
else \
  nohup env NO_ATTACH=1 bash ${setup_q} ${session_q} > ${log_q} 2>&1 < /dev/null & \
fi; echo __W3_SETUP_STARTED__"

  if command -v timeout >/dev/null 2>&1; then
    started="$(timeout "${SETUP_TIMEOUT_SEC}s" "${SSH_CTL[@]}" "$launch_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  else
    started="$("${SSH_CTL[@]}" "$launch_cmd" 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
  fi

  if [[ "$started" != "__W3_SETUP_STARTED__" ]]; then
    echo "[setup] WARN: setup launcher did not confirm start; continue waiting marker..." >&2
  fi
}

wait_pane_ready() {
  echo "[setup] waiting pane ${TMUX_SESSION}:${TMUX_PANE} ready marker: ${TMUX_READY_MARKER}"
  local start_ts now
  start_ts="$(date +%s)"
  while true; do
    if "${SSH_CTL[@]}" "tmux capture-pane -p -t ${TMUX_SESSION}:${TMUX_PANE} | tail -n 200 | grep -F '${TMUX_READY_MARKER}' >/dev/null" >/dev/null 2>&1; then
      echo "[setup] pane ready"
      return 0
    fi
    now="$(date +%s)"
    if (( now - start_ts >= TMUX_READY_TIMEOUT )); then
      echo "ERROR: timeout waiting for pane ready marker '${TMUX_READY_MARKER}'" >&2
      echo "[setup] remote log tail (${REMOTE_SETUP_LOG}):" >&2
      "${SSH_CTL[@]}" "tail -n 120 ${REMOTE_SETUP_LOG} 2>/dev/null || true" >&2 || true
      return 1
    fi
    sleep "$TMUX_READY_POLL_INTERVAL"
  done
}

probe_ssh3_attach_support() {
  if ! has_proto ssh3; then
    SSH3_ATTACH_ENABLED=0
    return
  fi

  case "$SSH3_ATTACH_MODE" in
    never)
      SSH3_ATTACH_ENABLED=0
      echo "[setup] ssh3 attach mode: never"
      return
      ;;
    force)
      SSH3_ATTACH_ENABLED=1
      echo "[setup] ssh3 attach mode: force"
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
  if [[ "${SSH3_INSECURE}" == "true" ]]; then
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
    echo "[setup] ssh3 supports remote command, will attach pane0 for ssh3."
  else
    SSH3_ATTACH_ENABLED=0
    echo "[setup] ssh3 remote-command probe failed, ssh3 will run without forced tmux attach." >&2
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
    ((i++))
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
ATTACH_CMD="${W3_ATTACH_CMD:?W3_ATTACH_CMD is not set}"

args=("$@")
for a in "${args[@]}"; do
  if [[ "$a" == "--" ]]; then
    exec "$REAL_MOSH" "${args[@]}"
  fi
done

exec "$REAL_MOSH" "${args[@]}" -- "$ATTACH_CMD"
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

resolve_tmux_setup_script
setup_remote_tmux
wait_pane_ready

ATTACH_CMD="bash -lc 'tmux select-pane -t ${TMUX_SESSION}:${TMUX_PANE} >/dev/null 2>&1; exec tmux attach -t ${TMUX_SESSION}'"
probe_ssh3_attach_support

export W3_REAL_SSH="$REAL_SSH"
export W3_REAL_MOSH="${REAL_MOSH:-$REAL_SSH}"
export W3_REAL_SSH3="${REAL_SSH3:-$REAL_SSH}"
export W3_ATTACH_CMD="$ATTACH_CMD"
export W3_SSH3_ATTACH_ENABLED="$SSH3_ATTACH_ENABLED"
export PATH="${WRAP_DIR}:${PATH}"

echo "=== W3 5-pane TMUX runner (python file unchanged) ==="
echo "[setup] setup script resolved: ${TMUX_SETUP_SCRIPT_RESOLVED}"
echo "[setup] attach command: ${ATTACH_CMD}"
echo "[setup] protocols: ${PROTOCOLS}"

echo "[run] executing benchmark..."
CMD=(
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
    --output-dir "$OUTPUT_DIR"
    --prompt "$PROMPT"
    --ssh3-path "$SSH3_PATH"
    --mosh-predict "$MOSH_PREDICT"
)

$SSH3_INSECURE       && CMD+=(--ssh3-insecure)
$BATCH_MODE          && CMD+=(--batch-mode)
$STRICT_HOST_KEY     && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS       && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE   && CMD+=(--reopen-on-failure)

printf '  %s \\\n' "${CMD[@]}"
"${CMD[@]}"

if [[ -f plot_trend.py ]]; then
  "$PYTHON_BIN" plot_trend.py --output-dir "$OUTPUT_DIR" --prefix "w3" --group-fields protocol workload
fi
