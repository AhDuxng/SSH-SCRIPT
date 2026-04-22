#!/usr/bin/env bash
# run_w3_benchmark.sh
# Chạy W3 Interactive Editing benchmark với đầy đủ tham số tường minh.
# Chỉnh sửa các biến bên dưới trước khi chạy.

set -euo pipefail

# ── Kết nối ──────────────────────────────────────────────────────────────────
HOST="192.168.8.102"
USER_NAME="trungnt"
SOURCE_IP="192.168.8.100"
IDENTITY_FILE="$HOME/.ssh/id_rsa"

# ── Giao thức & workload ──────────────────────────────────────────────────────
PROTOCOLS="ssh"                          # ssh | ssh3 | mosh  (space-separated)
WORKLOADS="interactive_shell vim nano"   # interactive_shell | vim | nano

# ── Số vòng đo ───────────────────────────────────────────────────────────────
ITERATIONS=10          # số sample thực đo (bỏ warmup)
WARMUP_ROUNDS=5        # số vòng khởi động (không tính kết quả)
TIMEOUT=20             # pexpect timeout (giây)
SEED=42                # random seed để tái hiện kết quả

# ── Output ───────────────────────────────────────────────────────────────────
OUTPUT_DIR="w3_results"
LOG_PEXPECT=true       # true = ghi raw pexpect log ra file

# ── Prompt marker ─────────────────────────────────────────────────────────────
PROMPT="__W3_PROMPT__# "

# ── SSH3 ─────────────────────────────────────────────────────────────────────
SSH3_PATH="/ssh3-term"
SSH3_INSECURE=true     # true = thêm -insecure cho ssh3

# ── SSH / Mosh tuning ────────────────────────────────────────────────────────
BATCH_MODE=false               # true = BatchMode=yes (tắt password prompt)
STRICT_HOST_KEY=false          # true = bật StrictHostKeyChecking
MOSH_PREDICT="never"           # adaptive | always | never

# ── Remote editor files ───────────────────────────────────────────────────────
REMOTE_VIM_FILE="/tmp/w3_vim_bench.txt"
REMOTE_NANO_FILE="/tmp/w3_nano_bench.txt"

# ── Misc ─────────────────────────────────────────────────────────────────────
SHUFFLE_PAIRS=false        # true = xáo trộn thứ tự protocol/workload
REOPEN_ON_FAILURE=true     # true = mở lại session sau khi sample thất bại

# ─────────────────────────────────────────────────────────────────────────────
# Xây dựng lệnh
# ─────────────────────────────────────────────────────────────────────────────

CMD=(
    python w3_interactive_benchmark.py
    --host            "$HOST"
    --user            "$USER_NAME"
    --source-ip       "$SOURCE_IP"
    --identity-file   "$IDENTITY_FILE"
    --protocols       $PROTOCOLS
    --workloads       $WORKLOADS
    --iterations      "$ITERATIONS"
    --warmup-rounds   "$WARMUP_ROUNDS"
    --timeout         "$TIMEOUT"
    --seed            "$SEED"
    --output-dir      "$OUTPUT_DIR"
    --prompt          "$PROMPT"
    --ssh3-path       "$SSH3_PATH"
    --mosh-predict    "$MOSH_PREDICT"
    --remote-vim-file  "$REMOTE_VIM_FILE"
    --remote-nano-file "$REMOTE_NANO_FILE"
)

# Các cờ boolean (chỉ thêm nếu = true)
$SSH3_INSECURE       && CMD+=(--ssh3-insecure)
$BATCH_MODE          && CMD+=(--batch-mode)
$STRICT_HOST_KEY     && CMD+=(--strict-host-key-checking)
$SHUFFLE_PAIRS       && CMD+=(--shuffle-pairs)
$REOPEN_ON_FAILURE   && CMD+=(--reopen-on-failure)
$LOG_PEXPECT         && CMD+=(--log-pexpect)

# ─────────────────────────────────────────────────────────────────────────────
echo "=== W3 Interactive Benchmark ==="
echo "Lệnh thực thi:"
printf '  %s \\\n' "${CMD[@]}"
echo ""

exec "${CMD[@]}"
