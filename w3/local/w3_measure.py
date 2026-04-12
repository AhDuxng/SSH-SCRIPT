#!/usr/bin/env python3
import argparse
import csv
import os
import random
import string
import sys
import time

import pexpect


def drain(child, duration=0.15):
    end = time.time() + duration
    while time.time() < end:
        try:
            child.read_nonblocking(size=4096, timeout=0.02)
        except Exception:
            pass


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
    parser = argparse.ArgumentParser(description="W3 line echo benchmark with 5 tmux panes")
    parser.add_argument("--cmd", required=True, help='Ví dụ: "ssh pi@192.168.8.102" hoặc lệnh ssh3 đầy đủ')
    parser.add_argument("--password", default=None, help="Password nếu cần")
    parser.add_argument(
        "--prompt-regex",
        default=r".*[$#] ?",
        help="Regex shell prompt ban đầu, ví dụ mặc định: .*[$#] ?",
    )
    parser.add_argument("--remote-setup", required=True, help="Đường dẫn script w3_tmux_setup.sh trên remote")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--token-len", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--proto", required=True, help="Nhãn giao thức, ví dụ ssh2 hoặc ssh3")
    parser.add_argument("--scenario", required=True, help="Nhãn kịch bản mạng, ví dụ default/low/medium/high")
    parser.add_argument("--output", required=True, help="CSV output")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Hiện toàn bộ output remote/ssh ra màn hình để debug",
    )
    args = parser.parse_args()

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

    print(f"[5/6] Warm-up {args.warmup} lần...", flush=True)
    for i in range(args.warmup):
        token = f"WARM{i:03d}"
        child.send(token + "\r")
        wait_exact_or_raise(
            child,
            token,
            timeout=10,
            step_name=f"warmup #{i}",
        )
        print(f"[warmup] OK {i + 1}/{args.warmup}", flush=True)
        drain(child, 0.05)

    print(f"[6/6] Đo {args.samples} samples...", flush=True)

    rows = []
    fail_count = 0
    alphabet = string.ascii_uppercase + string.digits

    for i in range(args.samples):
        rand_part = "".join(random.choice(alphabet) for _ in range(args.token_len))
        token = f"T{i:04d}_{rand_part}"

        drain(child, 0.05)
        t0 = time.perf_counter()
        child.send(token + "\r")

        try:
            child.expect_exact(token, timeout=10)
            t1 = time.perf_counter()
            latency_ms = (t1 - t0) * 1000.0
            ok = 1
        except Exception:
            latency_ms = ""
            ok = 0
            fail_count += 1
            print(f"[sample {i}] FAIL token={token!r}", flush=True)
            print(f"[sample {i}] Last output: {child.before!r}", flush=True)

        rows.append(
            {
                "sample": i,
                "proto": args.proto,
                "scenario": args.scenario,
                "token": token,
                "latency_ms": latency_ms,
                "ok": ok,
            }
        )

        if (i + 1) % 10 == 0 or i == 0 or (i + 1) == args.samples:
            print(
                f"[progress] {i + 1}/{args.samples} | fail={fail_count}",
                flush=True,
            )

    print("[write] Ghi CSV...", flush=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sample", "proto", "scenario", "token", "latency_ms", "ok"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. fail_count={fail_count}, output={args.output}", flush=True)

    child.close(force=True)


if __name__ == "__main__":
    main()