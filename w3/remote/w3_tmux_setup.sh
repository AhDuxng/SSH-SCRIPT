#!/usr/bin/env bash
set -euo pipefail

SESSION="w3bench"
LOGFILE="/tmp/w3_pane4.log"

# Xóa session cũ nếu còn
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

rm -f "$LOGFILE"
touch "$LOGFILE"

# Pane 0: interactive shell
# Thay vì cat, ta chạy bash tương tác để benchmark script có thể gửi lệnh
tmux new-session -d -s "$SESSION" -n w3 "bash"
tmux send-keys -t "$SESSION":0.0 "printf '__W3_TMUX_ATTACHED__\n'" Enter

# Pane 1: periodic output nhỏ nhưng liên tục (background noise)
tmux split-window -h -t "$SESSION":0 \
  "bash -lc 'while true; do printf \"pane1 heartbeat %(%s)T\n\" -1; sleep 0.2; done'"

# Pane 2: stdout burst lớn, lặp vô hạn (background noise)
tmux split-window -v -t "$SESSION":0.0 \
  "bash -lc 'while true; do for i in \$(seq 1 200); do echo \"pane2 burst line \$i \$(date +%s%N)\"; done; sleep 0.2; done'"

# Pane 3: command loop có output nhìn thấy được (background noise)
tmux split-window -v -t "$SESSION":0.1 \
  "bash -lc 'while true; do echo \"pane3 /etc snapshot \$(date +%s)\"; ls /etc | head -n 25; sleep 0.4; clear; done'"

# Pane 4: synthetic log tail (background noise)
(
  while true; do
    echo "pane4 log $(date +%s%N) background-event" >> "$LOGFILE"
    sleep 0.05
  done
) >/dev/null 2>&1 &

tmux split-window -v -t "$SESSION":0.2 \
  "bash -lc 'tail -f $LOGFILE'"

# Layout
tmux select-layout -t "$SESSION":0 tiled
tmux set-option -t "$SESSION" status off
tmux select-pane -t "$SESSION":0.0

# Attach — SSH session sẽ attach vào pane 0 (cat echo server)
exec tmux attach -t "$SESSION"