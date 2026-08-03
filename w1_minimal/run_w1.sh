#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

CONFIG="${1:-config.env}"
if [[ ! -f "$CONFIG" ]]; then
  echo "Missing $CONFIG. Create it with: cp config.example.env config.env" >&2
  exit 2
fi

# Đọc config nhưng giữ quyền ghi đè của biến môi trường từ command line.
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

if [[ ! -x "${PYTHON_BIN:-.venv/bin/python}" ]]; then
  echo "Missing Python environment. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 2
fi

"${PYTHON_BIN:-.venv/bin/python}" src/run_w1.py "$CONFIG"
"${PYTHON_BIN:-.venv/bin/python}" tools/analyze_w1.py "${RESULT_DIR:-artifacts/results}"

if [[ "${AUTO_PLOT:-1}" == "1" ]]; then
  IFS=',' read -r -a plot_metrics <<< "${PLOT_METRICS:-mean,median,p90,p95}"
  for metric in "${plot_metrics[@]}"; do
    "${PYTHON_BIN:-.venv/bin/python}" tools/plot_w1.py \
      "${RESULT_DIR:-artifacts/results}" \
      "${FIGURE_DIR:-artifacts/figures}" \
      --metric "$metric"
  done
fi

echo "Done. See ${RESULT_DIR:-artifacts/results} and ${FIGURE_DIR:-artifacts/figures}"
