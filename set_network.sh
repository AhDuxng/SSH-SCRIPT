#!/usr/bin/env bash
# set_network.sh — Áp dụng network emulation bằng tc netem
#
# QUAN TRỌNG VỀ OWD vs RTT:
#   Script này áp tc netem lên LOCAL interface.
#   Nếu chỉ chạy trên CLIENT:  OWD_client=Xms, OWD_server=0ms → RTT ≈ Xms (KHÔNG phải 2X)
#   Nếu chạy trên CẢ HAI đầu: OWD_client=Xms, OWD_server=Xms → RTT ≈ 2X ms (ĐÚNG)
#
#   Luôn chạy script này trên CẢ client VÀ server với cùng scenario
#   để RTT đúng bằng 2 × OWD.
#
# Usage:
#   ./set_network.sh <iface> {low|medium|high|clear|show}
#
# Scenarios (OWD = one-way delay, RTT = 2 × OWD):
#   Cố định cả ba mức: BW=40Mbps, OWD=20ms → RTT ≈ 40ms.
#   low    : jitter=0ms,  loss=0%
#   medium : jitter=4ms,  loss=1.5%
#   high   : jitter=16ms, loss=3%

set -euo pipefail

IFACE="${1:-eth0}"
SCENARIO="${2:-}"

clear_tc() {
    sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
}

show_tc() {
    echo "=== tc qdisc on $IFACE ==="
    tc qdisc show dev "$IFACE"
}

case "$SCENARIO" in
    low)
        echo "[INFO] Apply LOW dynamicity on $IFACE"
        echo "       BW=40Mbps, OWD=20ms, jitter=0ms, loss=0%"
        echo "       RTT (nếu áp cả 2 đầu) = 2 × 20ms = ~40ms"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 40mbit burst 64kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 20ms loss 0%
        show_tc
        ;;
    medium)
        echo "[INFO] Apply MEDIUM dynamicity on $IFACE"
        echo "       BW=40Mbps, OWD=20ms, jitter=4ms, loss=1.5%"
        echo "       RTT trung tâm (nếu áp cả 2 đầu) = 2 × 20ms = ~40ms; jitter cấu hình 4ms mỗi chiều"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 40mbit burst 64kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 20ms 4ms distribution normal loss 1.5%
        show_tc
        ;;
    high)
        echo "[INFO] Apply HIGH dynamicity on $IFACE"
        echo "       BW=40Mbps, OWD=20ms, jitter=16ms, loss=3%"
        echo "       RTT trung tâm (nếu áp cả 2 đầu) = 2 × 20ms = ~40ms; jitter cấu hình 16ms mỗi chiều"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 40mbit burst 64kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 20ms 16ms distribution normal loss 3%
        show_tc
        ;;
    clear|reset)
        echo "[INFO] Clear tc on $IFACE"
        clear_tc
        show_tc
        ;;
    show)
        show_tc
        ;;
    *)
        echo "Usage:"
        echo "  $0 <iface> {low|medium|high|clear|show}"
        echo
        echo "QUAN TRỌNG: Chạy script này trên CẢ client VÀ server để RTT = 2 × OWD"
        echo
        echo "Examples:"
        echo "  [client]  $0 eth0 high   # thêm OWD 20ms outgoing"
        echo "  [server]  $0 eth0 high   # thêm OWD 20ms outgoing (= return path)"
        echo "  → RTT trung tâm đo được sẽ ≈ 40ms; high có jitter/loss lớn hơn"
        echo
        echo "  $0 eth0 clear   # xóa hết"
        exit 1
        ;;
esac
