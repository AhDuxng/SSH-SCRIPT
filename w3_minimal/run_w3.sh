#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

CONFIG="${1:-config.env}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG. Create it with: cp config.example.env config.env" >&2
  exit 2
fi
# Doc config ma khong ghi de cac bien da duoc truyen tu command line.
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

mkdir -p "${RESULT_DIR:-artifacts/results}" "${LOG_DIR:-artifacts/logs}"

if [[ ",$PROTOCOLS," == *,ssh3,* ]]; then
  PATCH_HASH="$(shasum -a 256 patches/ssh3_mux.patch | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_BIN}.patch.sha256" && sed -n '1p' "${SSH3_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Missing or stale multiplex-capable SSH3 client: $SSH3_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_BIN" bash scripts/build_ssh3_mux.sh
  fi
  if [[ ! -x "$SSH3_BIN" ]]; then
    echo "Missing multiplex-capable SSH3 client: $SSH3_BIN" >&2
    exit 2
  fi
fi

SSH_PORT_ARGS=()
SCP_PORT_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_PORT_ARGS=(-p "$SERVER_PORT")
  SCP_PORT_ARGS=(-P "$SERVER_PORT")
fi

scp ${SCP_PORT_ARGS[@]+"${SCP_PORT_ARGS[@]}"} scripts/remote_workloads.sh "${SERVER_USER}@${SERVER_HOST}:${REMOTE_WORKLOAD}"
ssh ${SSH_PORT_ARGS[@]+"${SSH_PORT_ARGS[@]}"} "${SERVER_USER}@${SERVER_HOST}" "chmod +x ${REMOTE_WORKLOAD}"

"${PYTHON_BIN:-python}" src/run_w3.py "$CONFIG"
"${PYTHON_BIN:-python}" tools/analyze_w3.py \
  "${RESULT_DIR:-artifacts/results}/samples.csv" \
  "${RESULT_DIR:-artifacts/results}/summary.csv"
if [[ ",$PROTOCOLS," == *,ssh3,* ]]; then
  "${PYTHON_BIN:-python}" tools/verify_ssh3_mux.py "${RESULT_DIR:-artifacts/results}"
fi

# Đo và lưu riêng thời gian mở phiên mới sau khi hoàn tất ma trận tương tác.
if [[ "${RUN_SESSION_SETUP:-1}" == "1" ]]; then
  "${PYTHON_BIN:-python}" tools/measure_session_setup.py "$CONFIG"
fi

echo "Done. See ${RESULT_DIR:-artifacts/results}/summary.csv and ${RESULT_DIR:-artifacts/results}/setup_summary.csv"
