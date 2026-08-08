import re
import shlex
import time

import pexpect

from terminal_io import ECHO_GAP, clean_digits, gapped_literal


TIMESTAMP_19 = rf"(\d(?:{ECHO_GAP}\d){{18}})"
SEQUENCE = rf"(\d(?:{ECHO_GAP}\d)*)"


# Tạo lệnh kiểm tra workload và giữ lại stderr ngắn gọn khi lệnh thất bại.
def build_preflight_command(command, marker):
    return (
        "W2_PREFLIGHT_ERR=$(mktemp); "
        f"{{ {command}; }} >/dev/null 2>\"$W2_PREFLIGHT_ERR\"; "
        "W2_PREFLIGHT_RC=$?; "
        "if [ \"$W2_PREFLIGHT_RC\" -ne 0 ]; then "
        "printf 'W2_PREFLIGHT_ERROR:'; tail -c 1000 \"$W2_PREFLIGHT_ERR\"; printf '\\n'; fi; "
        "rm -f \"$W2_PREFLIGHT_ERR\"; "
        f"printf '%s%s\\n' {shlex.quote(marker)} \"$W2_PREFLIGHT_RC\""
    )


# Dùng một writer duy nhất để xen marker vào workload đã định tốc độ.
def build_output_load_command(
    command, label, interval, rate_limiter_script="", rate=1048576, chunk_size=4096,
):
    if rate_limiter_script:
        return " ".join((
            "python3",
            shlex.quote(rate_limiter_script),
            "--rate", str(int(rate)),
            "--chunk", str(int(chunk_size)),
            "--interval", str(float(interval)),
            "--label", shlex.quote(label),
            "--command", shlex.quote(command),
        ))

    return (
        "( W2_SEQ=0; "
        "trap 'for W2_PID in $(jobs -p); do kill \"$W2_PID\" 2>/dev/null; "
        "wait \"$W2_PID\" 2>/dev/null; done' EXIT; "
        "trap 'exit 130' INT TERM; "
        "(while true; do W2_SEQ=$((W2_SEQ+1)); "
        f'printf "{label}%s:%s\\n" "$W2_SEQ" "$(date +%s%N)"; '
        f"sleep {interval}; done) & "
        f"while true; do {{ {command}; }} 2>&1; done )"
    )


# Kiểm tra exit code và đưa stderr của preflight vào thông báo lỗi.
def preflight_workload(child, runner, command, cfg):
    marker = f"W2_PREFLIGHT_{time.time_ns()}_RC:"
    pattern = re.compile(gapped_literal(marker) + rf"(\d(?:{ECHO_GAP}\d)*)")
    child.sendline(build_preflight_command(command, marker))
    child.expect(pattern, timeout=float(cfg.get("PREFLIGHT_TIMEOUT", "180")))
    diagnostic = child.before[-1000:].strip()
    exit_code = int(clean_digits(child.match.group(1)))
    runner.expect_prompt(child, float(cfg.get("EVENT_TIMEOUT", "20")))
    if exit_code != 0:
        raise RuntimeError(
            f"workload preflight exited with {exit_code}: {command}; "
            f"remote_error={diagnostic!r}"
        )


# Thu marker, áp deadline tổng và trả về bộ đếm dữ liệu thực sự quan sát được.
def collect_events(child, pattern, cfg, callback):
    warmup = int(cfg.get("WARMUP_SAMPLES", "10"))
    samples = int(cfg.get("SAMPLES_PER_TRIAL", "100"))
    timeout = float(cfg.get("EVENT_TIMEOUT", "20"))
    clock_offset_ns = int(cfg["_CLOCK_OFFSET_NS"])
    last_sequence = -1
    recorded = warmed = 0
    parse_buffer = ""
    max_buffer = int(cfg.get("EVENT_PARSE_BUFFER_CHARS", "131072"))
    read_size = int(cfg.get("EVENT_READ_BYTES", "4096"))
    total_timeout = float(cfg.get("EVENT_TOTAL_TIMEOUT", "120"))
    started = time.monotonic()
    deadline = started + total_timeout
    received_bytes = 0

    while recorded < samples:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise pexpect.TIMEOUT(
                f"event collection exceeded {total_timeout:.1f}s; "
                f"recorded={recorded}/{samples}"
            )
        try:
            chunk = child.read_nonblocking(size=read_size, timeout=min(timeout, remaining))
        except pexpect.TIMEOUT as exc:
            raise pexpect.TIMEOUT(
                f"no terminal output for {min(timeout, remaining):.1f}s; "
                f"recorded={recorded}/{samples}"
            ) from exc
        except pexpect.EOF:
            break

        recv_ns = time.time_ns()
        if chunk:
            parse_buffer += chunk
            received_bytes += len(chunk.encode("utf-8", errors="replace"))

        while True:
            match = pattern.search(parse_buffer)
            if not match:
                break

            sequence = int(clean_digits(match.group(1)))
            remote_ns = int(clean_digits(match.group(2)))

            if sequence > last_sequence:
                last_sequence = sequence
                if warmed < warmup:
                    warmed += 1
                else:
                    latency_ms = (recv_ns - remote_ns - clock_offset_ns) / 1_000_000.0
                    recorded += 1
                    callback(recorded, sequence, latency_ms, remote_ns, recv_ns)

            parse_buffer = parse_buffer[match.end():]

        if len(parse_buffer) > max_buffer:
            parse_buffer = parse_buffer[-max_buffer:]

    if recorded < samples:
        raise RuntimeError(
            f"collected {recorded}/{samples} samples before EOF/timeout"
        )
    duration = time.monotonic() - started
    return {
        "received_bytes": received_bytes,
        "receive_duration_s": duration,
        "observed_rate_bytes_per_sec": received_bytes / duration if duration > 0 else 0.0,
    }


# Dừng pipeline; trap phía server chịu trách nhiệm dừng marker nền.
def stop_output_load(child, runner, cfg):
    timeout = float(cfg.get("EVENT_TIMEOUT", "20"))
    for _ in range(3):
        child.sendcontrol("c")
        time.sleep(0.25)
    runner.expect_prompt(child, timeout)


# Chạy workload liên tục và đo marker trên chính dòng output đã giới hạn tốc độ.
def measure_workload(child, runner, _protocol, workload, cfg, callback):
    command = cfg["_WORKLOAD_COMMAND"]
    preflight_workload(child, runner, command, cfg)
    label = "W2_OUTPUT_EVENT_"
    pattern = re.compile(
        gapped_literal(label) + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
    )
    interval = float(cfg.get("EVENT_INTERVAL_SECONDS", "0.1"))
    rate_limiter = cfg.get("REMOTE_RATE_LIMIT_SCRIPT", "")
    rate = int(cfg.get("OUTPUT_RATE_BYTES_PER_SEC", "1048576"))
    chunk_size = int(cfg.get("OUTPUT_RATE_CHUNK_BYTES", "4096"))
    child.sendline(build_output_load_command(
        command, label, interval, rate_limiter, rate, chunk_size,
    ))
    try:
        return collect_events(child, pattern, cfg, callback)
    finally:
        stop_output_load(child, runner, cfg)
