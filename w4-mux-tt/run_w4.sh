#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

CONFIG="${1:-config.env}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Thiếu $CONFIG. Tạo bằng: cp config.example.env config.env" >&2
  exit 2
fi

# Nạp config nhưng giữ biến môi trường truyền trực tiếp từ dòng lệnh.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  key="${key//[[:space:]]/}"
  if [[ "$value" == \"*\" && "$value" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  if ! declare -p "$key" >/dev/null 2>&1; then
    printf -v "$key" '%s' "$value"
    export "$key"
  fi
done < "$CONFIG"

PYTHON_COMMAND="${PYTHON_BIN:-python3}"
PAYLOAD_PATH="${PAYLOAD_DIR:-payloads}"
REMOTE_PATH="${W4_REMOTE_PAYLOAD_DIR:-/tmp/w4_mux_tt_payloads}"
RESULT_PATH="${RESULT_DIR:-artifacts/results}"
mkdir -p "$RESULT_PATH"
source "$REPO_DIR/stream_mux/scripts/run_logging.sh"
stream_mux_start_run_log "$RESULT_PATH" "$PROJECT_DIR/run_w4.sh" "$CONFIG"

# Payload is generated and deployed before any measured connection is opened.
"$PYTHON_COMMAND" tools/generate_payload.py "$PAYLOAD_PATH"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_mux_stdio.patch"
  QUIC_PATCH_PATH="$REPO_DIR/stream_mux/patches/quic_go_cubic.patch"
  QUIC_PREPARE_SCRIPT="$REPO_DIR/stream_mux/scripts/prepare_quic_cubic.sh"
  PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$QUIC_PATCH_PATH" "$QUIC_PREPARE_SCRIPT" | shasum -a 256 | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Thiếu hoặc sai SSH3 multiplex bridge: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

SSH_ARGS=()
SCP_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then SSH_ARGS+=(-p "$SERVER_PORT"); SCP_ARGS+=(-P "$SERVER_PORT"); fi
IDENTITY_PATH="${SSH_IDENTITY_FILE:-}"
IDENTITY_PATH="${IDENTITY_PATH/#\~/$HOME}"
if [[ -n "$IDENTITY_PATH" ]]; then SSH_ARGS+=(-i "$IDENTITY_PATH"); SCP_ARGS+=(-i "$IDENTITY_PATH"); fi
if [[ "${SSH_STRICT_HOST_KEY_CHECKING:-0}" != "1" ]]; then
  SSH_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  SCP_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
fi
if [[ "${SSH_BATCH_MODE:-1}" == "1" ]]; then SSH_ARGS+=(-o BatchMode=yes); SCP_ARGS+=(-o BatchMode=yes); fi

REMOTE_QUOTED="$(printf '%q' "$REMOTE_PATH")"
ssh "${SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  "mkdir -p $REMOTE_QUOTED && command -v ${VIM_BIN:-vim} >/dev/null && command -v ${NANO_BIN:-nano} >/dev/null && command -v ${TMUX_BIN:-tmux} >/dev/null && command -v od >/dev/null && command -v fold >/dev/null && command -v sha256sum >/dev/null"
scp "${SCP_ARGS[@]}" "$PAYLOAD_PATH/large_output_s0_1MiB.txt" "$PAYLOAD_PATH/SHA256SUMS" \
  "${SERVER_USER}@${SERVER_HOST}:${REMOTE_PATH}/"
ssh "${SSH_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  "cd $REMOTE_QUOTED && sha256sum -c SHA256SUMS"

PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_COMMAND" src/run_w4.py "$CONFIG"
"$PYTHON_COMMAND" tools/analyze_w4.py "$RESULT_PATH"
"$PYTHON_COMMAND" tools/verify_mux.py "$RESULT_PATH"

echo "Hoàn tất. Xem $RESULT_PATH/scenario_summary.csv"
