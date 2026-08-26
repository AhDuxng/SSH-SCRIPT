#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

CONFIG="${1:-config.env}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Thiếu $CONFIG. Hãy tạo bằng: cp config.example.env config.env" >&2
  exit 2
fi

# Đọc cấu hình nhưng giữ giá trị môi trường truyền từ dòng lệnh.
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
REMOTE_PATH="${W2_REMOTE_PAYLOAD_DIR:-/tmp/w2_mux_tt_payloads}"
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)}"
export RUN_ID
RESULT_PATH="${RESULT_DIR:-artifacts/results}"
mkdir -p "$RESULT_PATH"
source "$REPO_DIR/stream_mux/scripts/run_logging.sh"
stream_mux_start_run_log "$RESULT_PATH" "$PROJECT_DIR/run_w2.sh" "$CONFIG"

# Tạo lại cùng một bộ payload xác định trước ở ngoài khoảng đo.
"$PYTHON_COMMAND" tools/generate_payloads.py "$PAYLOAD_PATH"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_mux_stdio.patch"
  JWT_PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_jwt_clock_skew.patch"
  QUIC_PATCH_PATH="$REPO_DIR/stream_mux/patches/quic_go_cubic.patch"
  QUIC_PREPARE_SCRIPT="$REPO_DIR/stream_mux/scripts/prepare_quic_cubic.sh"
  PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$JWT_PATCH_PATH" "$QUIC_PATCH_PATH" "$QUIC_PREPARE_SCRIPT" | shasum -a 256 | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Thiếu hoặc sai phiên bản SSH3 mux client: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

SSH_DEPLOY_ARGS=()
SCP_DEPLOY_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_DEPLOY_ARGS+=(-p "$SERVER_PORT")
  SCP_DEPLOY_ARGS+=(-P "$SERVER_PORT")
fi
IDENTITY_PATH="${SSH_IDENTITY_FILE:-}"
IDENTITY_PATH="${IDENTITY_PATH/#\~/$HOME}"
if [[ -n "$IDENTITY_PATH" ]]; then
  SSH_DEPLOY_ARGS+=(-i "$IDENTITY_PATH")
  SCP_DEPLOY_ARGS+=(-i "$IDENTITY_PATH")
fi
if [[ "${SSH_STRICT_HOST_KEY_CHECKING:-0}" != "1" ]]; then
  SSH_DEPLOY_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
  SCP_DEPLOY_ARGS+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
fi
if [[ "${SSH_BATCH_MODE:-1}" == "1" ]]; then
  SSH_DEPLOY_ARGS+=(-o BatchMode=yes)
  SCP_DEPLOY_ARGS+=(-o BatchMode=yes)
fi

# Triển khai và xác minh payload trước khi tạo bất kỳ trial nào.
REMOTE_QUOTED="$(printf '%q' "$REMOTE_PATH")"
ssh "${SSH_DEPLOY_ARGS[@]}" \
  "${SERVER_USER}@${SERVER_HOST}" \
  "mkdir -p $REMOTE_QUOTED"
scp "${SCP_DEPLOY_ARGS[@]}" \
  "$PAYLOAD_PATH"/large_output_s*_100KB.txt \
  "$PAYLOAD_PATH"/SHA256SUMS \
  "${SERVER_USER}@${SERVER_HOST}:${REMOTE_PATH}/"
ssh "${SSH_DEPLOY_ARGS[@]}" \
  "${SERVER_USER}@${SERVER_HOST}" \
  "cd $REMOTE_QUOTED && sha256sum -c SHA256SUMS"

PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_COMMAND" src/run_w2.py "$CONFIG"
"$PYTHON_COMMAND" tools/analyze_w2.py "$RESULT_PATH"
if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  "$PYTHON_COMMAND" tools/verify_ssh3_mux.py "$RESULT_PATH"
fi

echo "Hoàn tất. Xem $RESULT_PATH/scenario_summary.csv"
