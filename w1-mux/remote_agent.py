#!/usr/bin/env python3
"""Chạy lệnh W1 bên trong các stream đã được transport mở."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time


FRAME_PREFIX = "MUX1 "
PROTOCOL_VERSION = 1
write_lock = threading.Lock()
role_locks: dict[str, threading.Lock] = {}


# Ghi một frame JSON ra output chuẩn.
def emit(payload: dict) -> None:
    document = {"version": PROTOCOL_VERSION, **payload}
    line = FRAME_PREFIX + json.dumps(document, separators=(",", ":"), ensure_ascii=True)
    with write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# Mã hóa dữ liệu nhị phân sang Base64.
def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


# Chạy một lệnh W1 và trả kết quả kèm checksum.
def run_command(frame: dict, allowed_roles: set[str]) -> None:
    role = str(frame.get("role", ""))
    request_id = str(frame.get("request_id", ""))
    if role not in allowed_roles or not request_id:
        emit({
            "type": "error", "role": role, "request_id": request_id,
            "message": "invalid role or request_id",
        })
        return
    command = frame.get("command")
    timeout = float(frame.get("timeout_seconds", 30.0))
    if not isinstance(command, str) or not command or timeout <= 0:
        emit({
            "type": "error", "role": role, "request_id": request_id,
            "message": "invalid command or timeout",
        })
        return

    # Khóa theo role để giữ đúng thứ tự lệnh.
    with role_locks[role]:
        started_ns = time.monotonic_ns()
        try:
            completed = subprocess.run(
                ["/bin/bash", "-lc", command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            timed_out = False
            exit_code = completed.returncode
            error = ""
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            timed_out = True
            exit_code = None
            error = f"remote command timeout after {timeout:.3f}s"
        except Exception as exc:
            stdout = b""
            stderr = b""
            timed_out = False
            exit_code = None
            error = repr(exc)
        completed_ns = time.monotonic_ns()
        emit({
            "type": "result",
            "role": role,
            "request_id": request_id,
            "remote_started_ns": started_ns,
            "remote_completed_ns": completed_ns,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "stdout_b64": b64(stdout),
            "stderr_b64": b64(stderr),
            "expected_stdout_bytes": len(stdout),
            "expected_stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "error": error,
        })


# Đọc frame điều khiển và quản lý các worker W1.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roles", required=True, help="comma-separated logical roles")
    args = parser.parse_args()
    roles = [item.strip() for item in args.roles.split(",") if item.strip()]
    if not roles or len(set(roles)) != len(roles):
        raise SystemExit("--roles must contain unique non-empty names")
    allowed_roles = set(roles)
    role_locks.update({role: threading.Lock() for role in roles})
    for role in roles:
        emit({"type": "ready", "role": role, "pid": os.getpid()})

    workers: list[threading.Thread] = []
    for raw in sys.stdin:
        marker = raw.find(FRAME_PREFIX)
        if marker < 0:
            continue
        try:
            frame = json.loads(raw[marker + len(FRAME_PREFIX):])
        except json.JSONDecodeError:
            continue
        if frame.get("version") != PROTOCOL_VERSION:
            continue
        if frame.get("type") == "shutdown":
            break
        if frame.get("type") != "exec":
            continue
        worker = threading.Thread(
            target=run_command, args=(frame, allowed_roles), daemon=False
        )
        worker.start()
        workers.append(worker)

    for worker in workers:
        worker.join()
    for role in roles:
        emit({"type": "closed", "role": role})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
