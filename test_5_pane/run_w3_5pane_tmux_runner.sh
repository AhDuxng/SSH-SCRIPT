#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# =====================
# User-configurable vars
# =====================
HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="${HOME}/.ssh/id_rsa"

# Keep python script unchanged; this runner only orchestrates tmux attach flow.
PROTOCOLS="ssh mosh"
WORKLOADS="interactive_shell vim nano"

TRIALS=3
ITERATIONS=100
WARMUP_ROUNDS=10
TIMEOUT=20
SEED=42

OUTPUT_DIR="w3_results"
PROMPT="__W3_PROMPT__# "

TMUX_SETUP_SCRIPT="remote/w3_tmux_setup.sh"
TMUX_SESSION="w3bench5"
TMUX_PANE="0.0"
TMUX_READY_MARKER="__W3_5PANE_PANE0_READY__"
TMUX_READY_TIMEOUT=60
TMUX_READY_POLL_INTERVAL=0.5

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

REAL_SSH="$(command -v ssh)"
REAL_MOSH="$(command -v mosh || true)"
if [[ -z "$REAL_SSH" ]]; then
  echo "ERROR: ssh not found in PATH." >&2
  exit 1
fi
if [[ -z "$REAL_MOSH" ]]; then
  echo "WARN: mosh not found, switching PROTOCOLS to ssh only." >&2
  PROTOCOLS="ssh"
fi

SSH_BOOT=("$REAL_SSH" -tt)
if [[ -n "$SOURCE_IP" ]]; then
  SSH_BOOT+=( -b "$SOURCE_IP" )
fi
if [[ "${STRICT_HOST_KEY}" == "true" ]]; then
  SSH_BOOT+=( -o StrictHostKeyChecking=yes )
else
  SSH_BOOT+=( -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null )
fi
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_BOOT+=( -i "$IDENTITY_FILE" )
fi
if [[ "${BATCH_MODE}" == "true" ]]; then
  SSH_BOOT+=( -o BatchMode=yes )
fi
SSH_BOOT+=("${USER_NAME}@${HOST}")

ATTACH_CMD="bash -lc 'NO_ATTACH=1 bash ${TMUX_SETUP_SCRIPT} ${TMUX_SESSION} >/dev/null 2>&1; tmux select-pane -t ${TMUX_SESSION}:${TMUX_PANE} >/dev/null 2>&1; exec tmux attach -t ${TMUX_SESSION}'"

setup_remote_tmux() {
  echo "[setup] chmod +x and run ${TMUX_SETUP_SCRIPT} on remote..."
  "${SSH_BOOT[@]}" "chmod +x ${TMUX_SETUP_SCRIPT} && NO_ATTACH=1 bash ${TMUX_SETUP_SCRIPT} ${TMUX_SESSION}" >/dev/null
}

wait_pane_ready() {
  echo "[setup] waiting pane ${TMUX_SESSION}:${TMUX_PANE} ready marker: ${TMUX_READY_MARKER}"
  local start_ts now
  start_ts="$(date +%s)"
  while true; do
    if "${SSH_BOOT[@]}" "tmux capture-pane -p -t ${TMUX_SESSION}:${TMUX_PANE} | tail -n 200 | grep -F '${TMUX_READY_MARKER}' >/dev/null" >/dev/null 2>&1; then
      echo "[setup] pane ready"
      return 0
    fi
    now="$(date +%s)"
    if (( now - start_ts >= TMUX_READY_TIMEOUT )); then
      echo "ERROR: timeout waiting for pane ready marker '${TMUX_READY_MARKER}'" >&2
      return 1
    fi
    sleep "$TMUX_READY_POLL_INTERVAL"
  done
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

# Parse ssh options to find host position.
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

# If caller already supplied a remote command, pass through untouched.
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

chmod +x "${WRAP_DIR}/ssh" "${WRAP_DIR}/mosh"

setup_remote_tmux
wait_pane_ready

export W3_REAL_SSH="$REAL_SSH"
export W3_REAL_MOSH="${REAL_MOSH:-$REAL_SSH}"
export W3_ATTACH_CMD="$ATTACH_CMD"
export PATH="${WRAP_DIR}:${PATH}"

echo "=== W3 5-pane TMUX runner (python file unchanged) ==="
echo "Attach command: ${ATTACH_CMD}"

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
