#!/usr/bin/env bash
# monitor_pi_cpu.sh — Monitor CPU usage trên Pi khi chạy W4 benchmark.
#
# Ghi lại CPU% của các process ssh3, sshd, mosh-server trên Pi mỗi giây.
# Nếu ssh3 server chiếm gần 100% CPU → xác nhận userspace QUIC overhead là bottleneck.
#
# Usage:
#   ./monitor_pi_cpu.sh start [duration_seconds]   # bắt đầu monitor (default: 600s)
#   ./monitor_pi_cpu.sh stop                       # dừng monitor sớm
#   ./monitor_pi_cpu.sh report                     # xem báo cáo từ lần chạy gần nhất
#
# Yêu cầu trên Pi:
#   - pidstat (sudo apt install sysstat)
#   - hoặc dùng fallback top nếu không có pidstat
#
set -euo pipefail

HOST="10.42.0.206"
USER_NAME="pi"
IDENTITY_FILE="$HOME/.ssh/id_ed25519"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -i $IDENTITY_FILE"

RESULT_DIR="w4_results/_verify_cpu"
mkdir -p "$RESULT_DIR"
PID_FILE="$RESULT_DIR/.monitor_pid"
LOG_FILE="$RESULT_DIR/cpu_monitor_$(date +%Y%m%d_%H%M%S).csv"
LATEST_LOG="$RESULT_DIR/cpu_monitor_latest.csv"

log() { printf '[%(%H:%M:%S)T] %s\n' -1 "$*"; }

