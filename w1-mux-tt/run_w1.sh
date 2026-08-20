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
source "$REPO_DIR/stream_mux/scripts/congestion_run.sh"

if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  PATCH_PATH="$REPO_DIR/stream_mux/patches/ssh3_mux_stdio.patch"
  CC_SOURCE_PATH="$REPO_DIR/stream_mux/patches/mux_cc.go"
  PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$CC_SOURCE_PATH" | shasum -a 256 | awk '{print $1}')"
  BUILT_HASH="$(test -f "${SSH3_MUX_BIN}.patch.sha256" && sed -n '1p' "${SSH3_MUX_BIN}.patch.sha256" || true)"
  if [[ ! -x "$SSH3_MUX_BIN" || "$PATCH_HASH" != "$BUILT_HASH" ]]; then
    if [[ "${AUTO_BUILD_SSH3_MUX:-1}" != "1" ]]; then
      echo "Missing or stale shared SSH3 mux client: $SSH3_MUX_BIN" >&2
      exit 2
    fi
    SSH3_MUX_BIN="$SSH3_MUX_BIN" bash "$REPO_DIR/stream_mux/scripts/build_ssh3_mux.sh"
  fi
fi

stream_mux_cc_prepare "$RESULT_PATH"

# Chạy trực tiếp nên không triển khai chương trình phụ lên máy đích.
PYTHONPATH="$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_COMMAND" src/run_w1.py "$CONFIG"
"$PYTHON_COMMAND" tools/analyze_w1.py "$RESULT_PATH"
if [[ ",${PROTOCOLS}," == *,ssh3,* ]]; then
  "$PYTHON_COMMAND" tools/verify_ssh3_mux.py "$RESULT_PATH"
fi
stream_mux_cc_finish "$RESULT_PATH"

echo "Done. See $RESULT_PATH/scenario_summary.csv"
