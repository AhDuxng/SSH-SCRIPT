import time

import pexpect

from config import bool_cfg
from protocol_runner import ProtocolRunner
from terminal_io import drain_pending_output, wait_for_marker
from workloads import wrap_command


# Tạo dòng setup mặc định trước khi thử mở session.
def setup_row(trial):
    row = {key: value for key, value in trial.items() if key != "command"}
    return {
        **row,
        "status": "trial_unavailable",
        "session_setup_ms": "",
        "note": "",
    }


# Tạo dòng kết quả cho một workload output lớn.
def sample_row(
    trial, status, latency_ms="", output_bytes="", output_lines="",
    throughput_mib_s="", exit_code="", note="",
):
    return {
        **trial,
        "status": status,
        "latency_ms": latency_ms,
        "output_bytes": output_bytes,
        "output_lines": output_lines,
        "throughput_mib_s": throughput_mib_s,
        "exit_code": exit_code,
        "note": note,
    }


# Chuyển trạng thái sample lúc mở session thành trạng thái setup cụ thể.
def setup_failure_status(sample_status):
    if sample_status == "timeout":
        return "timeout"
    if sample_status == "eof":
        return "eof"
    return "failure"


# Chạy một workload duy nhất trên một session độc lập và nhận hết output.
def run_trial(cfg, trial):
    prompt_marker = f"__W2_{trial['trial_order']:03d}_{trial['trial_id']}_PROMPT__# "
    done_marker = f"__W2_DONE_{trial['trial_order']:03d}_{time.time_ns()}__"
    runner = ProtocolRunner(cfg, trial["protocol"], prompt_marker)
    setup = setup_row(trial)
    child = None
    try:
        child, setup_ms = runner.open()
        setup.update(status="success", session_setup_ms=f"{setup_ms:.3f}")
        if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
            print(
                f"[READY] trial={trial['trial_id']} session_setup_ms={setup_ms:.3f}",
                flush=True,
            )

        warmup = float(cfg.get("WARMUP_SECONDS", "2"))
        if warmup > 0:
            time.sleep(warmup)
        max_lines = int(cfg.get("MAX_OUTPUT_LINES", "0"))
        wrapped = wrap_command(trial["command"], done_marker, max_lines)
        drain_pending_output(child)
        started = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes, output_lines, exit_code = wait_for_marker(
            child,
            done_marker,
            float(cfg.get("SAMPLE_TIMEOUT", "180")),
            float(cfg.get("COMMAND_IDLE_TIMEOUT", "20")),
            int(cfg.get("MAX_READ_BYTES", "65536")),
        )
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        throughput = (
            output_bytes / (1024.0 * 1024.0) / (latency_ms / 1000.0)
            if latency_ms > 0 else 0.0
        )
        status = "success" if exit_code == 0 else "command_error"
        row = sample_row(
            trial, status, f"{latency_ms:.3f}", output_bytes, output_lines,
            f"{throughput:.6f}", exit_code,
            "" if exit_code == 0 else f"remote command exited with {exit_code}",
        )
        if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
            print(
                f"[LIVE] trial={trial['trial_id']} status={status} "
                f"latency_ms={latency_ms:.3f} bytes={output_bytes} "
                f"throughput_mib_s={throughput:.3f}",
                flush=True,
            )
        return row, setup
    except pexpect.TIMEOUT as exc:
        row = sample_row(trial, "timeout", note=str(exc))
    except pexpect.EOF as exc:
        row = sample_row(trial, "eof", note=str(exc))
    except Exception as exc:
        row = sample_row(trial, "failure", note=repr(exc))
    finally:
        if child is not None:
            runner.close(child)

    if setup["status"] != "success":
        setup["status"] = setup_failure_status(row["status"])
        setup["note"] = row["note"]
    print(f"[FAIL] trial={trial['trial_id']} status={row['status']} note={row['note']}", flush=True)
    return row, setup
