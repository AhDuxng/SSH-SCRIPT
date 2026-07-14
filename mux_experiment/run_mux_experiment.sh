#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
CONFIG="${1:-mux_config.env}"
source "$CONFIG"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${RESULT_DIR}/${RUN_ID}"
LOG_OUT_DIR="${LOG_DIR}/${RUN_ID}"
mkdir -p "$OUT_DIR" "$LOG_OUT_DIR"

SSH_PORT_ARGS=()
SCP_PORT_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_PORT_ARGS=(-p "$SERVER_PORT")
  SCP_PORT_ARGS=(-P "$SERVER_PORT")
fi
REMOTE="${SERVER_USER}@${SERVER_HOST}"

need_quic=0
case ",$PROTOCOLS," in
  *,quic,*) need_quic=1 ;;
esac

ensure_local_aioquic() {
  if [[ "$need_quic" != "1" ]]; then
    return
  fi
  if "$LOCAL_PYTHON_BIN" -c 'import aioquic' >/dev/null 2>&1; then
    return
  fi
  if [[ "${AUTO_INSTALL_AIOQUIC:-1}" != "1" ]]; then
    echo "Missing local aioquic. Install: $LOCAL_PYTHON_BIN -m pip install aioquic" >&2
    exit 2
  fi
  echo "[SETUP] local aioquic missing; creating/updating local venv"
  "${LOCAL_BOOTSTRAP_PYTHON:-python3}" -m venv .venv
  "$LOCAL_PYTHON_BIN" -m pip install --upgrade pip
  "$LOCAL_PYTHON_BIN" -m pip install aioquic
}

ensure_remote_aioquic() {
  if [[ "$need_quic" != "1" ]]; then
    return
  fi
  if ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "'$REMOTE_PYTHON_BIN' -c 'import aioquic'" >/dev/null 2>&1; then
    return
  fi
  if [[ "${AUTO_INSTALL_AIOQUIC:-1}" != "1" ]]; then
    echo "Missing remote aioquic. Install on Pi: $REMOTE_PYTHON_BIN -m pip install aioquic" >&2
    exit 2
  fi
  echo "[SETUP] remote aioquic missing; creating/updating remote venv on $REMOTE"
  ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" \
    "cd '$REMOTE_DIR' && '${REMOTE_BOOTSTRAP_PYTHON:-python3}' -m venv .venv && '$REMOTE_PYTHON_BIN' -m pip install --upgrade pip && '$REMOTE_PYTHON_BIN' -m pip install aioquic"
}

ensure_local_aioquic

ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "mkdir -p '$REMOTE_DIR/certs' '$REMOTE_DIR/logs'"
scp "${SCP_PORT_ARGS[@]}" mux_bench.py analyze_mux.py "$REMOTE:$REMOTE_DIR/"

ensure_remote_aioquic

if [[ "$need_quic" == "1" ]]; then
  ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" \
    "cd '$REMOTE_DIR' && if [ ! -f certs/mux_cert.pem ] || [ ! -f certs/mux_key.pem ]; then openssl req -x509 -newkey rsa:2048 -nodes -keyout certs/mux_key.pem -out certs/mux_cert.pem -subj /CN=muxbench -days 7 >/dev/null 2>&1; fi"
fi

start_capture() {
  local protocol="$1"
  CAPTURE_PID=""
  if [[ "${CAPTURE:-0}" != "1" ]]; then
    return
  fi
  local pcap="$OUT_DIR/${protocol}.pcap"
  local filter
  if [[ "$protocol" == "tcp" ]]; then
    filter="tcp and host ${SERVER_HOST} and port ${MUX_PORT}"
  else
    filter="udp and host ${SERVER_HOST} and port ${MUX_PORT}"
  fi
  echo "[CAPTURE] tcpdump -i ${CAPTURE_IFACE} -w $pcap '$filter'"
  sudo tcpdump -i "$CAPTURE_IFACE" -w "$pcap" "$filter" >/dev/null 2>&1 &
  CAPTURE_PID="$!"
  sleep 1
}

stop_capture() {
  if [[ -n "${CAPTURE_PID:-}" ]]; then
    sudo kill "$CAPTURE_PID" >/dev/null 2>&1 || true
    wait "$CAPTURE_PID" 2>/dev/null || true
  fi
}

csv_to_words() {
  echo "$1" | tr ',' ' '
}

start_remote_server() {
  local protocol="$1"
  local remote_log="$2"
  echo "[SERVER] start $protocol"
  ssh -n "${SSH_PORT_ARGS[@]}" "$REMOTE" "
cd '$REMOTE_DIR' || exit 1
if [ -f 'server_${protocol}.pid' ]; then
  kill \$(cat 'server_${protocol}.pid') >/dev/null 2>&1 || true
  rm -f 'server_${protocol}.pid'
fi
nohup '$REMOTE_PYTHON_BIN' mux_bench.py server --protocol '$protocol' --host 0.0.0.0 --port '$MUX_PORT' --cert certs/mux_cert.pem --key certs/mux_key.pem </dev/null > '$remote_log' 2>&1 &
echo \$! > 'server_${protocol}.pid'
"
  echo "[SERVER] start command returned for $protocol"
  sleep 2
  if ! ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "cd '$REMOTE_DIR' && test -s 'server_${protocol}.pid' && kill -0 \$(cat 'server_${protocol}.pid') >/dev/null 2>&1"; then
    echo "[SERVER] $protocol failed to stay running. Remote log:" >&2
    ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "tail -80 '$remote_log'" >&2 || true
    exit 1
  fi
  echo "[SERVER] $protocol is running"
}

for protocol in $(csv_to_words "$PROTOCOLS"); do
  remote_log="$REMOTE_DIR/logs/server_${protocol}_${RUN_ID}.log"
  start_remote_server "$protocol" "$remote_log"

  start_capture "$protocol"
  keylog_arg=()
  if [[ "$protocol" == "quic" ]]; then
    keylog_arg=(--keylog "$OUT_DIR/$QUIC_KEYLOG")
  fi

  echo "[CLIENT] run $protocol"
  "$LOCAL_PYTHON_BIN" mux_bench.py client \
    --protocols "$protocol" \
    --host "$SERVER_HOST" \
    --port "$MUX_PORT" \
    --profiles "$PROFILES" \
    --runs "$RUNS" \
    --interval "$INTERVAL" \
    --timeout "$TIMEOUT" \
    --warmup-seconds "$WARMUP_SECONDS" \
    --out-dir "$OUT_DIR" \
    "${keylog_arg[@]}" \
    $([[ "${LIVE_PROGRESS:-1}" == "1" ]] && printf %s --live)

  stop_capture
  ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "cd '$REMOTE_DIR' && if [ -f 'server_${protocol}.pid' ]; then kill \$(cat 'server_${protocol}.pid') >/dev/null 2>&1 || true; rm -f 'server_${protocol}.pid'; fi"
  scp "${SCP_PORT_ARGS[@]}" "$REMOTE:$remote_log" "$LOG_OUT_DIR/server_${protocol}.log" >/dev/null 2>&1 || true
  sleep 1
 done

"$LOCAL_PYTHON_BIN" analyze_mux.py "$OUT_DIR/mux_samples.csv" "$OUT_DIR/mux_summary.csv"

echo "Done. Results: $OUT_DIR"
echo "Stream map: $OUT_DIR/mux_stream_map.csv"
echo "Summary   : $OUT_DIR/mux_summary.csv"
if [[ "$need_quic" == "1" ]]; then
  echo "QUIC keylog for Wireshark: $OUT_DIR/$QUIC_KEYLOG"
fi
