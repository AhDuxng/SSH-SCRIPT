import re
import shlex
import time

import pexpect

from terminal_io import ECHO_GAP, clean_digits, gapped_literal


TIMESTAMP_19 = rf"(\d(?:{ECHO_GAP}\d){{18}})"
SEQUENCE = rf"(\d(?:{ECHO_GAP}\d)*)"


# Tạo lệnh kiểm tra exit code mà không đưa output preflight lên terminal.
def build_preflight_command(command, marker):
    return (
        f"{{ {command}; }} >/dev/null 2>&1; "
        f"printf '%s%s\\n' {shlex.quote(marker)} \"$?\""
    )


# Tạo workload output liên tục và marker timestamp chạy đồng thời.
def build_output_load_command(command, label, interval):
    return (
        "W2_SEQ=0; (while true; do W2_SEQ=$((W2_SEQ+1)); "
        f'printf "{label}%s:%s\\n" "$W2_SEQ" "$(date +%s%N)"; '
        f"sleep {interval}; done) & W2_MARKER_PID=$!; "
        f"while true; do {{ {command}; }} 2>&1; done"
    )


# Kiểm tra lệnh workload chạy thành công trước khi bắt đầu thu mẫu.
def preflight_workload(child, runner, command, cfg):
    marker = f"W2_PREFLIGHT_{time.time_ns()}_RC:"
    pattern = re.compile(gapped_literal(marker) + rf"(\d(?:{ECHO_GAP}\d)*)")
    child.sendline(build_preflight_command(command, marker))
    child.expect(pattern, timeout=float(cfg.get("PREFLIGHT_TIMEOUT", "180")))
    exit_code = int(clean_digits(child.match.group(1)))
    runner.expect_prompt(child, float(cfg.get("EVENT_TIMEOUT", "20")))
    if exit_code != 0:
        raise RuntimeError(f"workload preflight exited with {exit_code}: {command}")


# Thu marker tăng dần, bỏ warm-up và tính event-display latency đã hiệu chỉnh clock.
def collect_events(child, pattern, cfg, callback):
    warmup = int(cfg.get("WARMUP_SAMPLES", "10"))
    samples = int(cfg.get("SAMPLES_PER_TRIAL", "100"))
    timeout = float(cfg.get("EVENT_TIMEOUT", "20"))
    clock_offset_ns = int(cfg["_CLOCK_OFFSET_NS"])
    last_sequence = -1
    accepted = warmed = 0

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


# Dừng cả workload lặp và marker nền, rồi đồng bộ lại prompt.
def stop_output_load(child, runner, cfg):
    timeout = float(cfg.get("EVENT_TIMEOUT", "20"))
    for _ in range(3):
        child.sendcontrol("c")
        time.sleep(0.25)
    runner.expect_prompt(child, timeout)
    child.sendline("kill $W2_MARKER_PID 2>/dev/null; wait $W2_MARKER_PID 2>/dev/null; true")
    runner.expect_prompt(child, timeout)


# Lặp đúng workload W2 và xen marker timestamp để đo độ trễ dưới output liên tục.
def measure_workload(child, runner, _protocol, workload, cfg, callback):
    command = cfg["_WORKLOAD_COMMAND"]
    preflight_workload(child, runner, command, cfg)
    label = "W2_OUTPUT_EVENT_"
    pattern = re.compile(
        gapped_literal(label) + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
    )
    interval = float(cfg.get("EVENT_INTERVAL_SECONDS", "0.1"))
    child.sendline(build_output_load_command(command, label, interval))
    try:
        collect_events(child, pattern, cfg, callback)
    finally:
        stop_output_load(child, runner, cfg)
