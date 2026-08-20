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
RESULT_PATH="${RESULT_DIR:-artifacts/results}"
mkdir -p "$RESULT_PATH"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_mux_stdio.patch"
  CC_SOURCE_PATH="$REPO_DIR/stream_mux/patches/mux_cc.go"
  PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$CC_SOURCE_PATH" | shasum -a 256 | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Thiếu hoặc sai phiên bản SSH3 multiplex bridge: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

# Kiểm tra editor/tmux trước khi tạo trial; thao tác này nằm ngoài khoảng đo.
SSH_PREFLIGHT=("${SSH_BIN:-ssh}")
if [[ -n "${SERVER_PORT:-}" ]]; then SSH_PREFLIGHT+=(-p "$SERVER_PORT"); fi
if [[ -n "${SSH_IDENTITY_FILE:-}" ]]; then
  IDENTITY_PATH="${SSH_IDENTITY_FILE/#\~/$HOME}"
  SSH_PREFLIGHT+=(-i "$IDENTITY_PATH")
fi
if [[ "${SSH_STRICT_HOST_KEY_CHECKING:-0}" != "1" ]]; then
  SSH_PREFLIGHT+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)
fi
if [[ "${SSH_BATCH_MODE:-1}" == "1" ]]; then SSH_PREFLIGHT+=(-o BatchMode=yes); fi

REMOTE_BINS=()
[[ ",${EDITORS}," == *,vim,* ]] && REMOTE_BINS+=("${VIM_BIN:-vim}")
[[ ",${EDITORS}," == *,nano,* ]] && REMOTE_BINS+=("${NANO_BIN:-nano}")
[[ ",${PROTOCOLS}," == *,mosh,* ]] && REMOTE_BINS+=("${TMUX_BIN:-tmux}")
CHECK_COMMAND=""
for binary in "${REMOTE_BINS[@]}"; do
  CHECK_COMMAND+="command -v $(printf '%q' "$binary") >/dev/null || exit 44; "
done
"${SSH_PREFLIGHT[@]}" "${SERVER_USER}@${SERVER_HOST}" "$CHECK_COMMAND"

PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_COMMAND" src/run_w3.py "$CONFIG"
"$PYTHON_COMMAND" tools/analyze_w3.py "$RESULT_PATH"
"$PYTHON_COMMAND" tools/verify_mux.py "$RESULT_PATH"

echo "Hoàn tất. Xem $RESULT_PATH/scenario_summary.csv"
