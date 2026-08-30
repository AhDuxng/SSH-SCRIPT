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

PYTHON_COMMAND="${PYTHON_BIN:-python3}"
RESULT_PATH="${RESULT_DIR:-artifacts/results}"
mkdir -p "$RESULT_PATH"
source "$REPO_DIR/stream_mux/scripts/run_logging.sh"
stream_mux_start_run_log "$RESULT_PATH" "$PROJECT_DIR/run_w1.sh" "$CONFIG"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  source "$REPO_DIR/stream_mux/scripts/patch_hash.sh"
  PATCH_HASH="$(stream_mux_patch_hash "$REPO_DIR/stream_mux")"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Missing or stale shared SSH3 mux client: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

# Chạy trực tiếp nên không triển khai chương trình phụ lên máy đích.
PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_COMMAND" src/run_w1.py "$CONFIG"
"$PYTHON_COMMAND" tools/analyze_w1.py "$RESULT_PATH"
if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  "$PYTHON_COMMAND" tools/verify_ssh3_mux.py "$RESULT_PATH"
fi

echo "Done. See $RESULT_PATH/scenario_summary.csv"
