#!/usr/bin/env bash
# Chạy một lần đo CHẨN ĐOÁN có qlog ở cả hai đầu, rồi tự hoàn tác server.
#
# Dùng: bash stream_mux/scripts/qlog_run.sh <workload-dir> [config.env]
#   ví dụ: bash stream_mux/scripts/qlog_run.sh w2-mux-tt
#
# Biến truyền thêm được chuyển thẳng cho run_wN.sh, ví dụ:
#   TRIALS_PER_COMBINATION=1 SAMPLES_PER_STREAM_PER_TRIAL=20 SCENARIOS=W2-S1
#
# KHÔNG dùng cho lần đo lấy số liệu chính: qlog ghi ~0.5 MB cho mỗi MB dữ
# liệu và sẽ làm chậm chính phép đo.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKLOAD="${1:?usage: qlog_run.sh <workload-dir> [config.env]}"
CONFIG="${2:-config.env}"
WORKLOAD_DIR="$REPO_DIR/${WORKLOAD%/}"
[[ -d "$WORKLOAD_DIR" ]] || { echo "Không có thư mục $WORKLOAD_DIR" >&2; exit 2; }
cd "$WORKLOAD_DIR"
[[ -f "$CONFIG" ]] || { echo "Không có $WORKLOAD_DIR/$CONFIG" >&2; exit 2; }

NAME="$(basename "$WORKLOAD_DIR")"
RUNNER="run_${NAME%%-*}.sh"
[[ -f "$RUNNER" ]] || { echo "Không có $RUNNER" >&2; exit 2; }

# Đọc cấu hình đủ để biết server và cách đăng nhập.
cfg() { sed -n "s/^$1=//p" "$CONFIG" | tail -1 | tr -d '"'"'"''; }
SERVER_USER="$(cfg SERVER_USER)"
SERVER_HOST="$(cfg SERVER_HOST)"
[[ -n "$SERVER_USER" && -n "$SERVER_HOST" ]] || {
  echo "Thiếu SERVER_USER/SERVER_HOST trong $CONFIG" >&2; exit 2; }
SRV="$SERVER_USER@$SERVER_HOST"

RESULT_DIR="${RESULT_DIR:-artifacts/qlogrun}"
QLOG_DIR="${SSH3_QLOG_DIR:-artifacts/qlog}"
SERVER_QLOG_DIR="/var/log/ssh3-qlog"
DROPIN="/etc/systemd/system/ssh3-server.service.d/qlog.conf"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null)

# Luôn trả server về nguyên trạng, kể cả khi lần chạy hỏng giữa chừng.
server_qlog_off() {
  echo "[QLOG] tắt qlog trên $SRV và khôi phục service"
  ssh -t "${SSH_OPTS[@]}" "$SRV" "
    sudo rm -f $DROPIN
    sudo rmdir /etc/systemd/system/ssh3-server.service.d 2>/dev/null || true
    sudo systemctl daemon-reload
    sudo systemctl restart ssh3-server
    sudo rm -rf $SERVER_QLOG_DIR
    systemctl is-active ssh3-server" || echo "[QLOG] CẢNH BÁO: hoàn tác thất bại, kiểm tra $SRV thủ công" >&2
}
trap server_qlog_off EXIT

echo "[QLOG] bật qlog trên $SRV (cần sudo)"
ssh -t "${SSH_OPTS[@]}" "$SRV" "
  sudo mkdir -p $SERVER_QLOG_DIR /etc/systemd/system/ssh3-server.service.d
  sudo chown \$USER $SERVER_QLOG_DIR
  printf '[Service]\nEnvironment=QLOGDIR=$SERVER_QLOG_DIR\n' > /tmp/qlog.conf
  sudo install -m 0644 /tmp/qlog.conf $DROPIN
  rm -f /tmp/qlog.conf
  sudo systemctl daemon-reload
  sudo systemctl restart ssh3-server
  sleep 2
  systemctl show ssh3-server -p Environment | grep -q QLOGDIR || exit 3
  systemctl is-active ssh3-server"

rm -rf "$QLOG_DIR" "$RESULT_DIR/qlog-server"
mkdir -p "$RESULT_DIR"

echo "[QLOG] chạy $RUNNER (client qlog -> $QLOG_DIR)"
SSH3_QLOG=1 SSH3_QLOG_DIR="$QLOG_DIR" RESULT_DIR="$RESULT_DIR" \
  bash "$RUNNER" "$CONFIG"

echo "[QLOG] kéo qlog phía server về"
mkdir -p "$RESULT_DIR/qlog-server"
rsync -az -e "ssh ${SSH_OPTS[*]}" "$SRV:$SERVER_QLOG_DIR/" "$RESULT_DIR/qlog-server/" \
  || echo "[QLOG] CẢNH BÁO: không kéo được qlog phía server" >&2

ANALYZER="tools/analyze_qlog.py"
[[ -f "$ANALYZER" ]] || ANALYZER="$REPO_DIR/w2-mux-tt/tools/analyze_qlog.py"
PYTHON_COMMAND="$(cfg PYTHON_BIN)"; PYTHON_COMMAND="${PYTHON_COMMAND:-python3}"

echo
echo "===== qlog phía CLIENT (bên nhận payload) ====="
"$PYTHON_COMMAND" "$ANALYZER" "$QLOG_DIR" "$RESULT_DIR" || true
mv -f "$RESULT_DIR/qlog_summary.csv" "$RESULT_DIR/qlog_summary_client.csv" 2>/dev/null || true

echo
echo "===== qlog phía SERVER (bên GỬI payload — cwnd cần nhìn ở đây) ====="
"$PYTHON_COMMAND" "$ANALYZER" "$RESULT_DIR/qlog-server" "$RESULT_DIR" || true
mv -f "$RESULT_DIR/qlog_summary.csv" "$RESULT_DIR/qlog_summary_server.csv" 2>/dev/null || true

echo
echo "Xong. Kết quả trong $WORKLOAD_DIR/$RESULT_DIR"
