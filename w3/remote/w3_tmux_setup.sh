#!/usr/bin/env bash
# w3_tmux_setup.sh — Remote helper: launch a 5-pane tmux session for W3 benchmarks.
#
# Pane 0 : interactive measurement target (line_echo / keystroke_latency)
# Pane 1 : periodic heartbeat (~5 lines/s)
# Pane 2 : burst stdout generator (high-volume background write)
# Pane 3 : frequent short commands (ls loop)
# Pane 4 : continuous log tail (file I/O background noise)
#
# The script ends with 'exec tmux attach', replacing this shell with the
# tmux client so that the SSH PTY carries pane-0 output directly.
# Pane-0 prints __W3_PANE0_READY__ once the attach is live.
set -euo pipefail

SESSION="w3bench"
LOGFILE="/tmp/w3_pane4.log"

# ── Clean up any stale session ────────────────────────────────────────────
tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"

rm -f "$LOGFILE"
touch "$LOGFILE"

# ── Pane 0: interactive shell (measurement target) ────────────────────────
tmux new-session -d -s "$SESSION" -n w3 "bash -l"

# ── Pane 1: periodic heartbeat ────────────────────────────────────────────
tmux split-window -h -t "$SESSION":0 \
  "bash -lc 'while true; do printf \"pane1 heartbeat %(%s)T\n\" -1; sleep 0.2; done'"

# ── Pane 2: burst stdout (high-volume background writes) ─────────────────
tmux split-window -v -t "$SESSION":0.0 \
  "bash -lc 'while true
             do
               for i in \$(seq 1 200); do
                 echo \"pane2 burst line \$i \$(date +%s%N)\"
               done
               sleep 0.2
             done'"

# ── Pane 3: frequent short commands ──────────────────────────────────────
tmux split-window -v -t "$SESSION":0.1 \
  "bash -lc 'while true
             do
               echo \"pane3 /etc snapshot \$(date +%s)\"
               ls /etc | head -n 25
               sleep 0.4
               clear
             done'"

# ── Pane 4 background writer + log tail ──────────────────────────────────
# Background writer process (survives this script)
(
  while true; do
    printf "pane4 log %s background-event\n" "$(date +%s%N)" >> "$LOGFILE"
    sleep 0.05
  done
) >/dev/null 2>&1 &

tmux split-window -v -t "$SESSION":0.2 \
  "bash -lc 'tail -f $LOGFILE'"

# ── Layout ────────────────────────────────────────────────────────────────
tmux select-layout   -t "$SESSION":0 tiled
tmux set-option      -t "$SESSION"   status off
tmux select-pane     -t "$SESSION":0.0

# ── Signal readiness from pane-0 once attach is live ─────────────────────
# We send the printf command to pane-0 *before* attaching.
# Bash in pane-0 will execute it immediately when the PTY is connected.
tmux send-keys -t "$SESSION":0.0 "printf '__W3_PANE0_READY__\\n'" Enter

# Replace this shell with the tmux client — from this point the SSH PTY
# carries pane-0 I/O and pexpect can read/write it directly.
exec tmux attach -t "$SESSION"