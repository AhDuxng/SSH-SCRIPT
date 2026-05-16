#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HOST="${HOST:-192.168.8.102}"
HOSTS="${HOSTS:-$HOST}"
USER_NAME="${USER_NAME:-trungnt}"
SOURCE_IP="${SOURCE_IP:-192.168.8.100}"
IDENTITY_FILE="${IDENTITY_FILE:-$HOME/.ssh/id_rsa}"

PROTOCOLS="${PROTOCOLS:-ssh ssh3}"
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

BACKGROUND_CHANNELS="${BACKGROUND_CHANNELS:-4}"
BACKGROUND_WARMUP_SEC="${BACKGROUND_WARMUP_SEC:-1.0}"
BACKGROUND_READ_CHUNK="${BACKGROUND_READ_CHUNK:-4096}"

OUTPUT_DIR="${OUTPUT_DIR:-w3_protocol_multiplex_results}"
PROMPT="${PROMPT:-__W3_PROMPT__# }"

SSH3_PATH="${SSH3_PATH:-/ssh3-term}"
SSH3_INSECURE="${SSH3_INSECURE:-true}"
BATCH_MODE="${BATCH_MODE:-false}"
STRICT_HOST_KEY="${STRICT_HOST_KEY:-false}"
SHUFFLE_PAIRS="${SHUFFLE_PAIRS:-false}"
REOPEN_ON_FAILURE="${REOPEN_ON_FAILURE:-true}"
MOSH_PREDICT="${MOSH_PREDICT:-never}"

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

export W3_REAL_SSH="$REAL_SSH"
export W3_REAL_MOSH="${REAL_MOSH:-$REAL_SSH}"
export W3_REAL_SSH3="${REAL_SSH3:-$REAL_SSH}"

read -r -a HOST_ARRAY <<< "$HOSTS"
HOST_COUNT="${#HOST_ARRAY[@]}"
FAILED_HOSTS=()

if (( HOST_COUNT == 0 )); then
  echo "ERROR: HOSTS is empty." >&2
  exit 1
fi

run_for_host() {
  local host="$1"
  local host_output_dir="$OUTPUT_DIR"
  if (( HOST_COUNT > 1 )); then
    host_output_dir="${OUTPUT_DIR}/$(sanitize_token "$host")"
  fi
  mkdir -p "$host_output_dir"

  echo ""
  echo "=== W3 protocol-multiplex benchmark: ${USER_NAME}@${host} ==="
  echo "[${host}] protocols: ${PROTOCOLS}"
  echo "[${host}] workloads: ${WORKLOADS}"
  echo "[${host}] background channels: ${BACKGROUND_CHANNELS}"
  echo "[${host}] ssh ControlMaster: enabled for protocol=ssh"
  echo "[${host}] output dir: ${host_output_dir}"

  local -a cmd
  cmd=(
    "$PYTHON_BIN" w3_5pane_benchmark.py
      --host "$host"
      --user "$USER_NAME"
      --source-ip "$SOURCE_IP"
      --identity-file "$IDENTITY_FILE"
      --protocols $PROTOCOLS
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
      --ssh3-path "$SSH3_PATH"
      --mosh-predict "$MOSH_PREDICT"
      --background-channels "$BACKGROUND_CHANNELS"
      --background-warmup-sec "$BACKGROUND_WARMUP_SEC"
      --background-read-chunk "$BACKGROUND_READ_CHUNK"
      --ssh-control-master
  )

  is_true "$SSH3_INSECURE"     && cmd+=(--ssh3-insecure)
  is_true "$BATCH_MODE"        && cmd+=(--batch-mode)
  is_true "$STRICT_HOST_KEY"   && cmd+=(--strict-host-key-checking)
  is_true "$SHUFFLE_PAIRS"     && cmd+=(--shuffle-pairs)
  is_true "$REOPEN_ON_FAILURE" && cmd+=(--reopen-on-failure)

  printf '[%s] command: ' "$host"
  printf '%q ' "${cmd[@]}"
  printf '\n'

  local run_log="${host_output_dir}/w3_protocol_multiplex_runner_$(date +%Y%m%d_%H%M%S).log"
  echo "[${host}] log file: ${run_log}"

  (
    "${cmd[@]}"
  ) 2>&1 | tee "$run_log" &
  local bench_pid=$!

  while kill -0 "$bench_pid" >/dev/null 2>&1; do
    echo "[${host}] benchmark is still running... $(date +%H:%M:%S)"
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
