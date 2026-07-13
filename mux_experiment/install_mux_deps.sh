#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
CONFIG="${1:-mux_config.env}"
source "$CONFIG"

SSH_PORT_ARGS=()
if [[ -n "${SERVER_PORT:-}" ]]; then
  SSH_PORT_ARGS=(-p "$SERVER_PORT")
fi
REMOTE="${SERVER_USER}@${SERVER_HOST}"

echo "[local] installing aioquic with $LOCAL_PYTHON_BIN"
"$LOCAL_PYTHON_BIN" -m pip install aioquic

echo "[remote] installing aioquic with $REMOTE_PYTHON_BIN on $REMOTE"
ssh "${SSH_PORT_ARGS[@]}" "$REMOTE" "'$REMOTE_PYTHON_BIN' -m pip install aioquic"

echo "Done."
