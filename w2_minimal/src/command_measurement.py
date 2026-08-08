import re
import time
import uuid

import pexpect

from terminal_io import ECHO_GAP, clean_digits, drain_pending_output, gapped_literal


# Biểu diễn lỗi khi lệnh remote kết thúc với exit code khác 0.
class CommandExitError(RuntimeError):
    def __init__(self, exit_code, measurement):
        super().__init__(f"remote command exited with {exit_code}")
        self.exit_code = exit_code
        self.measurement = measurement


# Bọc lệnh bằng marker chỉ được in sau khi lệnh và toàn bộ output phía server kết thúc.
def build_measured_command(command, marker):
    return (
        f"{{ {command}; }} 2>&1; W2_RC=$?; "
        f"printf '%s:%s\\n' {marker!r} \"$W2_RC\""
    )


# Đọc terminal đến marker kết thúc và đếm lượng dữ liệu đứng trước marker.
def wait_for_completion(child, marker, cfg):
    pattern = re.compile(
        gapped_literal(marker) + ECHO_GAP + ":" + ECHO_GAP
        + rf"(\d(?:{ECHO_GAP}\d)*)"
    )
    total_timeout = float(cfg.get("COMMAND_TIMEOUT", "180"))
    idle_timeout = float(cfg.get("COMMAND_IDLE_TIMEOUT", "20"))
    read_size = int(cfg.get("COMMAND_READ_BYTES", "65536"))
    keep_chars = int(cfg.get("COMMAND_PARSE_BUFFER_CHARS", "131072"))
    deadline = time.monotonic() + total_timeout
    last_output = time.monotonic()
    buffer = ""
    output_bytes = 0

    while True:
        now = time.monotonic()
        if now >= deadline:
            raise pexpect.TIMEOUT(
                f"completion marker not received within {total_timeout:.1f}s; "
                f"output_bytes={output_bytes}"
            )
        if idle_timeout > 0 and now - last_output >= idle_timeout:
            raise pexpect.TIMEOUT(
                f"no terminal output for {idle_timeout:.1f}s; output_bytes={output_bytes}"
            )

        read_timeout = min(0.5, deadline - now)
        if idle_timeout > 0:
            read_timeout = min(read_timeout, max(0.05, idle_timeout - (now - last_output)))
        try:
            chunk = child.read_nonblocking(size=read_size, timeout=max(0.05, read_timeout))
        except pexpect.TIMEOUT:
            continue
        if not chunk:
            continue

        last_output = time.monotonic()
        buffer += chunk
        match = pattern.search(buffer)
        if match:
            output_bytes += len(buffer[:match.start()].encode("utf-8", errors="replace"))
            return output_bytes, int(clean_digits(match.group(1))), time.perf_counter_ns()

        if len(buffer) > keep_chars:
            dropped = buffer[:-keep_chars]
            output_bytes += len(dropped.encode("utf-8", errors="replace"))
            buffer = buffer[-keep_chars:]


# Đo một lần chạy lệnh từ trước sendline đến khi client nhận marker sau output.
def measure_command(child, command, cfg):
    marker = f"__W2_DONE_{uuid.uuid4().hex.upper()}__"
    wrapped = build_measured_command(command, marker)
    drain_pending_output(child)
    started_ns = time.perf_counter_ns()
    child.sendline(wrapped)
    output_bytes, exit_code, ended_ns = wait_for_completion(child, marker, cfg)
    duration_s = (ended_ns - started_ns) / 1_000_000_000.0
    measurement = {
        "latency_ms": duration_s * 1000.0,
        "start_local_ns": started_ns,
        "end_local_ns": ended_ns,
        "output_bytes": output_bytes,
        "throughput_bytes_per_sec": output_bytes / duration_s if duration_s > 0 else 0.0,
        "exit_code": exit_code,
    }
    if exit_code != 0:
        raise CommandExitError(exit_code, measurement)
    return measurement


# Cố gắng dừng lệnh bị timeout để connection có thể đóng sạch.
def interrupt_command(child):
    try:
        child.sendcontrol("c")
        time.sleep(0.25)
    except Exception:
        pass
