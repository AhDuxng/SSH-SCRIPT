# W4 – Interactive under Background Workloads

Implementation of sections 4–7 in `Thiết kế thí nghiệm.pdf`. The foreground
workload types the same deterministic 100-character C source used by W3 into
Vim or Nano. Background workloads remain active for the whole measured typing
interval.

## Scenarios

```text
W4-CMD
 ├─ interactive_0: W3 editor workload
 └─ command_0:     W1 command sequence repeated continuously

W4-OUTPUT
 ├─ interactive_0: W3 editor workload
 └─ output_0:      cat deterministic 1 MiB payload repeatedly

W4-MIX
 ├─ interactive_0
 ├─ command_0
 └─ output_0
```

The W1 sequence is `ls`, `df`, `free`, `ps`, and `uptime`; after command five,
the background worker starts command one again. The W2-style payload is exactly
1,048,576 bytes, 256 fixed 4,096-byte lines, and SHA-256
`51a7200cd10e343f430ab6acb2b0e67b73adfafe5e07450dbc305d18bdfc2504`.

## Transport topology

SSH opens one ControlMaster TCP connection per trial and one real SSH session
channel for each logical role. SSH3 calls `Dial` once, creates one conversation,
and opens one real QUIC bidirectional stream for each role. The audit records
every SSH3 StreamID and the shared conversation StreamID.

**Mosh is not evaluated in W4.** Its background scenarios require a workload to
run concurrently with the editor, and a single terminal session cannot do that
while keystroke latency stays measurable: both processes write to the same PTY,
which interleaves their bytes and breaks the cursor parser. Mosh is evaluated in
W1, W2 and W3 with the single-workload scenario, where the comparison is sound.

## Timing

Every trial creates a new connection, opens all roles, waits for READY, warms up
for five seconds, clears old terminal state, then releases one start barrier.
Warm-up and setup are excluded from workload latency.

For every interactive character:

```text
keystroke_latency = client VT100 render timestamp - client send timestamp
```

The parser accepts a render only at the cursor cell captured immediately before
the key. A latency above one second is a stall; no verified render within two
seconds is a timeout. Characters are spaced by 0.2 seconds. `MOSH_PREDICT` is
forced to `always`, and Mosh results are labelled `local_prediction`.

Background SSH/SSH3 latency is measured from the client sending the framed real
command until its last output/marker is observed by the client. Mosh background
latency is explicitly labelled client-observed start-marker to completion-marker
because Mosh exposes no independent background input stream.

For dynamic W1 commands over SSH/SSH3, the server executes each command exactly
once into a temporary file, records that invocation's byte count and SHA-256,
then sends the same bytes on the measured channel/QUIC stream. The client checks
its received bytes and hash against those values; the command is not executed a
second time to manufacture an unstable reference.

After the timed interval, the editor saves and exits. The same workload stream
prints the file as short indexed hex chunks that fit inside a Mosh tmux pane;
the client reconstructs it and compares all 100 bytes with the probe. No second
SSH connection is used to retrieve the result. Before saving, the client resets
its final-output capture; after the editor exits, the server clears the viewport
and keeps the indexed markers stable longer than the client verification timeout.
This prevents old editor redraws or a partially visible marker from being
mistaken for the saved file.

## Results

- `keystrokes.csv`: one row per real interactive character.
- `background.csv`: one row per completed/partial background command or transfer.
- `streams.csv`: per-role latency, completion, timeout, and completeness.
- `trials.csv`: connection and trial summary.
- `stream_audit.csv`: topology proof for every logical role.
- `scenario_summary.csv`: interactive Mean/Median/P95/P99 and reliability.
- `background_summary.csv`: background completion and output completeness.
- `ssh3_vs_ssh.csv`: SSH3/SSH interactive median checks.

`tools/verify_mux.py` checks the exact role set required by each scenario and
requires every background role to contain completed samples. Interactive
verification additionally requires all 100 keystrokes and the exact saved
100-byte editor file.

Runner không tạo terminal log riêng.

See `run.md` for deployment, smoke test, full run, and plotting commands.
