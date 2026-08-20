#!/usr/bin/env bash

# Các hàm dùng chung để chuẩn bị và thu log congestion cho W1-W4.

STREAM_MUX_CC_ACTIVE=0
STREAM_MUX_CC_SSH_ARGS=()
STREAM_MUX_CC_SCP_ARGS=()
STREAM_MUX_CC_BASELINE_FILE=""

# Tạo tham số SSH/SCP từ cấu hình workload hiện tại.
stream_mux_cc_build_remote_args() {
  STREAM_MUX_CC_SSH_ARGS=()
  STREAM_MUX_CC_SCP_ARGS=()
  if [[ -n "${SERVER_PORT:-}" ]]; then
    STREAM_MUX_CC_SSH_ARGS+=(-p "$SERVER_PORT")
    STREAM_MUX_CC_SCP_ARGS+=(-P "$SERVER_PORT")
  fi
  local identity_path="${SSH_IDENTITY_FILE:-}"
  identity_path="${identity_path/#\~/$HOME}"
  if [[ -n "$identity_path" ]]; then
    STREAM_MUX_CC_SSH_ARGS+=(-i "$identity_path")
    STREAM_MUX_CC_SCP_ARGS+=(-i "$identity_path")
  fi
  if [[ "${SSH_STRICT_HOST_KEY_CHECKING:-0}" != "1" ]]; then
    STREAM_MUX_CC_SSH_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
    STREAM_MUX_CC_SCP_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  fi
  if [[ "${SSH_BATCH_MODE:-1}" == "1" ]]; then
    STREAM_MUX_CC_SSH_ARGS+=(-o BatchMode=yes)
    STREAM_MUX_CC_SCP_ARGS+=(-o BatchMode=yes)
  fi
}

# Ghi cấu hình kernel, qdisc và interface ở client.
stream_mux_cc_capture_local_context() {
  local output_path="$1"
  {
    date --iso-8601=ns 2>&1 || date
    uname -a
    sysctl net.ipv4.tcp_congestion_control 2>&1 || true
    sysctl net.ipv4.tcp_available_congestion_control 2>&1 || true
    sysctl net.core.default_qdisc 2>&1 || true
    sysctl net.core.rmem_max net.core.wmem_max 2>&1 || true
    tc -s qdisc show 2>&1 || true
    ip -s link 2>&1 || true
  } > "$output_path"
}

# Ghi cùng bộ thông tin network stack ở server.
stream_mux_cc_capture_server_context() {
  local output_path="$1"
  ssh "${STREAM_MUX_CC_SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
    "date --iso-8601=ns 2>&1 || date; uname -a; \
     sysctl net.ipv4.tcp_congestion_control 2>&1 || true; \
     sysctl net.ipv4.tcp_available_congestion_control 2>&1 || true; \
     sysctl net.core.default_qdisc 2>&1 || true; \
     sysctl net.core.rmem_max net.core.wmem_max 2>&1 || true; \
     tc -s qdisc show 2>&1 || true; ip -s link 2>&1 || true" \
    > "$output_path"
}

