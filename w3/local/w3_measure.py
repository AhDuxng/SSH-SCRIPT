#!/usr/bin/env python3
import argparse
import csv
import os
import random
import re
import shlex
import string
import sys
import time
from typing import List, Optional, Tuple

import pexpect

ANSI_STRIP_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
    r"|[\r\n\x00\x08]"
)


def strip_ansi(text: str) -> str:
    return ANSI_STRIP_RE.sub("", text)


def wait_marker_via_stream(child: pexpect.spawn, marker: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    raw_parts: List[str] = []
    max_chars = 32768

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise pexpect.TIMEOUT(f"Timeout waiting for marker: {marker!r}")

        try:
            chunk = child.read_nonblocking(size=4096, timeout=min(0.5, max(0.05, remaining)))
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            raise RuntimeError(f"EOF while waiting for marker. raw={raw_parts}")

        if not chunk:
            continue

        raw_parts.append(chunk)
        total = sum(len(x) for x in raw_parts)
        if total > max_chars:
            raw_parts = ["".join(raw_parts)[-max_chars:]]

        clean = strip_ansi("".join(raw_parts))
        if marker in clean:
            return clean


def wait_ack_via_stream(child: pexpect.spawn, ack_marker: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    raw_parts: List[str] = []
    max_chars = 32768

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise pexpect.TIMEOUT(f"ACK not received within {timeout_s:.1f}s: {ack_marker!r}")

        try:
            chunk = child.read_nonblocking(size=4096, timeout=min(0.5, max(0.05, remaining)))
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            raise RuntimeError(f"EOF while waiting for ACK. raw={raw_parts}")

        if not chunk:
            continue

        raw_parts.append(chunk)
        total = sum(len(x) for x in raw_parts)
        if total > max_chars:
            raw_parts = ["".join(raw_parts)[-max_chars:]]

        if ack_marker in strip_ansi("".join(raw_parts)):
            return


def login_and_prepare(child: pexpect.spawn, password: Optional[str], prompt_regex: str, timeout_s: float) -> None:
    while True:
        idx = child.expect(
            [
                r"Are you sure you want to continue connecting \(yes/no(?:/\[fingerprint\])?\)\?",
                r"Do you want to add this certificate to .*known_hosts \(yes/no\)\?",
                r"[Pp]assword:",
                prompt_regex,
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=timeout_s,
        )

        if idx == 0 or idx == 1:
            print("[login] Auto-accepting host key/cert ('yes')...", flush=True)
            child.sendline("yes")
        elif idx == 2:
            print("[login] Remote requested password...", flush=True)
            if password is None:
                raise RuntimeError("Remote requested password but --password was not provided.")
            child.sendline(password)
        elif idx == 3:
            print("[login] Reached remote shell.", flush=True)
            return
        elif idx == 4:
            print("[login] Waiting for shell prompt...", flush=True)
            continue
        else:
            raise RuntimeError("SSH connection closed unexpectedly.")


def start_helper(child: pexpect.spawn, trial_id: int, timeout_s: float) -> Tuple[str, str]:
    ready = f"W3RDY{trial_id:03d}Z"
    ack = f"W3ACK{trial_id:03d}A"
    bye = f"W3BYE{trial_id:03d}Z"
    helper = (
        "import os,sys\n"
        "rdy=os.environ['W3R']\n"
        "ack=os.environ['W3A']\n"
        "bye=os.environ['W3B']\n"
        "print(rdy,flush=True)\n"
        "for ln in sys.stdin:\n"
        "    ln=ln.rstrip('\\n')\n"
        "    if ln=='W3EXIT':\n"
        "        print(bye,flush=True)\n"
        "        break\n"
        "    print(ack+ln,flush=True)\n"
    )
    cmd = (
        f"W3R={shlex.quote(ready)} "
        f"W3A={shlex.quote(ack)} "
        f"W3B={shlex.quote(bye)} "
        f"python3 -u -c {shlex.quote(helper)}"
    )
    child.sendline(cmd)
    try:
        child.expect_exact(ready, timeout=timeout_s)
    except Exception as exc:
        raise RuntimeError(f"Failed to start python helper: {exc}")
    return ack, bye


def stop_helper(child: pexpect.spawn, bye: str, timeout_s: float, prompt: str) -> None:
    child.sendline("W3EXIT")
    try:
        child.expect_exact(bye, timeout=timeout_s)
    except Exception:
        pass
    child.sendline("printf 'W3BACK\\n'")
    try:
        child.expect_exact("W3BACK", timeout=timeout_s)
        child.expect_exact(prompt, timeout=timeout_s)
    except Exception:
        pass


def measure_echo(
    child: pexpect.spawn, trial_id: int, sample_id: int, token_len: int, ack_prefix: str, timeout_s: float
) -> Tuple[str, float]:
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=token_len))
    token = f"E{trial_id:03d}_{sample_id:04d}_{rand_part}"
    ack_marker = ack_prefix + token

    t0 = time.perf_counter_ns()
    child.sendline(token)
    wait_ack_via_stream(child, ack_marker, timeout_s)
    t1 = time.perf_counter_ns()

    return token, (t1 - t0) / 1e6


def measure_keystroke_latency(
    child: pexpect.spawn, trial_id: int, sample_id: int, token_len: int, state: int, timeout_s: float
) -> Tuple[str, float, int]:
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=token_len))
    token = f"K{trial_id:03d}_{sample_id:04d}_{rand_part}"

    obs_marker = f"__W3OBS__ {token} "
    cmd1 = f"printf '__W3OBS__ {token} %d\\n' $(({state}*3+7))"
    
    t0 = time.perf_counter_ns()
    child.sendline(cmd1)
    clean_obs = wait_marker_via_stream(child, obs_marker, timeout_s)
    t1 = time.perf_counter_ns()

    m = re.search(rf"__W3OBS__\s+{re.escape(token)}\s+(-?\d+)", clean_obs)
    if not m:
        raise RuntimeError(f"Cannot parse observation for token={token}")
    obs = int(m.group(1))

    if obs % 2 == 0:
        action = "INC"
        next_state = obs + 1
    else:
        action = "DEC"
        next_state = obs - 1

    act_marker = f"__W3ACT__ {token} {action} {next_state}"
    cmd2 = f"printf '__W3ACT__ {token} {action} {next_state}\\n'"
    
    t2 = time.perf_counter_ns()
    child.sendline(cmd2)
    wait_marker_via_stream(child, act_marker, timeout_s)
    t3 = time.perf_counter_ns()

    lat1_ms = (t1 - t0) / 1e6
    lat2_ms = (t3 - t2) / 1e6
    return token, (lat1_ms + lat2_ms) / 2.0, next_state


