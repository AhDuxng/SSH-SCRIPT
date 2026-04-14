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
#   low    : BW=100Mbps, OWD=10ms,  jitter=0ms,  loss=0%   → RTT ≈ 20ms
#   medium : BW=40Mbps,  OWD=50ms,  jitter=4ms,  loss=1.5% → RTT ≈ 100ms ± 8ms
#   high   : BW=10Mbps,  OWD=100ms, jitter=16ms, loss=3%   → RTT ≈ 200ms ± 32ms

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
        echo "       BW=100Mbps, OWD=10ms, jitter=0ms, loss=0%"
        echo "       RTT (nếu áp cả 2 đầu) = 2 × 10ms = ~20ms"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 100mbit burst 32kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 10ms loss 0%
        show_tc
        ;;
    medium)
        echo "[INFO] Apply MEDIUM dynamicity on $IFACE"
        echo "       BW=40Mbps, OWD=50ms, jitter=4ms, loss=1.5%"
        echo "       RTT (nếu áp cả 2 đầu) = 2 × 50ms = ~100ms ± 8ms"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 40mbit burst 32kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 50ms 4ms distribution normal loss 1.5%
        show_tc
        ;;
    high)
        echo "[INFO] Apply HIGH dynamicity on $IFACE"
        echo "       BW=10Mbps, OWD=100ms, jitter=16ms, loss=3%"
        echo "       RTT (nếu áp cả 2 đầu) = 2 × 100ms = ~200ms ± 32ms"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 10mbit burst 32kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 100ms 16ms distribution normal loss 3%
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
        echo "  [client]  $0 eth0 high   # thêm OWD 100ms outgoing"
        echo "  [server]  $0 eth0 high   # thêm OWD 100ms outgoing (= return path)"
        echo "  → RTT đo được sẽ ≈ 200ms ± 32ms"
        echo
        echo "  $0 eth0 clear   # xóa hết"
        exit 1
        ;;
esac