# Bật collector, triển khai sampler SSH và chụp trạng thái trước lượt chạy.
stream_mux_cc_prepare() {
  local result_dir="$1"
  if [[ "${CONGESTION_LOG_ENABLED:-1}" != "1" ]]; then
    export CONGESTION_LOG_DIR=""
    export SERVER_CONGESTION_LOG_DIR=""
    export REMOTE_CONGESTION_SAMPLER=""
    return
  fi
  STREAM_MUX_CC_ACTIVE=1
  export RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)}"
  export CONGESTION_LOG_DIR="$result_dir/congestion/client"
  export SERVER_CONGESTION_LOG_DIR="${REMOTE_CONGESTION_DIR:-/tmp/stream_mux_congestion}"
  export REMOTE_CONGESTION_SAMPLER="$SERVER_CONGESTION_LOG_DIR/remote_tcp_sampler.py"
  mkdir -p "$CONGESTION_LOG_DIR" "$result_dir/congestion/server"
  stream_mux_cc_build_remote_args

  if [[ ",${PROTOCOLS}," == *,ssh,* ]]; then
    command -v ss >/dev/null 2>&1 || {
      echo "Client thiếu ss; hãy cài iproute2" >&2
      return 2
    }
    ssh "${STREAM_MUX_CC_SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
      "command -v python3 >/dev/null && command -v ss >/dev/null" || {
        echo "Server thiếu python3 hoặc ss/iproute2" >&2
        return 2
      }
    ssh "${STREAM_MUX_CC_SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
      "mkdir -p $(printf '%q' "$SERVER_CONGESTION_LOG_DIR")"
    scp "${STREAM_MUX_CC_SCP_ARGS[@]}" \
      "$REPO_DIR/stream_mux/scripts/remote_tcp_sampler.py" \
      "${SERVER_USER}@${SERVER_HOST}:${REMOTE_CONGESTION_SAMPLER}"
  fi

  if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
    STREAM_MUX_CC_BASELINE_FILE="$(mktemp -t stream-mux-cc-before.XXXXXX)"
    ssh "${STREAM_MUX_CC_SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
      "find $(printf '%q' "${SSH3_SERVER_CONGESTION_DIR:-/tmp/ssh3-server-congestion}") \
       -maxdepth 1 -type f -name '*.ssh3_server_quic.jsonl' -printf '%f\\n' 2>/dev/null | sort" \
      > "$STREAM_MUX_CC_BASELINE_FILE"
  fi

  stream_mux_cc_capture_local_context \
    "$result_dir/congestion/client/network_stack_before.txt"
  stream_mux_cc_capture_server_context \
    "$result_dir/congestion/server/network_stack_before.txt"
}

# Lấy đúng log của lượt chạy, chụp trạng thái sau và tạo summary.csv.
stream_mux_cc_finish() {
  local result_dir="$1"
  [[ "$STREAM_MUX_CC_ACTIVE" == "1" ]] || return 0
  local server_local="$result_dir/congestion/server"
  if [[ ",${PROTOCOLS}," == *,ssh,* ]]; then
    scp "${STREAM_MUX_CC_SCP_ARGS[@]}" \
      "${SERVER_USER}@${SERVER_HOST}:${SERVER_CONGESTION_LOG_DIR}/${RUN_ID}.*.ssh_server_tcp.jsonl" \
      "$server_local/" || echo "Không lấy đủ TCP congestion log phía server" >&2
  fi
  if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
    local after_file new_file remote_quic_dir
    after_file="$(mktemp -t stream-mux-cc-after.XXXXXX)"
    new_file="$(mktemp -t stream-mux-cc-new.XXXXXX)"
    remote_quic_dir="${SSH3_SERVER_CONGESTION_DIR:-/tmp/ssh3-server-congestion}"
    ssh "${STREAM_MUX_CC_SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
      "find $(printf '%q' "$remote_quic_dir") -maxdepth 1 -type f \
       -name '*.ssh3_server_quic.jsonl' -printf '%f\\n' 2>/dev/null | sort" \
      > "$after_file"
    comm -13 "$STREAM_MUX_CC_BASELINE_FILE" "$after_file" > "$new_file"
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      scp "${STREAM_MUX_CC_SCP_ARGS[@]}" \
        "${SERVER_USER}@${SERVER_HOST}:${remote_quic_dir}/${name}" \
        "$server_local/"
    done < "$new_file"
    rm -f "$after_file" "$new_file" "$STREAM_MUX_CC_BASELINE_FILE"
    STREAM_MUX_CC_BASELINE_FILE=""
  fi
  stream_mux_cc_capture_local_context \
    "$result_dir/congestion/client/network_stack_after.txt"
  stream_mux_cc_capture_server_context \
    "$result_dir/congestion/server/network_stack_after.txt"
  "${PYTHON_BIN:-python3}" "$REPO_DIR/stream_mux/scripts/analyze_congestion.py" \
    "$result_dir"
}
