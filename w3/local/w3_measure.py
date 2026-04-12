#!/usr/bin/env python3
import argparse
import csv
import datetime
import os
import random
import string
import sys
import time

import pexpect

def drain(child: pexpect.spawn, duration: float = 0.15) -> None:
    end = time.monotonic() + duration
    while time.monotonic() < end:
        try:
            chunk = child.read_nonblocking(size=4096, timeout=0.02)
            if chunk:
                end = max(end, time.monotonic() + 0.05)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass


def login_and_prepare(
    child: pexpect.spawn,
    password: str | None,
    prompt_regex: str,
) -> None:
    """Xử lý host-key confirmation, password prompt, và chờ shell prompt."""
    while True:
        idx = child.expect(
            [
                r"Are you sure you want to continue connecting \(yes/no(/\[fingerprint\])?\)\?",
                r"[Pp]assword:",
                prompt_regex,
                pexpect.TIMEOUT,
                pexpect.EOF,
            ],
            timeout=30,
        )

        if idx == 0:
            print("[login] Xác nhận host key → gửi 'yes'", flush=True)
            child.sendline("yes")
        elif idx == 1:
            print("[login] Yêu cầu password...", flush=True)
            if password is None:
                raise RuntimeError(
                    "Remote yêu cầu password nhưng chưa truyền --password"
                )
            child.sendline(password)
        elif idx == 2:
            print("[login] Đã vào shell remote.", flush=True)
            return
        elif idx == 3:
            continue
        else:  
            raise RuntimeError("SSH/SSH3 đóng kết nối sớm trong quá trình login")


def wait_exact_or_raise(
    child: pexpect.spawn,
    text: str,
    timeout: float,
    step_name: str,
) -> None:
    """Chờ chuỗi chính xác; in context debug rõ ràng nếu timeout/EOF."""
    try:
        child.expect_exact(text, timeout=timeout)
    except pexpect.TIMEOUT:
        tail = repr(child.before[-500:]) if child.before else "''"
        raise TimeoutError(
            f"[{step_name}] Timeout sau {timeout}s chờ {text!r}\n"
            f"  Output tail: {tail}"
        ) from None
    except pexpect.EOF:
        tail = repr(child.before[-500:]) if child.before else "''"
        raise EOFError(
            f"[{step_name}] EOF khi chờ {text!r}\n"
            f"  Output tail: {tail}"
        ) from None


def random_token(idx: int, token_len: int) -> str:
    """Tạo token có prefix index và suffix ngẫu nhiên."""
    alphabet = string.ascii_uppercase + string.digits
    rand_part = "".join(random.choices(alphabet, k=token_len))
    return f"T{idx:04d}_{rand_part}"

