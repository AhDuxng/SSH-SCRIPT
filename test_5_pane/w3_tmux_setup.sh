#!/usr/bin/env bash
set -euo pipefail

SESSION="${1:-w3bench5}"
LOGFILE="/tmp/w3_pane4_5pane.log"

# Clean old session if it exists.
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "[tmux] Killing existing session: $SESSION"
    tmux kill-session -t "$SESSION"
fi

rm -f "$LOGFILE"
touch "$LOGFILE"

echo "[tmux] Creating session '$SESSION' with 5 panes..."

# Pane 0: measurement target.
tmux new-session -d -s "$SESSION" -n w3 "bash -l"

# Pane 1: periodic heartbeat (~5 lines/s).
tmux split-window -h -t "${SESSION}:0" \
    "bash -lc 'while true; do
        printf \"[pane1|heartbeat] %(%Y-%m-%dT%H:%M:%S)T ts=%(%s)T\\n\" -1 -1
        sleep 0.2
    done'"

# Pane 2: burst stdout (~750 lines/s).
tmux split-window -v -t "${SESSION}:0.0" \
    "bash -lc 'while true; do
        for i in \$(seq 1 150); do
            echo \"[pane2|burst] line=\$i ts=\$(date +%s%N)\"
        done
        sleep 0.15
    done'"

# Pane 3: short command loop.
tmux split-window -v -t "${SESSION}:0.1" \
    "bash -lc 'while true; do
        echo \"[pane3|cmd] \$(date +%T) listing /etc...\"
        ls /etc | head -n 20
        sleep 0.4
        clear
    done'"

# Pane 4: background writer + tail -f.
tmux split-window -v -t "${SESSION}:0.2" \
    "bash -lc '(while true; do
        printf \"[pane4|writer] %s background-event\\n\" \"\$(date +%s%N)\" >> \"${LOGFILE}\"
        sleep 0.05
    done) &
    echo \"[pane4] Writer PID=\$! started, tailing: ${LOGFILE}\"
    tail -f \"${LOGFILE}\"'"

# Layout and options.
tmux select-layout -t "${SESSION}:0" tiled
tmux set-option -t "$SESSION" prefix C-b
tmux set-option -t "$SESSION" prefix2 None
tmux set-option -t "$SESSION" status off
tmux select-pane -t "${SESSION}:0.0"

# Ready marker for pane 0.
tmux send-keys -t "${SESSION}:0.0" \
    "printf '__W3_5PANE_PANE0_READY__\\n'" Enter

echo "[tmux] Session '$SESSION' ready. Panes:"
echo "  Pane 0 -> measurement target (interactive shell)"
echo "  Pane 1 -> heartbeat  (~5 lines/s)"
echo "  Pane 2 -> burst      (~750 lines/s)"
echo "  Pane 3 -> ls-loop    (~clear+list every 0.4 s)"
echo "  Pane 4 -> log-writer (~20 events/s) + tail -f"
echo ""

if [ -z "${NO_ATTACH:-}" ]; then
    echo "Attaching to session..."
    exec tmux attach -t "$SESSION"
else
    echo "[tmux] NO_ATTACH=1: session running detached."
fi