def main():
    parser = argparse.ArgumentParser(description="W3 interactive benchmark")
    parser.add_argument("--cmd", required=True, help="SSH/SSH3 connection command")
    parser.add_argument("--password", default=None, help="Password if needed")
    parser.add_argument(
        "--prompt-regex",
        default=r".*[$#] ?",
        help="Initial shell prompt regex",
    )
    parser.add_argument("--remote-setup", required=True, help="Path to w3_tmux_setup.sh on remote")
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--samples-per-trial", type=int, default=100)
    parser.add_argument("--warmup-samples", type=int, default=10)
    
    # Retro-compatibility aliases
    parser.add_argument("--samples", type=int, default=None, help="Alias for --samples-per-trial")
    parser.add_argument("--warmup", type=int, default=None, help="Alias for--warmup-samples")
    
    parser.add_argument("--token-len", type=int, default=6)
    parser.add_argument(
        "--metric",
        choices=["keystroke_latency", "line_echo"],
        default="keystroke_latency",
    )
    parser.add_argument("--timeout", type=float, default=45.0, help="pexpect spawn timeout")
    parser.add_argument("--echo-timeout", type=float, default=45.0, help="ACK wait timeout")
    parser.add_argument("--proto", required=True, help="Protocol label")
    parser.add_argument("--scenario", required=True, help="Scenario label")
    parser.add_argument("--output", required=True, help="CSV output path")
    parser.add_argument("--verbose", action="store_true", help="Print remote streams")

    args = parser.parse_args()

    if args.samples is not None:
        args.samples_per_trial = args.samples
    if args.warmup is not None:
        args.warmup_samples = args.warmup

    if args.trials <= 0 or args.samples_per_trial <= 0 or args.token_len <= 0:
        raise SystemExit("--trials, --samples-per-trial, and --token-len must be > 0")
    if args.warmup_samples < 0:
        raise SystemExit("--warmup-samples must be >= 0")

    outdir = os.path.dirname(args.output)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    print("[1/6] Spawn connection...", flush=True)
    child = pexpect.spawn(args.cmd, encoding="utf-8", codec_errors="ignore", timeout=args.timeout, maxread=65535)
    child.delaybeforesend = 0

    if args.verbose:
        child.logfile = sys.stdout

    print("[2/6] Login to remote...", flush=True)
    login_and_prepare(child, args.password, args.prompt_regex, timeout_s=args.timeout)

    print(f"[3/6] Running remote setup: bash {args.remote_setup}", flush=True)
    child.sendline(f"bash {args.remote_setup}")

    print("[4/6] Waiting for pane-0 ready signal...", flush=True)
    try:
        child.expect_exact("__W3_PANE0_READY__", timeout=args.timeout)
    except Exception as exc:
        raise RuntimeError(f"Timeout waiting for pane-0 ready. {child.before}") from exc

    # ---------------------------------------------------------------------------
    # Configure inner shell — identical sequence to benchmark.py _open_session()
    # ---------------------------------------------------------------------------
    prompt = "__W3PROMPT__"
    setup_marker = "__W3SETUP__"
    child.sendline(
        "unset PROMPT_COMMAND 2>/dev/null || true; "
        "bind 'set enable-bracketed-paste off' 2>/dev/null || true; "
        f"export PS1={shlex.quote(prompt)}; "
        f"printf '{setup_marker}\\n'"
    )
    try:
        child.expect_exact(setup_marker, timeout=args.timeout)
        child.expect_exact(prompt, timeout=args.timeout)
    except Exception as exc:
        raise RuntimeError(f"Failed to configure inner shell: {exc}")
    # stty: disable echo and set PTY size (matches benchmark.py)
    child.sendline(
        "stty -echo -echoctl cols 220 rows 50 2>/dev/null || true"
    )
    try:
        child.expect_exact(prompt, timeout=args.timeout)
    except Exception:
        pass

    time.sleep(1.0)
    print(
        f"[5/6] Measuring {args.trials} trials | warmup={args.warmup_samples} | samples={args.samples_per_trial} | metric={args.metric}...",
        flush=True,
    )

    rows = []
    fail_count = 0
    global_sample = 0

    for trial_id in range(1, args.trials + 1):
        print(f"[trial] {trial_id}/{args.trials}", flush=True)
        
        ack_prefix = None
        bye_marker = None
        if args.metric == "line_echo":
            try:
                ack_prefix, bye_marker = start_helper(child, trial_id, timeout_s=args.timeout)
            except Exception as exc:
                print(f"[setup_fail] trial={trial_id} exc={exc}", flush=True)
                continue

        agent_state = trial_id

        # Warmup
        for i in range(1, args.warmup_samples + 1):
            sid = -i
            ok = 0
            lat = 0.0
            tok = ""
            err_type = ""
            err_msg = ""
            try:
                if args.metric == "keystroke_latency":
                    tok, lat, agent_state = measure_keystroke_latency(
                        child, trial_id, sid, args.token_len, agent_state, args.echo_timeout
                    )
                    print(f"[{args.proto:>4}/key  warm {i:>3}/{args.warmup_samples}]  {lat:.2f} ms", flush=True)
                else:
                    tok, lat = measure_echo(
                        child, trial_id, sid, args.token_len, ack_prefix, args.echo_timeout
                    )
                    print(f"[{args.proto:>4}/echo warm {i:>3}/{args.warmup_samples}]  {lat:.2f} ms", flush=True)
                ok = 1
            except Exception as exc:
                fail_count += 1
                err_type = type(exc).__name__
                err_msg = str(exc)
                print(f"[{args.proto:>4}/      warm {sid}     ]  FAIL  {err_type}", flush=True)
                break # abort trial on warmup fail

            rows.append({
                "sample": global_sample,
                "metric": args.metric,
                "trial_id": trial_id,
                "sample_id": sid,
                "is_warmup": 1,
                "proto": args.proto,
                "scenario": args.scenario,
                "token": tok,
                "latency_ms": lat if ok else "",
                "ok": ok,
                "error_type": err_type,
                "error_message": err_msg,
            })
            global_sample += 1

        # Measurement
        for sid in range(1, args.samples_per_trial + 1):
            ok = 0
            lat = 0.0
            tok = ""
            err_type = ""
            err_msg = ""
            try:
                if args.metric == "keystroke_latency":
                    tok, lat, agent_state = measure_keystroke_latency(
                        child, trial_id, sid, args.token_len, agent_state, args.echo_timeout
                    )
                    print(f"[{args.proto:>4}/key  meas {sid:>3}/{args.samples_per_trial}]  {lat:.2f} ms", flush=True)
                else:
                    tok, lat = measure_echo(
                        child, trial_id, sid, args.token_len, ack_prefix, args.echo_timeout
                    )
                    print(f"[{args.proto:>4}/echo meas {sid:>3}/{args.samples_per_trial}]  {lat:.2f} ms", flush=True)
                ok = 1
            except Exception as exc:
                fail_count += 1
                err_type = type(exc).__name__
                err_msg = str(exc)
                print(f"[{args.proto:>4}/      meas {sid:>3}     ]  FAIL  {err_type}", flush=True)
            
            rows.append({
                "sample": global_sample,
                "metric": args.metric,
                "trial_id": trial_id,
                "sample_id": sid,
                "is_warmup": 0,
                "proto": args.proto,
                "scenario": args.scenario,
                "token": tok,
                "latency_ms": lat if ok else "",
                "ok": ok,
                "error_type": err_type,
                "error_message": err_msg,
            })
            global_sample += 1

        if bye_marker is not None:
            stop_helper(child, bye_marker, timeout_s=args.timeout, prompt=prompt)

    print("[6/6] Writing CSV...", flush=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample", "metric", "trial_id", "sample_id", "is_warmup",
                "proto", "scenario", "token", "latency_ms", "ok", 
                "error_type", "error_message"
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. fail_count={fail_count}, output={args.output}", flush=True)
    child.close(force=True)


if __name__ == "__main__":
    main()