def main() -> None:
    parser = argparse.ArgumentParser(
        description="W3 line-echo latency benchmark (fixed for research)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--cmd", required=True,
        help='Lệnh kết nối, ví dụ: "ssh pi@192.168.8.102" hoặc lệnh ssh3 đầy đủ',
    )
    parser.add_argument("--password", default=None, help="Password SSH nếu cần")
    parser.add_argument(
        "--prompt-regex", default=r".*[$#] ?",
        help="Regex nhận dạng shell prompt ban đầu",
    )
    parser.add_argument(
        "--remote-setup", required=True,
        help="Đường dẫn script w3_tmux_setup.sh trên remote",
    )
    parser.add_argument("--samples",   type=int,   default=200,  help="Số sample đo")
    parser.add_argument("--token-len", type=int,   default=12,   help="Độ dài phần random của token")
    parser.add_argument("--warmup",    type=int,   default=10,   help="Số lần warm-up")
    parser.add_argument(
        "--proto", required=True,
        help="Nhãn giao thức, ví dụ: ssh2 | ssh3",
    )
    parser.add_argument(
        "--scenario", required=True,
        help="Nhãn kịch bản mạng, ví dụ: default | low | medium | high",
    )
    parser.add_argument("--output", required=True, help="Đường dẫn file CSV output")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="[F7] Ghi đè file output nếu đã tồn tại (mặc định: báo lỗi)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="In toàn bộ output SSH ra stdout để debug",
    )
    args = parser.parse_args()

    if os.path.exists(args.output) and not args.overwrite:
        print(
            f"[error] File output đã tồn tại: {args.output}\n"
            "  Dùng --overwrite để ghi đè, hoặc chọn tên khác.",
            file=sys.stderr,
        )
        sys.exit(1)

    outdir = os.path.dirname(args.output)
    if outdir:
        os.makedirs(outdir, exist_ok=True)

    child: pexpect.spawn | None = None

    try:
        print("[1/6] Spawn kết nối SSH/SSH3...", flush=True)
        child = pexpect.spawn(args.cmd, encoding="utf-8", timeout=30)
        child.delaybeforesend = 0  

        if args.verbose:
            child.logfile = sys.stdout

        print("[2/6] Đăng nhập vào remote...", flush=True)
        login_and_prepare(child, args.password, args.prompt_regex)

        print(f"[3/6] Chạy remote setup: bash {args.remote_setup}", flush=True)

        child.maxread = 65536
        child.searchwindowsize = None  
        child.sendline(f"bash {args.remote_setup}")

        print("[4/6] Chờ __W3_PANE0_READY__ ...", flush=True)
        wait_exact_or_raise(
            child,
            "__W3_PANE0_READY__",
            timeout=120,
            step_name="remote setup ready",
        )

        drain(child, duration=0.3)

        print(f"[5/6] Warm-up {args.warmup} lần...", flush=True)
        for i in range(args.warmup):
            
            wtoken = random_token(-(i + 1), args.token_len)
            drain(child, duration=0.05)
            child.send(wtoken + "\r")
            
            wait_exact_or_raise(
                child,
                wtoken + "\r\n",
                timeout=15,
                step_name=f"warmup #{i}",
            )
            drain(child, duration=0.05)
            print(f"  warm-up {i + 1}/{args.warmup} OK", flush=True)

        print(f"[6/6] Đo {args.samples} samples...", flush=True)
        rows: list[dict] = []
        fail_count = 0

        for i in range(args.samples):
            token = random_token(i, args.token_len)

            drain(child, duration=0.08)

            t0 = time.perf_counter()
            child.send(token + "\r")

            try:
                child.expect_exact(token + "\r\n", timeout=30)
                t1 = time.perf_counter()          
                latency_ms = (t1 - t0) * 1000.0
                ok = 1

            except pexpect.TIMEOUT:
                latency_ms = None
                ok = 0
                fail_count += 1
                tail = repr(child.before[-300:]) if child.before else "''"
                print(f"  [TIMEOUT] sample={i} token={token!r} tail={tail}", flush=True)

            except pexpect.EOF:

                raise RuntimeError(
                    f"Kết nối bị đóng ở sample {i} khi chờ token {token!r}"
                )

            rows.append(
                {
                    "sample":        i,
                    "proto":         args.proto,
                    "scenario":      args.scenario,
                    "token":         token,
                    "latency_ms":    latency_ms if latency_ms is not None else "",
                    "ok":            ok,
                    "timestamp_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds"),
                }
            )

            if (i + 1) % 10 == 0 or i == 0 or (i + 1) == args.samples:
                lat_str = f"{latency_ms:.2f}ms" if ok else "FAIL"
                print(
                    f"  [{i + 1:>4}/{args.samples}] fail={fail_count} last={lat_str}",
                    flush=True,
                )

        print(f"[write] Ghi kết quả → {args.output}", flush=True)
        fieldnames = ["sample", "proto", "scenario", "token",
                      "latency_ms", "ok", "timestamp_utc"]
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        success_count = args.samples - fail_count
        print(
            f"\n[done] samples={args.samples} ok={success_count} fail={fail_count}\n"
            f"       output={args.output}",
            flush=True,
        )

    finally:
        if child is not None and child.isalive():
            child.close(force=True)


if __name__ == "__main__":
    main()