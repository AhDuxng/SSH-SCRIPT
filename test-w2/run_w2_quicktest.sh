#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# W2 Quick Test — kiểm tra nhanh từng workload trước khi chạy benchmark chính
#
# Mục đích: Chạy 1 trial × 5 iterations cho mỗi workload trên 1 protocol
#           để xác nhận script hoạt động đúng và output hợp lý.
#
# Expected results (Tailscale, ~10-50ms RTT):
#   top  : inter-arrival ≈ 1000ms + network delay → expect ~1000-1100ms
#   tail : inter-arrival ≈   50ms + network delay → expect ~50-80ms
#   ping : inter-arrival ≈  100ms + network delay → expect ~100-130ms
#
# Usage:
#   ./run_w2_quicktest.sh              # test tất cả workloads, chỉ SSH
#   ./run_w2_quicktest.sh top          # test chỉ workload top
#   ./run_w2_quicktest.sh tail ssh3    # test workload tail trên SSH3
#   ./run_w2_quicktest.sh all mosh     # test tất cả trên Mosh
# ============================================================================

HOST="100.66.79.93"
USER_NAME="pi"
SOURCE_IP="100.70.166.91"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"

WORKLOAD="${1:-all}"
PROTOCOL="${2:-ssh}"

ITERATIONS=5
TRIALS=1
TIMEOUT=30
TOP_INTERVAL=1.0

OUTPUT_DIR="w2_quicktest_results"
PROMPT="__W2_PROMPT__# "

SSH3_PATH=":4433/ssh3-term"
SSH3_INSECURE=true
MOSH_PREDICT="never"

# Map workload argument
if [[ "$WORKLOAD" == "all" ]]; then
    WORKLOADS="top tail ping"
else
    WORKLOADS="$WORKLOAD"
fi

CMD=(
  python w2_continuous_monitoring_benchmark.py
  --host "$HOST"
  --user "$USER_NAME"
  --source-ip "$SOURCE_IP"
  --identity-file "$IDENTITY_FILE"
  --protocols "$PROTOCOL"
  --workloads $WORKLOADS
  --iterations "$ITERATIONS"
  --trials "$TRIALS"
  --timeout "$TIMEOUT"
  --seed 42
  --output-dir "$OUTPUT_DIR"
  --prompt "$PROMPT"
  --ssh3-path "$SSH3_PATH"
  --mosh-predict "$MOSH_PREDICT"
  --top-interval "$TOP_INTERVAL"
  --log-pexpect
  --reopen-on-failure
)

$SSH3_INSECURE && CMD+=(--ssh3-insecure)

echo "=== W2 Quick Test ==="
echo "Protocol : $PROTOCOL"
echo "Workloads: $WORKLOADS"
echo "Trials   : $TRIALS × $ITERATIONS iterations"
echo ""
echo "Expected inter-arrival times:"
echo "  top  : ~1000 ms (server interval 1.0s)"
echo "  tail : ~50 ms   (server interval 0.05s)"
echo "  ping : ~100 ms  (server interval 0.1s)"
echo ""
echo "Command:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

exec "${CMD[@]}"
