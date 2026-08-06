import re
import time

import pexpect

from terminal_io import ECHO_GAP, clean_digits, gapped_literal


TIMESTAMP_19 = rf"(\d(?:{ECHO_GAP}\d){{18}})"
SEQUENCE = rf"(\d(?:{ECHO_GAP}\d)*)"


# Nhận các marker sự kiện duy nhất, bỏ warm-up và tính latency đã hiệu chỉnh clock.
def collect_events(child, pattern, warmup, samples, timeout, clock_offset_ns, callback):
    last_sequence = -1
    accepted = 0
    warmed = 0
    while accepted < samples:
        child.expect(pattern, timeout=timeout)
        recv_ns = time.time_ns()
        sequence = int(clean_digits(child.match.group(1)))
        remote_ns = int(clean_digits(child.match.group(2)))
        if sequence <= last_sequence:
            continue
        last_sequence = sequence
        if warmed < warmup:
            warmed += 1
            continue
        latency_ms = (recv_ns - remote_ns - clock_offset_ns) / 1_000_000.0
        accepted += 1
        callback(accepted, sequence, latency_ms, remote_ns, recv_ns)


# Dừng workload lặp và đồng bộ lại shell prompt.
def stop_foreground(child, runner, timeout):
    for _ in range(3):
        child.sendcontrol("c")
        time.sleep(0.25)
    runner.expect_prompt(child, timeout)


# Đo các lần làm mới màn hình kiểu top; Mosh dùng dòng sự kiện như test-w2.
def measure_top(child, runner, protocol, cfg, callback):
    label = "W2_CUI_"
    pattern = re.compile(gapped_literal(label) + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19)
    interval = float(cfg.get("TOP_INTERVAL_SECONDS", "1.0"))
    if protocol == "mosh":
        command = (
            "W2_SEQ=0; while true; do W2_SEQ=$((W2_SEQ+1)); "
            f'printf "{label}%s:%s\\n" "$W2_SEQ" "$(date +%s%N)"; '
            f"sleep {interval}; done"
        )
    else:
        command = (
            "stty -onlcr; W2_SEQ=0; while true; do W2_SEQ=$((W2_SEQ+1)); "
            "W2_TS=$(date +%s%N); "
            "printf '\\033[2J\\033[H=== System Monitor [Frame %s] ===\\r\\n"
            "CPU [####............] 78%%\\r\\nMEM [##########......] 67%%\\r\\n"
            f"{label}%s:%s\\r\\n' \"$W2_SEQ\" \"$W2_SEQ\" \"$W2_TS\"; "
            f"sleep {interval}; done"
        )
    child.sendline(command)
    try:
        collect_events_from_cfg(child, pattern, cfg, callback)
    finally:
        stop_foreground(child, runner, float(cfg.get("EVENT_TIMEOUT", "20")))
        if protocol != "mosh":
            child.sendline("stty onlcr")
            runner.expect_prompt(child, float(cfg.get("EVENT_TIMEOUT", "20")))


# Đo thời điểm các dòng mới do tail -f hiển thị tại client.
def measure_tail(child, runner, cfg, callback):
    label = "W2_TAIL_"
    pattern = re.compile(gapped_literal(label) + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19)
    remote_log = f"/tmp/w2_tail_{time.time_ns()}.log"
    interval = float(cfg.get("TAIL_INTERVAL_SECONDS", "0.05"))
    child.sendline(f"rm -f {remote_log}; touch {remote_log}")
    runner.expect_prompt(child, float(cfg.get("EVENT_TIMEOUT", "20")))
    child.sendline(
        f"(W2_SEQ=0; while true; do W2_SEQ=$((W2_SEQ+1)); "
        f'printf "{label}%s:%s\\n" "$W2_SEQ" "$(date +%s%N)" >> {remote_log}; '
        f"sleep {interval}; done) & W2_WRITER_PID=$!; "
        f"printf 'W2_WRITER_PID=%s\\n' \"$W2_WRITER_PID\"; tail -n 0 -f {remote_log}"
    )
    child.expect(r"W2_WRITER_PID=(\d+)", timeout=float(cfg.get("EVENT_TIMEOUT", "20")))
    writer_pid = child.match.group(1)
    try:
        collect_events_from_cfg(child, pattern, cfg, callback)
    finally:
        try:
            stop_foreground(child, runner, float(cfg.get("EVENT_TIMEOUT", "20")))
        finally:
            child.sendline(f"kill {writer_pid} 2>/dev/null; rm -f {remote_log}")
            runner.expect_prompt(child, float(cfg.get("EVENT_TIMEOUT", "20")))


# Đo thời điểm timestamp của từng dòng ping xuất hiện tại client.
def measure_ping(child, runner, cfg, callback):
    gap = ECHO_GAP
    timestamp_us = rf"(\d(?:{gap}\d){{9}}{gap}\.{gap}\d(?:{gap}\d){{5}})"
    pattern = re.compile(r"\[" + gap + timestamp_us + gap + r"\].*icmp_seq=" + gap + SEQUENCE)
    interval = float(cfg.get("PING_INTERVAL_SECONDS", "0.1"))
    target = cfg.get("PING_TARGET", "127.0.0.1")
    child.sendline(f"ping -D -i {interval} {target}")
    child.expect(r"PING ", timeout=float(cfg.get("EVENT_TIMEOUT", "20")))

    # Ping regex trả timestamp trước sequence; đổi thứ tự group cho callback chung.
    warmup = int(cfg.get("WARMUP_SAMPLES", "10"))
    samples = int(cfg.get("SAMPLES_PER_TRIAL", "100"))
    timeout = float(cfg.get("EVENT_TIMEOUT", "20"))
    offset = int(cfg["_CLOCK_OFFSET_NS"])
    last_sequence = -1
    accepted = warmed = 0
    try:
        while accepted < samples:
            child.expect(pattern, timeout=timeout)
            recv_ns = time.time_ns()
            timestamp = re.sub(r"[^0-9.]", "", re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", child.match.group(1)))
            sequence = int(clean_digits(child.match.group(2)))
            if sequence <= last_sequence:
                continue
            last_sequence = sequence
            seconds, micros = timestamp.split(".", 1)
            remote_ns = int(seconds) * 1_000_000_000 + int((micros + "000000")[:6]) * 1_000
            if warmed < warmup:
                warmed += 1
                continue
            latency_ms = (recv_ns - remote_ns - offset) / 1_000_000.0
            accepted += 1
            callback(accepted, sequence, latency_ms, remote_ns, recv_ns)
    finally:
        stop_foreground(child, runner, timeout)


# Đọc cấu hình chung rồi thu đủ mẫu cho top hoặc tail.
def collect_events_from_cfg(child, pattern, cfg, callback):
    collect_events(
        child, pattern,
        int(cfg.get("WARMUP_SAMPLES", "10")),
        int(cfg.get("SAMPLES_PER_TRIAL", "100")),
        float(cfg.get("EVENT_TIMEOUT", "20")),
        int(cfg["_CLOCK_OFFSET_NS"]), callback,
    )


# Chọn workload đo liên tục theo tên cấu hình.
def measure_workload(child, runner, protocol, workload, cfg, callback):
    if workload == "top":
        return measure_top(child, runner, protocol, cfg, callback)
    if workload == "tail":
        return measure_tail(child, runner, cfg, callback)
    if workload == "ping":
        return measure_ping(child, runner, cfg, callback)
    raise ValueError(f"unsupported workload: {workload}")
