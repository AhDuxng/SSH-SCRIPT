import time

import pexpect

from command_measurement import CommandExitError, interrupt_command, measure_command
from config import bool_cfg
from protocol_runner import ProtocolRunner


# Tạo bản ghi thời gian thiết lập session.
def setup_row(trial):
    return {**trial, "status": "trial_unavailable", "session_setup_ms": "", "note": ""}


# Tạo một hàng kết quả command-completion.
def sample_row(trial, index, status, measurement=None, note=""):
    measurement = measurement or {}
    protocol = trial["protocol"]
    semantics = (
        "terminal_final_state" if protocol == "mosh" else "ordered_full_output"
    )
    return {
        **trial,
        "sample_index": index,
        "status": status,
        "latency_ms": (
            f"{measurement['latency_ms']:.6f}" if "latency_ms" in measurement else ""
        ),
        "command_exit_code": measurement.get("exit_code", ""),
        "start_local_ns": measurement.get("start_local_ns", ""),
        "end_local_ns": measurement.get("end_local_ns", ""),
        "output_bytes": measurement.get("output_bytes", ""),
        "throughput_bytes_per_sec": (
            f"{measurement['throughput_bytes_per_sec']:.3f}"
            if "throughput_bytes_per_sec" in measurement else ""
        ),
        "completion_semantics": semantics,
        "note": note,
    }


# Tạo audit cho một connection và tổng lượng output đã nhận.
def trial_row(trial, status, expected, successful, rows, stage="", note=""):
    valid = [row for row in rows if row["status"] == "success"]
    total_bytes = sum(int(row["output_bytes"]) for row in valid if row["output_bytes"] != "")
    total_seconds = sum(float(row["latency_ms"]) / 1000.0 for row in valid if row["latency_ms"])
    return {
        **trial,
        "status": status,
        "expected_samples": expected,
        "successful_samples": successful,
        "received_bytes": total_bytes,
        "receive_duration_s": f"{total_seconds:.6f}",
        "observed_rate_bytes_per_sec": (
            f"{total_bytes / total_seconds:.3f}" if total_seconds > 0 else ""
        ),
        "failure_stage": stage,
        "note": note,
    }


# Chuẩn hóa exception thành trạng thái CSV.
def failure_status(exc):
    if isinstance(exc, pexpect.TIMEOUT):
        return "timeout"
    if isinstance(exc, pexpect.EOF):
        return "eof"
    if isinstance(exc, CommandExitError):
        return "command_error"
    return "failure"


# Chạy warm-up rồi đo nhiều lần hoàn thành lệnh trên cùng một connection.
def run_trial(cfg, trial, sample_count):
    prompt = f"__W2_{trial['trial_order']:03d}_{trial['trial_id']}_PROMPT__# "
    runner = ProtocolRunner(cfg, trial["protocol"], prompt)
    setup = setup_row(trial)
    rows = []
    child = None
    stage = "session_setup"

    try:
        child, setup_ms = runner.open()
        setup.update(status="success", session_setup_ms=f"{setup_ms:.3f}")
        if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
            print(f"[READY] trial={trial['trial_id']} session_setup_ms={setup_ms:.3f}", flush=True)

        stage = "warmup"
        warmups = int(cfg.get("WARMUP_SAMPLES", "10"))
        command = cfg["_WORKLOAD_COMMAND"]
        measurement_cfg = {**cfg, "_PROTOCOL": trial["protocol"]}
        for index in range(1, warmups + 1):
            measure_command(child, command, measurement_cfg, runner.tracker)
            if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
                print(
                    f"[WARMUP] trial={trial['trial_id']} sample={index:03d}/{warmups:03d}",
                    flush=True,
                )

        stage = "workload"
        for index in range(1, sample_count + 1):
            try:
                measurement = measure_command(
                    child, command, measurement_cfg, runner.tracker,
                )
                rows.append(sample_row(trial, index, "success", measurement))
                if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
                    print(
                        f"[LIVE] trial={trial['trial_id']} sample={index:03d}/{sample_count:03d} "
                        f"status=success latency_ms={measurement['latency_ms']:.3f} "
                        f"bytes={measurement['output_bytes']}", flush=True,
                    )
            except CommandExitError as exc:
                rows.append(sample_row(trial, index, "command_error", exc.measurement, str(exc)))
                raise

        successful = sum(row["status"] == "success" for row in rows)
        return rows, setup, trial_row(
            trial, "success" if successful == sample_count else "partial",
            sample_count, successful, rows,
        )
    except Exception as exc:
        status = failure_status(exc)
        note = repr(exc)[:1000]
        if setup["status"] != "success":
            setup.update(status=status, note=note)
        if child is not None and status == "timeout":
            interrupt_command(child)
        successful = sum(row["status"] == "success" for row in rows)
        for index in range(len(rows) + 1, sample_count + 1):
            rows.append(sample_row(trial, index, status, note=note))
        print(
            f"[FAIL] trial={trial['trial_id']} collected={successful}/{sample_count} "
            f"status={status} note={note}", flush=True,
        )
        return rows, setup, trial_row(
            trial, "partial" if successful else status,
            sample_count, successful, rows, stage, note,
        )
    finally:
        if child is not None:
            runner.close(child)
