#!/usr/bin/env bash
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
        echo "       BW=100Mbps, OWD=10ms, variation=0, loss=0%"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 100mbit burst 32kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 10ms loss 0%
        show_tc
        ;;
    medium)
        echo "[INFO] Apply MEDIUM dynamicity on $IFACE"
        echo "       BW=40Mbps, OWD=50ms, variation=8%, loss=1.5%"
        echo "       => jitter = 4ms"
        clear_tc
        sudo tc qdisc add dev "$IFACE" root handle 1: tbf rate 40mbit burst 32kbit latency 400ms
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: netem delay 50ms 4ms distribution normal loss 1.5%
        show_tc
        ;;
    high)
        echo "[INFO] Apply HIGH dynamicity on $IFACE"
        echo "       BW=10Mbps, OWD=100ms, variation=16%, loss=3%"
        echo "       => jitter = 16ms"
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
        echo "Examples:"
        echo "  $0 eth0 low"
        echo "  $0 eth0 medium"
        echo "  $0 eth0 high"
        echo "  $0 eth0 clear"
        exit 1
        ;;
esac