start_monitor() {
    local duration="${1:-600}"

    if [[ -f "$PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            log "Monitor đang chạy (PID=$old_pid). Dùng 'stop' để dừng trước."
            exit 1
        fi
        rm -f "$PID_FILE"
    fi

    log "=== CPU Monitor ==="
    log "Host: $USER_NAME@$HOST"
    log "Duration: ${duration}s"
    log "Log file: $LOG_FILE"

    # Check if pidstat is available on Pi
    local has_pidstat
    has_pidstat=$(ssh $SSH_OPTS "$USER_NAME@$HOST" "which pidstat 2>/dev/null && echo yes || echo no")

    if [[ "$has_pidstat" == *"yes"* ]]; then
        log "Dùng pidstat (chính xác hơn)"
        _start_pidstat_monitor "$duration"
    else
        log "pidstat không có trên Pi, dùng top fallback"
        log "Gợi ý: ssh $USER_NAME@$HOST 'sudo apt install sysstat' để có pidstat"
        _start_top_monitor "$duration"
    fi
}

_start_pidstat_monitor() {
    local duration="$1"

    # Header cho CSV
    echo "timestamp,process,pid,cpu_pct,mem_pct,vsz_kb,rss_kb" > "$LOG_FILE"

    # Chạy pidstat trên Pi, filter ssh3/sshd/mosh, ghi ra CSV
    ssh $SSH_OPTS "$USER_NAME@$HOST" bash -s "$duration" << 'REMOTE_SCRIPT' >> "$LOG_FILE" &
DURATION=$1
END_TIME=$((SECONDS + DURATION))
while [[ $SECONDS -lt $END_TIME ]]; do
    TIMESTAMP=$(date +%Y-%m-%dT%H:%M:%S)
    # Lấy CPU% của các process liên quan
    ps aux | grep -E '(ssh3|sshd|mosh-server)' | grep -v grep | while read -r user pid cpu mem vsz rss tty stat start time cmd; do
        # Xác định tên process
        PNAME="unknown"
        if echo "$cmd" | grep -q "ssh3"; then
            PNAME="ssh3"
        elif echo "$cmd" | grep -q "mosh-server"; then
            PNAME="mosh-server"
        elif echo "$cmd" | grep -q "sshd"; then
            PNAME="sshd"
        fi
        echo "$TIMESTAMP,$PNAME,$pid,$cpu,$mem,$vsz,$rss"
    done
    sleep 1
done
REMOTE_SCRIPT

    local bg_pid=$!
    echo "$bg_pid" > "$PID_FILE"
    ln -sf "$(basename "$LOG_FILE")" "$LATEST_LOG"

    log "Monitor started (PID=$bg_pid, background)"
    log "Chạy benchmark ở terminal khác, rồi dùng './monitor_pi_cpu.sh stop' khi xong."
    log "Hoặc đợi ${duration}s để tự dừng."
}

_start_top_monitor() {
    local duration="$1"

    echo "timestamp,process,pid,cpu_pct,mem_pct,vsz_kb,rss_kb" > "$LOG_FILE"

    ssh $SSH_OPTS "$USER_NAME@$HOST" bash -s "$duration" << 'REMOTE_SCRIPT' >> "$LOG_FILE" &
DURATION=$1
END_TIME=$((SECONDS + DURATION))
while [[ $SECONDS -lt $END_TIME ]]; do
    TIMESTAMP=$(date +%Y-%m-%dT%H:%M:%S)
    # top batch mode, 1 iteration
    top -b -n 1 | grep -E '(ssh3|sshd|mosh-server)' | grep -v grep | while read -r pid user pr ni virt res shr s cpu mem time cmd; do
        PNAME="unknown"
        if echo "$cmd" | grep -q "ssh3"; then
            PNAME="ssh3"
        elif echo "$cmd" | grep -q "mosh-server"; then
            PNAME="mosh-server"
        elif echo "$cmd" | grep -q "sshd"; then
            PNAME="sshd"
        fi
        echo "$TIMESTAMP,$PNAME,$pid,$cpu,$mem,$virt,$res"
    done
    sleep 1
done
REMOTE_SCRIPT

    local bg_pid=$!
    echo "$bg_pid" > "$PID_FILE"
    ln -sf "$(basename "$LOG_FILE")" "$LATEST_LOG"

    log "Monitor started (PID=$bg_pid, background)"
    log "Chạy benchmark ở terminal khác, rồi dùng './monitor_pi_cpu.sh stop' khi xong."
}

stop_monitor() {
    if [[ ! -f "$PID_FILE" ]]; then
        log "Không tìm thấy monitor đang chạy."
        exit 0
    fi

    local pid
    pid=$(cat "$PID_FILE")

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        log "Monitor stopped (PID=$pid)"
    else
        log "Monitor đã dừng trước đó (PID=$pid)"
    fi

    rm -f "$PID_FILE"
    log "Dùng './monitor_pi_cpu.sh report' để xem kết quả."
}

show_report() {
    local target_log="${1:-$LATEST_LOG}"

    if [[ ! -f "$target_log" ]]; then
        log "Không tìm thấy log file. Chạy 'start' trước."
        exit 1
    fi

    # Resolve symlink
    if [[ -L "$target_log" ]]; then
        target_log="$RESULT_DIR/$(readlink "$target_log")"
    fi

    local total_lines
    total_lines=$(wc -l < "$target_log")
    log "=== CPU Monitor Report ==="
    log "Log: $target_log"
    log "Total samples: $((total_lines - 1))"
    echo

    if [[ $total_lines -le 1 ]]; then
        log "Không có dữ liệu. Có thể monitor chưa chạy đủ lâu hoặc không có process nào active."
        exit 0
    fi

    echo "=== Thống kê CPU% theo process ==="
    echo
    echo "Process       | Samples | Mean CPU% | Max CPU% | Min CPU%"
    echo "--------------|---------|-----------|----------|--------"

    # Parse CSV và tính thống kê cho từng process
    for proc in ssh3 sshd mosh-server; do
        local data
        data=$(tail -n +2 "$target_log" | awk -F',' -v p="$proc" '$2==p {print $4}')
        if [[ -z "$data" ]]; then
            printf "%-13s | %7s | %9s | %8s | %s\n" "$proc" "0" "—" "—" "—"
            continue
        fi

        local count mean max min
        count=$(echo "$data" | wc -l)
        mean=$(echo "$data" | awk '{s+=$1} END {printf "%.1f", s/NR}')
        max=$(echo "$data" | sort -n | tail -1)
        min=$(echo "$data" | sort -n | head -1)
        printf "%-13s | %7d | %8s%% | %7s%% | %s%%\n" "$proc" "$count" "$mean" "$max" "$min"
    done

    echo
    echo "=== Phân tích ==="
    echo

    # Check if ssh3 CPU is high
    local ssh3_max
    ssh3_max=$(tail -n +2 "$target_log" | awk -F',' '$2=="ssh3" {print $4}' | sort -n | tail -1)

    if [[ -n "$ssh3_max" ]]; then
        local ssh3_max_int
        ssh3_max_int=$(echo "$ssh3_max" | cut -d. -f1)
        if [[ "$ssh3_max_int" -ge 80 ]]; then
            echo "⚠ SSH3 server đạt CPU ${ssh3_max}% → XÁC NHẬN: userspace QUIC overhead"
            echo "  là bottleneck. CPU Pi không đủ mạnh để chạy quic-go hiệu quả"
            echo "  dưới packet loss."
        elif [[ "$ssh3_max_int" -ge 50 ]]; then
            echo "△ SSH3 server đạt CPU ${ssh3_max}% → CÓ THỂ là bottleneck."
            echo "  CPU usage đáng kể nhưng chưa bão hòa hoàn toàn."
            echo "  Vấn đề có thể nằm ở cả CPU lẫn congestion control logic."
        else
            echo "○ SSH3 server CPU thấp (max ${ssh3_max}%) → CPU KHÔNG phải bottleneck."
            echo "  Vấn đề nằm ở congestion control logic của quic-go,"
            echo "  không phải thiếu CPU."
        fi
    else
        echo "Không có dữ liệu SSH3. Đảm bảo benchmark SSH3 đang chạy khi monitor active."
    fi

    echo
    echo "=== Timeline (10 dòng cuối) ==="
    tail -10 "$target_log" | column -t -s','
}

# --- Main ---
ACTION="${1:-help}"

case "$ACTION" in
    start)
        shift
        start_monitor "${1:-600}"
        ;;
    stop)
        stop_monitor
        ;;
    report)
        show_report
        ;;
    *)
        echo "Usage:"
        echo "  $0 start [duration_seconds]   # bắt đầu monitor (default: 600s)"
        echo "  $0 stop                       # dừng monitor"
        echo "  $0 report                     # xem báo cáo"
        echo
        echo "Workflow:"
        echo "  Terminal 1: ./monitor_pi_cpu.sh start"
        echo "  Terminal 2: ./run_w4_benchmark.sh medium"
        echo "  Terminal 1: ./monitor_pi_cpu.sh stop"
        echo "  Terminal 1: ./monitor_pi_cpu.sh report"
        exit 1
        ;;
esac
