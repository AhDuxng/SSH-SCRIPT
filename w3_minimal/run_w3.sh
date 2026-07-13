#!/usr/bin/env bash
set -euo pipefail

CONFIG="${1:-config.env}"
source "$CONFIG"

mkdir -p "${RESULT_DIR:-results}" "${LOG_DIR:-logs}"

SSH_PORT_ARGS=()
SCP_PORT_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_PORT_ARGS=(-p "$SERVER_PORT")
  SCP_PORT_ARGS=(-P "$SERVER_PORT")
fi

scp "${SCP_PORT_ARGS[@]}" remote_workloads.sh "${SERVER_USER}@${SERVER_HOST}:${REMOTE_WORKLOAD}"
ssh "${SSH_PORT_ARGS[@]}" "${SERVER_USER}@${SERVER_HOST}" "chmod +x ${REMOTE_WORKLOAD}"

"${PYTHON_BIN:-python}" run_w3.py "$CONFIG"
"${PYTHON_BIN:-python}" analyze_w3.py "${RESULT_DIR:-results}/samples.csv" "${RESULT_DIR:-results}/summary.csv"

echo "Done. See ${RESULT_DIR:-results}/samples.csv and ${RESULT_DIR:-results}/summary.csv"
