#!/usr/bin/env bash
set -euo pipefail

IFACE="${1:-eth0}"
SCENARIO="${2:-}"

RATE="40mbit"
BURST="128kb"       
TBF_LATENCY="400ms"
NETEM_LIMIT="1000"

clear_tc() {
    sudo tc qdisc del dev "$IFACE" root 2>/dev/null || true
}

show_tc() {
    echo "=== tc qdisc statistics on $IFACE ==="
    tc -s qdisc show dev "$IFACE"
}

apply_network() {
    local jitter="$1"
    local loss="$2"

    clear_tc

    # Bandwidth bottleneck
    sudo tc qdisc add dev "$IFACE" root handle 1: \
        tbf rate "$RATE" burst "$BURST" latency "$TBF_LATENCY"

    # Delay / jitter / loss
    if [[ "$jitter" == "0ms" ]]; then
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: \
            netem limit "$NETEM_LIMIT" \
            delay 20ms \
            loss "$loss"
    else
        sudo tc qdisc add dev "$IFACE" parent 1:1 handle 10: \
            netem limit "$NETEM_LIMIT" \
            delay 20ms "$jitter" distribution normal \
            loss "$loss"
    fi

    show_tc
}

case "$SCENARIO" in
    low)
        apply_network "0ms" "0%"
        ;;
    medium)
        apply_network "4ms" "1.5%"
        ;;
    high)
        apply_network "16ms" "3%"
        ;;
    clear|reset)
        clear_tc
        show_tc
        ;;
    show)
        show_tc
        ;;
    *)
        echo "Usage: $0 <iface> {low|medium|high|clear|show}"
        exit 1
        ;;
esac