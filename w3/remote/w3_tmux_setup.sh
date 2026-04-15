#!/usr/bin/env bash
set -euo pipefail

SESSION="w3bench"
LOGFILE="/tmp/w3_pane4.log"

# Kill existing session if present
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

rm -f "$LOGFILE"
touch "$LOGFILE"

# ---------------------------------------------------------------------------
# Pane 0: interactive shell (measurement target)
# ---------------------------------------------------------------------------
# The shell is started detached. The benchmark client will attach to this
# session at the end of this script via 'exec tmux attach'.  We do NOT print
# the readiness marker here; we print it *after* attach so that it appears
# in the live PTY stream that pexpect is reading.
tmux new-session -d -s "$SESSION" -n w3 "bash -l"

# ---------------------------------------------------------------------------
# Pane 1-4: background noise (load the server to stress-test multiplexing)
# ---------------------------------------------------------------------------

# Pane 1: periodic small output (heartbeat)
tmux split-window -h -t "$SESSION":0 \
  "bash -lc 'while true; do printf \"pane1 heartbeat %(%s)T\n\" -1; sleep 0.2; done'"

# Pane 2: burst stdout (high-volume background write)
tmux split-window -v -t "$SESSION":0.0 \
  "bash -lc 'while true; do for i in \$(seq 1 200); do echo \"pane2 burst line \$i \$(date +%s%N)\"; done; sleep 0.2; done'"

# Pane 3: command loop (frequent short commands)
tmux split-window -v -t "$SESSION":0.1 \
  "bash -lc 'while true; do echo \"pane3 /etc snapshot \$(date +%s)\"; ls /etc | head -n 25; sleep 0.4; clear; done'"

# Pane 4: log tail (continuous file I/O)
(
  while true; do
    echo "pane4 log $(date +%s%N) background-event" >> "$LOGFILE"
    sleep 0.05
  done
) >/dev/null 2>&1 &

tmux split-window -v -t "$SESSION":0.2 \
  "bash -lc 'tail -f $LOGFILE'"

# Layout and cosmetics
tmux select-layout -t "$SESSION":0 tiled
tmux set-option -t "$SESSION" status off
tmux select-pane -t "$SESSION":0.0

# ---------------------------------------------------------------------------
# Signal readiness.
#
# 'exec tmux attach' replaces this shell process with the tmux client.
# From that moment the SSH PTY carries pane-0 output.  We schedule the
# marker to be printed by pane-0's bash *after* the attach is live, by
# sending a key sequence to the pane before attaching.  Pane-0's bash will
# execute the printf once the attach PTY is open and pexpect can read it.
# ---------------------------------------------------------------------------
tmux send-keys -t "$SESSION":0.0 "printf '__W3_PANE0_READY__\\n'" Enter

exec tmux attach -t "$SESSION"