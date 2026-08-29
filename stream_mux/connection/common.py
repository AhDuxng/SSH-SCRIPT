"""Các hàm phụ trợ cho tiến trình, pipe và kiểm tra socket."""

import json
import os
import subprocess
import threading
import time
from typing import BinaryIO


# Đọc một giá trị boolean từ cấu hình.
def cfg_bool(cfg: dict, key: str, default: str = "0") -> bool:
    return cfg.get(key, default).strip().lower() in {"1", "true", "yes", "on"}


# Tạo phần lệnh SSH dùng chung.
def ssh_base(cfg: dict) -> list[str]:
    command = [cfg.get("SSH_BIN", "ssh")]
    if cfg_bool(cfg, "SSH_STRICT_HOST_KEY_CHECKING", "0"):
        command += ["-o", "StrictHostKeyChecking=yes"]
    else:
        command += [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]
    if cfg_bool(cfg, "SSH_BATCH_MODE", "1"):
        command += ["-o", "BatchMode=yes"]
    identity = cfg.get("SSH_IDENTITY_FILE", "").strip()
    if identity:
        command += ["-i", os.path.expanduser(identity)]
    port = cfg.get("SERVER_PORT", "").strip()
    if port:
        command += ["-p", port]
    return command


# Thu thập PID của một cây tiến trình.
def process_tree(root_pid: int) -> list[int]:
    seen, pending, output = set(), [root_pid], []
    while pending:
        parent = pending.pop()
        if parent in seen:
            continue
        seen.add(parent)
        output.append(parent)
        checked = subprocess.run(
            ["pgrep", "-P", str(parent)], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, check=False,
        )
        for raw in checked.stdout.split():
            try:
                pending.append(int(raw))
            except ValueError:
                pass
    return output


# Liệt kê các socket thuộc nhóm PID.
def socket_rows(pids: list[int], protocol: str) -> list[str]:
    rows = []
    selector = "-iUDP" if protocol == "udp" else "-iTCP"
    for pid in pids:
        command = ["lsof", "-nP", "-a", "-p", str(pid), selector]
        if protocol == "tcp":
            command.append("-sTCP:ESTABLISHED")
        try:
            checked = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, check=False,
            )
        except FileNotFoundError:
            break
        for line in checked.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 9:
                rows.append(f"pid={pid} {' '.join(parts[8:])}")
    if rows or protocol != "udp":
        return sorted(set(rows))

    try:
        checked = subprocess.run(
            ["ss", "-H", "-u", "-a", "-p", "-n"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False,
        )
    except FileNotFoundError:
        return []
    wanted = {f"pid={pid}," for pid in pids}
    for line in checked.stdout.splitlines():
        if any(marker in line for marker in wanted):
            rows.append(line.strip())
    return sorted(set(rows))


class PipeReader:
    """Đọc byte thô từ pipe trong luồng nền."""

    # Khởi tạo bộ đọc byte từ pipe.
    def __init__(self, source: BinaryIO, on_data, name: str):
        self.source = source
        self.on_data = on_data
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    # Khởi chạy luồng đọc nền.
    def start(self):
        self.thread.start()

    # Đọc liên tục và chuyển tiếp từng khối byte kèm thời điểm quan sát.
    def _run(self):
        while True:
            chunk = os.read(self.source.fileno(), 65536)
            if not chunk:
                return
            self.on_data(chunk, time.time_ns(), time.perf_counter_ns())


class JSONPipeReader:
    """Đọc sự kiện JSON từng dòng từ SSH3 bridge viết bằng Go."""

    # Khởi tạo bộ đọc JSON từ pipe.
    def __init__(self, source: BinaryIO, on_frame, name: str):
        self.source = source
        self.on_frame = on_frame
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    # Khởi chạy luồng đọc nền.
    def start(self):
        self.thread.start()

    # Đọc và giải mã từng dòng JSON hợp lệ.
    def _run(self):
        for raw in self.source:
            observed_wall_ns, observed_mono_ns = time.time_ns(), time.perf_counter_ns()
            try:
                frame = json.loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            self.on_frame(frame, observed_wall_ns, observed_mono_ns)
