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
            child.sendline("yes")
        elif idx == 1:
            if password is None:
                raise RuntimeError("Remote yêu cầu password nhưng chưa truyền --password")
            child.sendline(password)
        elif idx == 2:
            return
        elif idx == 3:
            continue
        else:
            raise RuntimeError("SSH/SSH3 đóng kết nối sớm")


def main():
    parser = argparse.ArgumentParser(description="W3 line echo benchmark with 5 tmux panes")
    parser.add_argument("--cmd", required=True, help='Ví dụ: "ssh pi@192.168.8.102" hoặc lệnh ssh3 đầy đủ')
    parser.add_argument("--password", default=None, help="Password nếu cần")
    parser.add_argument("--prompt-regex", default=r"[$#] ", help="Regex shell prompt ban đầu")
    parser.add_argument("--remote-setup", required=True, help="Đường dẫn script w3_tmux_setup.sh trên remote")
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--token-len", type=int, default=12)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--proto", required=True, help="Nhãn giao thức, ví dụ ssh2 hoặc ssh3")
    parser.add_argument("--scenario", required=True, help="Nhãn kịch bản mạng, ví dụ default/low/medium/high")
    parser.add_argument("--output", required=True, help="CSV output")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    child = pexpect.spawn(args.cmd, encoding="utf-8", timeout=30)
    child.delaybeforesend = 0

    login_and_prepare(child, args.password, args.prompt_regex)

    child.sendline(f"bash {args.remote_setup}")

    # Chờ pane 0 sẵn sàng
    child.expect_exact("__W3_PANE0_READY__", timeout=30)
    time.sleep(2.0)
    drain(child, 0.3)

    # Warm-up
    for i in range(args.warmup):
        token = f"WARM{i:03d}"
        child.send(token + "\r")
        child.expect_exact(token, timeout=10)
        drain(child, 0.05)

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

    # Ghi CSV
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sample", "proto", "scenario", "token", "latency_ms", "ok"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. fail_count={fail_count}, output={args.output}")

    # Không cần cleanup từ local; lần chạy sau remote setup sẽ tự kill session cũ
    child.close(force=True)


if __name__ == "__main__":
    main()