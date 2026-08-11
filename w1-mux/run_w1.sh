#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$PROJECT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

CONFIG="${1:-config.env}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG. Create it with: cp config.example.env config.env" >&2
  exit 2
fi

# Đọc cấu hình nhưng giữ các giá trị môi trường đã truyền vào.
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

mkdir -p "${RESULT_DIR:-artifacts/results}"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_mux_stdio.patch"
  PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Missing or stale shared SSH3 mux client: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

SSH_PORT_ARGS=()
SCP_PORT_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_PORT_ARGS=(-p "$SERVER_PORT")
  SCP_PORT_ARGS=(-P "$SERVER_PORT")
fi

# Triển khai agent W1 trước khi bắt đầu các trial đo lường.
scp ${SCP_PORT_ARGS[@]+"${SCP_PORT_ARGS[@]}"} \
  "$PROJECT_DIR/remote_agent.py" \
  "${SERVER_USER}@${SERVER_HOST}:${W1_REMOTE_AGENT}"
ssh ${SSH_PORT_ARGS[@]+"${SSH_PORT_ARGS[@]}"} \
  "${SERVER_USER}@${SERVER_HOST}" "chmod 700 ${W1_REMOTE_AGENT}"

PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "${PYTHON_BIN:-python3}" src/run_w1.py "$CONFIG"
"${PYTHON_BIN:-python3}" tools/analyze_w1.py "${RESULT_DIR:-artifacts/results}"
if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  "${PYTHON_BIN:-python3}" tools/verify_ssh3_mux.py "${RESULT_DIR:-artifacts/results}"
fi

echo "Done. See ${RESULT_DIR:-artifacts/results}/scenario_summary.csv"
