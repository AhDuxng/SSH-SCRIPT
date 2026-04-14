#!/usr/bin/env python3
import argparse
import csv
import os
import random
import re
import string
import sys
import time

import pexpect


ANSI_STRIP_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
    r"|[\r\n\x00\x08]"
)


def drain(child, duration=0.15):
    end = time.time() + duration
    while time.time() < end:
        try:
            child.read_nonblocking(size=4096, timeout=0.02)
        except Exception:
            pass


def strip_ansi(text):
    return ANSI_STRIP_RE.sub("", text)


def wait_marker_via_stream(child, marker, timeout):
    deadline = time.monotonic() + timeout
    raw_parts = []
    max_chars = 32768

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise pexpect.TIMEOUT(f"Timeout chờ marker: {marker!r}")

        try:
            chunk = child.read_nonblocking(size=4096, timeout=min(0.5, max(0.05, remaining)))
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            raise

        if not chunk:
            continue

        raw_parts.append(chunk)
        total = sum(len(x) for x in raw_parts)
        if total > max_chars:
            raw_parts = ["".join(raw_parts)[-max_chars:]]

        clean = strip_ansi("".join(raw_parts))
        if marker in clean:
            return clean


def login_and_prepare(child, password, prompt_regex):
    while True:
        idx = child.expect(
            [
                r"Are you sure you want to continue connecting \(yes/no(/\[fingerprint\])?\)\?",
                r"[Pp]assword:",
                prompt_regex,
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=20,
        )

        if idx == 0:
            print("[login] Host key chưa có, tự động gửi 'yes'...", flush=True)
            child.sendline("yes")
        elif idx == 1:
            print("[login] Remote yêu cầu password...", flush=True)
            if password is None:
                raise RuntimeError("Remote yêu cầu password nhưng chưa truyền --password")
            child.sendline(password)
        elif idx == 2:
            print("[login] Đã vào shell remote.", flush=True)
            return
        elif idx == 3:
            print("[login] Đang chờ password hoặc shell prompt...", flush=True)
            continue
        else:
            raise RuntimeError("SSH/SSH3 đóng kết nối sớm")


def wait_exact_or_raise(child, text, timeout, step_name):
    try:
        child.expect_exact(text, timeout=timeout)
    except pexpect.TIMEOUT:
        print(f"[error] Timeout ở bước: {step_name}", flush=True)
        print(f"[error] Đang chờ chuỗi: {text!r}", flush=True)
        print(f"[error] Output gần nhất: {child.before!r}", flush=True)
        raise
    except pexpect.EOF:
        print(f"[error] EOF ở bước: {step_name}", flush=True)
        print(f"[error] Output gần nhất: {child.before!r}", flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="W3 interactive benchmark")
    parser.add_argument("--cmd", required=True, help='Ví dụ: "ssh pi@192.168.8.102" hoặc lệnh ssh3 đầy đủ')
    parser.add_argument("--password", default=None, help="Password nếu cần")
    parser.add_argument(
        "--prompt-regex",
        default=r".*[$#] ?",
        help="Regex shell prompt ban đầu, ví dụ mặc định: .*[$#] ?",
    )
    parser.add_argument("--remote-setup", required=True, help="Đường dẫn script w3_tmux_setup.sh trên remote")
    parser.add_argument("--trials", type=int, default=30,
                        help="Số trial độc lập (mỗi trial = 1 kết nối SSH)")
    parser.add_argument("--samples-per-trial", type=int, default=100,
                        help="Số sample đo thực sự mỗi trial (sau khi bỏ warmup)")
    parser.add_argument("--warmup-samples", type=int, default=10)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--token-len", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=None)
    parser.add_argument(
        "--metric",
        choices=["keystroke_latency", "line_echo"],
        default="keystroke_latency",
    )
    parser.add_argument("--echo-timeout", type=float, default=20.0)
    parser.add_argument("--proto", required=True, help="Nhãn giao thức, ví dụ ssh2 hoặc ssh3")
    parser.add_argument("--scenario", required=True, help="Nhãn kịch bản mạng, ví dụ default/low/medium/high")
    parser.add_argument("--output", required=True, help="CSV output")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Hiện toàn bộ output remote/ssh ra màn hình để debug",
    )
    args = parser.parse_args()

    if args.samples is not None:
        args.samples_per_trial = args.samples
    if args.warmup is not None:
        args.warmup_samples = args.warmup

    if args.trials <= 0 or args.samples_per_trial <= 0 or args.token_len <= 0:
        raise SystemExit("--trials, --samples-per-trial và --token-len phải > 0")
    if args.warmup_samples < 0:
        raise SystemExit("--warmup-samples phải >= 0")

    outdir = os.path.dirname(args.output)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    print("[1/6] Spawn SSH/SSH3 command...", flush=True)
    child = pexpect.spawn(args.cmd, encoding="utf-8", timeout=30)
    child.delaybeforesend = 0

    if args.verbose:
        child.logfile = sys.stdout

    print("[2/6] Login vào remote...", flush=True)
    login_and_prepare(child, args.password, args.prompt_regex)

    print(f"[3/6] Chạy remote setup: bash {args.remote_setup}", flush=True)
    child.sendline(f"bash {args.remote_setup}")

    print("[4/6] Chờ remote báo __W3_PANE0_READY__ ...", flush=True)
    wait_exact_or_raise(
        child,
        "__W3_PANE0_READY__",
        timeout=30,
        step_name="wait remote setup ready",
    )

    time.sleep(2.0)
    drain(child, 0.3)

    print(
        f"[5/6] Chạy {args.trials} trial | warmup={args.warmup_samples} | samples={args.samples_per_trial} | metric={args.metric}...",
        flush=True,
    )

    rows = []
    fail_count = 0
    global_sample = 0
    alphabet = string.ascii_uppercase + string.digits

    for trial_id in range(1, args.trials + 1):
        print(f"[trial] {trial_id}/{args.trials}", flush=True)
        agent_state = trial_id

        for i in range(1, args.warmup_samples + 1):
            token = f"WARM{trial_id:03d}_{i:03d}"
            if args.metric == "line_echo":
                # Dùng printf để tạo server-side echo với prefix duy nhất.
                # Marker "W3E:<token>" chỉ xuất hiện khi server thực thi lệnh
                # → tránh nhầm với local PTY echo của SSH client.
                cmd = f"printf 'W3E:{token}\\n'"
                marker = f"W3E:{token}"
            else:
                cmd = f"OBS:{token}:{agent_state}"
                marker = cmd
            child.send(cmd + "\r")
            try:
                wait_marker_via_stream(child, marker, timeout=10)
            except Exception as exc:
                print(f"[warmup] FAIL trial={trial_id} idx={i}: {type(exc).__name__}", flush=True)
                break
            if args.metric == "keystroke_latency":
                agent_state = agent_state + 1 if agent_state % 2 == 0 else agent_state - 1
            drain(child, 0.05)

        for sample_id in range(1, args.samples_per_trial + 1):
            rand_part = "".join(random.choice(alphabet) for _ in range(args.token_len))
            token = f"T{trial_id:03d}_{sample_id:04d}_{rand_part}"

            # Drain 150ms để flush hết byte thừa từ sample trước.
            # 50ms không đủ khi mạng có OWD lớn (response cũ vẫn đang trên đường)
            drain(child, 0.15)
            latency_ms = ""
            ok = 0

            try:
                if args.metric == "line_echo":
                    # QUAN TRỌNG: KHÔNG gửi token trực tiếp mà dùng printf trên server.
                    # Lý do: SSH PTY echo trả về ký tự gõ ngay khi server nhận STDIN
                    # (sau 1 OWD). Nếu wait marker là chính token đó thì có thể
                    # khớp với PTY echo (1 OWD) thay vì stdout của lệnh (1 RTT).
                    # Marker "W3E:<token>" chỉ xuất hiện khi printf chạy xong
                    # trên server → đảm bảo đo đúng 1 RTT ứng với đường đi đầy đủ.
                    echo_cmd = f"printf 'W3E:{token}\\n'"
                    echo_marker = f"W3E:{token}"
                    t0 = time.perf_counter_ns()
                    child.send(echo_cmd + "\r")
                    wait_marker_via_stream(child, echo_marker, timeout=args.echo_timeout)
                    t1 = time.perf_counter_ns()
                    latency_ms = (t1 - t0) / 1e6
                else:
                    cmd_obs = f"OBS:{token}:{agent_state}"
                    t0 = time.perf_counter_ns()
                    child.send(cmd_obs + "\r")
                    clean_obs = wait_marker_via_stream(child, cmd_obs, timeout=args.echo_timeout)
                    t1 = time.perf_counter_ns()

                    # BUG FIX: trong raw string r"...", \\d là chuỗi ký tự backslash-d,
                    # không phải pattern digit. Phải dùng \d (một backslash trong regex).
                    m = re.search(rf"OBS:{re.escape(token)}:(-?\d+)", clean_obs)
                    if m:
                        obs = int(m.group(1))
                    else:
                        obs = agent_state

                    if obs % 2 == 0:
                        action = "INC"
                        next_state = obs + 1
                    else:
                        action = "DEC"
                        next_state = obs - 1

                    cmd_act = f"ACT:{token}:{action}:{next_state}"
                    t2 = time.perf_counter_ns()
                    child.send(cmd_act + "\r")
                    wait_marker_via_stream(child, cmd_act, timeout=args.echo_timeout)
                    t3 = time.perf_counter_ns()

                    latency_ms = (((t1 - t0) / 1e6) + ((t3 - t2) / 1e6)) / 2.0
                    agent_state = next_state
                ok = 1
            except Exception:
                fail_count += 1
                snippet = repr(child.before[-300:]) if child.before else "''"
                print(
                    f"[sample] FAIL trial={trial_id} sample={sample_id} token={token!r} tail={snippet}",
                    flush=True,
                )

            rows.append(
                {
                    "sample": global_sample,
                    "metric": args.metric,
                    "trial_id": trial_id,
                    "sample_id": sample_id,
                    "is_warmup": 0,
                    "proto": args.proto,
                    "scenario": args.scenario,
                    "token": token,
                    "latency_ms": latency_ms,
                    "ok": ok,
                }
            )
            global_sample += 1

            if sample_id % 10 == 0 or sample_id == 1 or sample_id == args.samples_per_trial:
                print(
                    f"[progress] trial={trial_id}/{args.trials} sample={sample_id}/{args.samples_per_trial} | fail={fail_count}",
                    flush=True,
                )

    print("[write] Ghi CSV...", flush=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "sample",
                "metric",
                "trial_id",
                "sample_id",
                "is_warmup",
                "proto",
                "scenario",
                "token",
                "latency_ms",
                "ok",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. fail_count={fail_count}, output={args.output}", flush=True)

    child.close(force=True)


if __name__ == "__main__":
    main()