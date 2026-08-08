#!/usr/bin/env python3
import argparse
import os
import selectors
import signal
import subprocess
import sys
import time


# Đọc tham số cho writer duy nhất của workload và marker.
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--chunk", type=int, required=True)
    parser.add_argument("--interval", type=float, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    if min(args.rate, args.chunk) <= 0 or args.interval <= 0:
        parser.error("rate, chunk and interval must be positive")
    return args


# Ghi một marker và timestamp ngay trước lần write ra terminal.
def write_marker(label, sequence):
    payload = f"{label}{sequence}:{time.time_ns()}\n".encode("ascii")
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


# Dừng workload con kể cả khi shell command tạo thêm process.
def stop_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# Chờ đến hạn phát output nhưng vẫn chèn marker đúng lịch.
def wait_with_markers(deadline, next_marker, interval, label, sequence):
    while next_marker <= deadline:
        delay = next_marker - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        sequence += 1
        write_marker(label, sequence)
        next_marker += interval
    delay = deadline - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    return next_marker, sequence


# Lặp workload, giới hạn output nền và xen marker bằng cùng một stdout writer.
def main():
    args = parse_args()
    selector = selectors.DefaultSelector()
    process = None
    started = time.monotonic()
    next_marker = started
    sequence = 0
    paced_bytes = 0

    try:
        while True:
            while next_marker <= time.monotonic():
                sequence += 1
                write_marker(args.label, sequence)
                next_marker += args.interval

            if process is None:
                process = subprocess.Popen(
                    args.command,
                    shell=True,
                    executable="/bin/bash",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                selector.register(process.stdout, selectors.EVENT_READ)

            timeout = max(0.0, next_marker - time.monotonic())
            if not selector.select(timeout):
                sequence += 1
                write_marker(args.label, sequence)
                next_marker += args.interval
                continue

            data = os.read(process.stdout.fileno(), args.chunk)
            if not data:
                selector.unregister(process.stdout)
                exit_code = process.wait()
                process = None
                if exit_code != 0:
                    raise RuntimeError(f"workload exited with {exit_code}: {args.command}")
                continue

            # ONLCR mặc định biến LF thành CRLF sau writer, nên tính thêm một byte/LF.
            paced_bytes += len(data) + data.count(b"\n")
            output_deadline = started + paced_bytes / args.rate
            next_marker, sequence = wait_with_markers(
                output_deadline, next_marker, args.interval, args.label, sequence,
            )
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        stop_process(process)


if __name__ == "__main__":
    main()
