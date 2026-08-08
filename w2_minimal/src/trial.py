import time

import pexpect

from clock_sync import estimate_clock_offset
from config import bool_cfg
from continuous_workloads import measure_workload
from protocol_runner import ProtocolRunner


# Tạo bản ghi thời gian thiết lập session.
def setup_row(trial):
    return {
        **trial,
        "status": "trial_unavailable",
        "session_setup_ms": "",
        "note": "",
    }


# Tạo bản ghi kiểm tra đồng bộ clock.
def clock_row(trial, requested):
    return {
        **trial,
        "status": "unavailable",
        "requested_probes": requested,
        "valid_probes": 0,
        "clock_offset_ns": "",
        "clock_offset_ms": "",
        "median_rtt_ms": "",
        "method": "",
        "note": "",
    }


# Tạo một hàng mẫu marker thành công hoặc thất bại.
def sample_row(trial, index, status, sequence="", latency="", remote_ns="", recv_ns="", note=""):
    return {
        **trial,
        "sample_index": index,
        "remote_sequence": sequence,
        "status": status,
        "latency_ms": latency,
        "remote_event_ns": remote_ns,
        "recv_local_ns": recv_ns,
        "note": note,
    }


# Tạo audit trial cùng lượng dữ liệu và tốc độ thực nhận.
def trial_row(trial, status, expected, successful, stage="", note="", measurement=None, cfg=None):
    measurement = measurement or {}
    cfg = cfg or {}
    return {
        **trial,
        "status": status,
        "expected_samples": expected,
        "successful_samples": successful,
        "received_bytes": measurement.get("received_bytes", ""),
        "receive_duration_s": (
            f"{measurement['receive_duration_s']:.6f}"
            if "receive_duration_s" in measurement else ""
        ),
        "observed_rate_bytes_per_sec": (
            f"{measurement['observed_rate_bytes_per_sec']:.3f}"
            if "observed_rate_bytes_per_sec" in measurement else ""
        ),
        "configured_rate_bytes_per_sec": cfg.get("OUTPUT_RATE_BYTES_PER_SEC", ""),
        "configured_chunk_bytes": cfg.get("OUTPUT_RATE_CHUNK_BYTES", ""),
        "failure_stage": stage,
        "note": note,
    }


# Chuẩn hóa exception thành trạng thái CSV.
def failure_status(exc):
    if isinstance(exc, pexpect.TIMEOUT):
        return "timeout"
    if isinstance(exc, pexpect.EOF):
        return "eof"
    return "failure"


# Chạy một connection độc lập và thu đủ các marker của trial.
def run_trial(cfg, trial, sample_count):
    prompt = f"__W2_{trial['trial_order']:03d}_{trial['trial_id']}_PROMPT__# "
    runner = ProtocolRunner(cfg, trial["protocol"], prompt)
    setup = setup_row(trial)
    requested_probes = int(cfg.get("CLOCK_OFFSET_PROBES", "9"))
    clock = clock_row(trial, requested_probes)
    rows = []
    child = None
    stage = "session_setup"

    try:
        child, setup_ms = runner.open()
        setup.update(status="success", session_setup_ms=f"{setup_ms:.3f}")
        if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
            print(f"[READY] trial={trial['trial_id']} session_setup_ms={setup_ms:.3f}", flush=True)

        stage = "clock_sync"
        sync = estimate_clock_offset(
            child, runner, requested_probes,
            int(cfg.get("CLOCK_OFFSET_MIN_PROBES", "5")),
            float(cfg.get("EVENT_TIMEOUT", "20")),
        )
        clock.update(
            status="success",
            valid_probes=sync["valid_probes"],
            clock_offset_ns=sync["clock_offset_ns"],
            clock_offset_ms=f"{sync['clock_offset_ns'] / 1_000_000.0:.6f}",
            median_rtt_ms=f"{sync['median_rtt_ms']:.6f}",
            method=sync["method"],
        )
        trial_cfg = {**cfg, "_CLOCK_OFFSET_NS": str(sync["clock_offset_ns"])}

        def record(index, sequence, latency_ms, remote_ns, recv_ns):
            maximum = float(cfg.get("MAX_VALID_LATENCY_MS", "60000"))
            minimum = float(cfg.get("MIN_VALID_LATENCY_MS", "0"))
            if not minimum <= latency_ms <= maximum:
                rows.append(sample_row(
                    trial, index, "clock_invalid", sequence, f"{latency_ms:.6f}",
                    remote_ns, recv_ns,
                ))
                if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
                    print(
                        f"[LIVE] trial={trial['trial_id']} sample={index:03d}/{sample_count:03d} "
                        f"remote_seq={sequence} status=clock_invalid latency_ms={latency_ms:.3f}",
                        flush=True,
                    )
                return False
            rows.append(sample_row(
                trial, index, "success", sequence, f"{latency_ms:.6f}",
                remote_ns, recv_ns,
            ))
            if bool_cfg(cfg, "LIVE_PROGRESS", "1"):
                print(
                    f"[LIVE] trial={trial['trial_id']} sample={index:03d}/{sample_count:03d} "
                    f"remote_seq={sequence} status=success latency_ms={latency_ms:.3f}",
                    flush=True,
                )
            return True

        stage = "workload"
        measurement = measure_workload(
            child, runner, trial["protocol"], trial["workload"], trial_cfg, record
        )
        successful = sum(row["status"] == "success" for row in rows)
        return rows, setup, clock, trial_row(
            trial, "success" if successful == sample_count else "partial",
            sample_count, successful, measurement=measurement, cfg=cfg,
        )
    except Exception as exc:
        status = failure_status(exc)
        note = repr(exc)[:1000]
        if setup["status"] != "success":
            setup.update(status=status, note=note)
            clock.update(status=status, note="clock sync not attempted because session setup failed")
        elif clock["status"] != "success":
            clock.update(status=status, note=note)
        successful = sum(row["status"] == "success" for row in rows)
        for index in range(len(rows) + 1, sample_count + 1):
            rows.append(sample_row(trial, index, status, note=note))
        print(
            f"[FAIL] trial={trial['trial_id']} collected={len([r for r in rows if r['status'] == 'success'])}"
            f"/{sample_count} status={status} note={note}", flush=True,
        )
        return rows, setup, clock, trial_row(
            trial, "partial" if successful else status,
            sample_count, successful, stage, note, cfg=cfg,
        )
    finally:
        if child is not None:
            runner.close(child)
