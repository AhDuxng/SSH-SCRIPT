#!/usr/bin/env bash

# Chạy lại workload qua tee và giữ log cạnh thư mục results.
stream_mux_start_run_log() {
  local result_path="$1"
  local runner="$2"
  shift 2
  local result_name result_parent log_path

  result_path="${result_path%/}"
  result_name="${result_path##*/}"
  result_parent="${result_path%/*}"
  if [[ "$result_parent" == "$result_path" ]]; then
    result_parent="."
  fi

  if [[ -n "${FULL_RUN_LOG:-}" ]]; then
    log_path="$FULL_RUN_LOG"
  elif [[ "$result_name" == "results" ]]; then
    log_path="$result_parent/full_run.log"
  else
    # Smoke test: /tmp/w1-smoke -> /tmp/w1-smoke.log.
    log_path="${result_path}.log"
  fi

  mkdir -p "$(dirname "$log_path")"
  export FULL_RUN_LOG="$log_path"

  if [[ "${STREAM_MUX_RUN_LOG_ACTIVE:-0}" != "1" ]]; then
    STREAM_MUX_RUN_LOG_ACTIVE=1 FULL_RUN_LOG="$log_path" \
      bash "$runner" "$@" 2>&1 | tee "$log_path"
    local runner_status="${PIPESTATUS[0]}"
    exit "$runner_status"
  fi
  echo "[LOG] full_run_log=$log_path"
}
