from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from constants import COMMANDS


# Tạo phần định danh chung cho một stream.
def _sample_base(trial: dict, role: str, index: int, stream) -> dict:
    return {
        **trial,
        "stream_role": role,
        "stream_index": index,
        "transport_stream_id": stream.stream_id,
        "conversation_stream_id": stream.conversation_id,
    }


# Tạo kế hoạch mẫu bằng cách lặp tuần tự năm lệnh.
def build_sample_plan(samples_per_stream: int):
    if samples_per_stream <= 0 or samples_per_stream % len(COMMANDS) != 0:
        raise ValueError(
            f"SAMPLES_PER_STREAM_PER_TRIAL must be a positive multiple of {len(COMMANDS)}"
        )
    return [
        {
            "sample_index": sample_index,
            "cycle_index": (sample_index - 1) // len(COMMANDS) + 1,
            "command_index": (sample_index - 1) % len(COMMANDS) + 1,
            "command": COMMANDS[(sample_index - 1) % len(COMMANDS)],
        }
        for sample_index in range(1, samples_per_stream + 1)
    ]


# Tạo một mẫu lỗi chưa có kết quả lệnh.
def _failed_sample(trial, role, index, stream, sample, status, note):
    return {
        **_sample_base(trial, role, index, stream),
        **sample,
        "request_id": f"{trial['trial_id']}:{role}:{sample['sample_index']}",
        "send_time_ns": "",
        "completion_time_ns": "",
        "latency_ms": "",
        "exit_code": "",
        "expected_bytes": "",
        "received_bytes": "",
        "expected_sha256": "",
        "received_sha256": "",
        "completion_marker_received": 0,
        "output_verifiable": 0,
        "output_complete": 0,
        "timed_out": int(status == "timeout"),
        "status": status,
        "stderr_bytes": "",
        "note": note,
    }


# Chạy tuần tự toàn bộ kế hoạch mẫu trên một stream.
def run_stream(
    trial, role, index, stream, barrier, command_timeout,
    sample_plan, live_progress, live_every, continue_after_timeout=False,
):
    rows = []
    started_ns = 0
    completed_ns = 0
    failure_note = ""
    try:
        barrier.wait(timeout=command_timeout)
        started_ns = time.time_ns()
        for position, sample in enumerate(sample_plan):
            sample_index = sample["sample_index"]
            command = sample["command"]
            request_id = f"{trial['trial_id']}:{role}:{sample_index}"
            try:
                result = stream.execute(request_id, command, command_timeout)
                stdout = result["stdout"]
                received_hash = hashlib.sha256(stdout).hexdigest()
                output_verifiable = bool(result.get("output_verifiable"))
                output_complete = bool(result.get("output_complete"))
                timed_out = bool(result.get("timed_out"))
                status = "timeout" if timed_out else "completed"
                note = str(result.get("error", ""))
                row = {
                    **_sample_base(trial, role, index, stream),
                    **sample,
                    "request_id": request_id,
                    "send_time_ns": result["send_time_ns"],
                    "completion_time_ns": result["completion_time_ns"],
                    "latency_ms": f"{result['latency_ms']:.3f}",
                    "exit_code": "" if result.get("exit_code") is None else result["exit_code"],
                    "expected_bytes": "",
                    "received_bytes": len(stdout),
                    "expected_sha256": "",
                    "received_sha256": received_hash,
                    "completion_marker_received": int(
                        bool(result.get("completion_marker_received"))
                    ),
                    "output_verifiable": int(output_verifiable),
                    "output_complete": int(output_complete),
                    "timed_out": int(timed_out),
                    "status": status,
                    "stderr_bytes": len(result["stderr"]),
                    "note": note,
                }
                rows.append(row)
                if live_progress and (
                    status != "completed"
                    or sample_index == 1
                    or sample_index == len(sample_plan)
                    or sample_index % live_every == 0
                ):
                    print(
                        f"[LIVE] {trial['trial_id']} {role} "
                        f"sample={sample_index}/{len(sample_plan)} "
                        f"command={sample['command_index']}/{len(COMMANDS)} "
                        f"status={status} latency_ms={row['latency_ms']} "
                        f"complete={int(output_complete)}",
                        flush=True,
                    )
                if timed_out:
                    failure_note = note or "remote command timed out"
                    if continue_after_timeout:
                        continue
                    for remaining_sample in sample_plan[position + 1:]:
                        rows.append(_failed_sample(
                            trial, role, index, stream, remaining_sample,
                            "skipped", failure_note,
                        ))
                    break
            except TimeoutError as exc:
                failure_note = str(exc)
                rows.append(_failed_sample(
                    trial, role, index, stream, sample, "timeout", failure_note,
                ))
                if continue_after_timeout:
                    continue
                for remaining_sample in sample_plan[position + 1:]:
                    rows.append(_failed_sample(
                        trial, role, index, stream, remaining_sample,
                        "skipped", failure_note,
                    ))
                break
            except Exception as exc:
                failure_note = repr(exc)
                rows.append(_failed_sample(
                    trial, role, index, stream, sample, "failure", failure_note,
                ))
                for remaining_sample in sample_plan[position + 1:]:
                    rows.append(_failed_sample(
                        trial, role, index, stream, remaining_sample,
                        "skipped", failure_note,
                    ))
                break
    except Exception as exc:
        failure_note = repr(exc)
        for sample in sample_plan:
            rows.append(_failed_sample(
                trial, role, index, stream, sample, "barrier_failure", failure_note,
            ))
    finally:
        completed_ns = time.time_ns()

    completed = sum(row["status"] == "completed" for row in rows)
    attempted = sum(
        row["status"] in {"completed", "timeout", "failure"} for row in rows
    )
    timeout_count = sum(row["status"] == "timeout" for row in rows)
    skipped_count = sum(row["status"] == "skipped" for row in rows)
    complete_outputs = sum(int(row["output_complete"]) for row in rows)
    verifiable_outputs = sum(int(row["output_verifiable"]) for row in rows)
    expected = len(sample_plan)
    summary = {
        **_sample_base(trial, role, index, stream),
        "expected_commands": expected,
        "attempted_commands": attempted,
        "completed_commands": completed,
        "command_completion_rate_pct": f"{100.0 * completed / expected:.3f}",
        "attempted_completion_rate_pct": (
            f"{100.0 * completed / attempted:.3f}" if attempted else ""
        ),
        "timeout_commands": timeout_count,
        "skipped_commands": skipped_count,
        "complete_outputs": complete_outputs,
        "output_completeness_pct": (
            f"{100.0 * complete_outputs / verifiable_outputs:.3f}"
            if verifiable_outputs else ""
        ),
        "stream_completed": int(completed == expected),
        "started_time_ns": started_ns or "",
        "completed_time_ns": completed_ns,
        "elapsed_ms": f"{(completed_ns - started_ns) / 1_000_000.0:.3f}" if started_ns else "",
        "note": failure_note,
    }
    return rows, summary


# Điền đủ dữ liệu khi trial không thể chạy.
def unavailable_rows(
    trial: dict, roles: list[str], sample_plan: list[dict], status: str, note: str
):
    samples, streams = [], []
    dummy = type("UnavailableStream", (), {"stream_id": "", "conversation_id": ""})()
    for index, role in enumerate(roles):
        for sample in sample_plan:
            samples.append(_failed_sample(
                trial, role, index, dummy, sample, status, note
            ))
        streams.append({
            **_sample_base(trial, role, index, dummy),
            "expected_commands": len(sample_plan), "attempted_commands": 0,
            "completed_commands": 0,
            "command_completion_rate_pct": "0.000", "complete_outputs": 0,
            "attempted_completion_rate_pct": "", "timeout_commands": 0,
            "skipped_commands": 0,
            "output_completeness_pct": "0.000", "stream_completed": 0,
            "started_time_ns": "", "completed_time_ns": "", "elapsed_ms": "",
            "note": note,
        })
    return samples, streams


# Mở connection, đồng bộ stream và chạy một trial.
def run_trial(cfg: dict, trial: dict, connection_factory):
    roles = [f"command_{index}" for index in range(trial["stream_count"])]
    timeout = float(cfg.get("COMMAND_TIMEOUT", "30"))
    ready_timeout = float(cfg.get("STREAM_READY_TIMEOUT", "20"))
    warmup = float(cfg.get("WARMUP_SECONDS", "5"))
    live = cfg.get("LIVE_PROGRESS", "1") == "1"
    samples_per_stream = int(cfg.get("SAMPLES_PER_STREAM_PER_TRIAL", "100"))
    live_every = int(cfg.get("LIVE_PROGRESS_EVERY", "10"))
    sample_plan = build_sample_plan(samples_per_stream)
    continue_after_timeout = (
        trial["protocol"] == "mosh"
        and cfg.get("MOSH_CONTINUE_AFTER_TIMEOUT", "1") == "1"
    )
    if live_every <= 0:
        raise ValueError("LIVE_PROGRESS_EVERY must be positive")
    connection = connection_factory(cfg, trial["protocol"], roles, trial["trial_tag"])
    setup_started = time.perf_counter_ns()
    workload_started = 0
    samples, stream_rows = [], []
    note = ""
    try:
        streams = connection.open(ready_timeout)
        setup_ms = (time.perf_counter_ns() - setup_started) / 1_000_000.0
        # READY đã được đọc hết nên buffer sạch trước warm-up chung.
        time.sleep(warmup)
        barrier = threading.Barrier(len(roles))
        workload_started = time.perf_counter_ns()
        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            futures = {
                pool.submit(
                    run_stream, trial, role, index, streams[role], barrier,
                    timeout, sample_plan, live, live_every,
                    continue_after_timeout,
                ): role
                for index, role in enumerate(roles)
            }
            for future in as_completed(futures):
                rows, summary = future.result()
                samples.extend(rows)
                stream_rows.append(summary)
        workload_ms = (time.perf_counter_ns() - workload_started) / 1_000_000.0
        status = "completed" if all(row["stream_completed"] for row in stream_rows) else "partial"
    except Exception as exc:
        note = repr(exc)
        setup_ms = (time.perf_counter_ns() - setup_started) / 1_000_000.0
        workload_ms = ""
        status = "trial_unavailable"
        samples, stream_rows = unavailable_rows(
            trial, roles, sample_plan, status, note
        )
    finally:
        try:
            connection.close()
        except Exception as exc:
            note = f"{note}; close={exc!r}" if note else f"close={exc!r}"

    samples.sort(key=lambda row: (int(row["stream_index"]), int(row["sample_index"])))
    stream_rows.sort(key=lambda row: int(row["stream_index"]))
    completed_commands = sum(int(row["completed_commands"]) for row in stream_rows)
    attempted_commands = sum(int(row["attempted_commands"]) for row in stream_rows)
    timeout_commands = sum(int(row["timeout_commands"]) for row in stream_rows)
    skipped_commands = sum(int(row["skipped_commands"]) for row in stream_rows)
    complete_outputs = sum(int(row["complete_outputs"]) for row in stream_rows)
    verifiable_outputs = sum(
        int(row["output_verifiable"]) for row in samples
    )
    completed_streams = sum(int(row["stream_completed"]) for row in stream_rows)
    expected_commands = len(sample_plan) * len(roles)
    audit = connection.audit
    trial_row = {
        **trial,
        "connection_valid": int(audit.valid),
        "connection_pid": audit.connection_pid or "",
        "socket_count": audit.socket_count,
        "opened_streams": audit.stream_count,
        "unique_transport_streams": len({
            value for value in audit.stream_ids.values() if value != ""
        }),
        "conversation_count": len(audit.conversation_ids),
        "ready_streams": sum(bool(row["started_time_ns"]) for row in stream_rows),
        "expected_commands": expected_commands,
        "attempted_commands": attempted_commands,
        "completed_commands": completed_commands,
        "command_completion_rate_pct": f"{100.0 * completed_commands / expected_commands:.3f}",
        "attempted_completion_rate_pct": (
            f"{100.0 * completed_commands / attempted_commands:.3f}"
            if attempted_commands else ""
        ),
        "timeout_commands": timeout_commands,
        "skipped_commands": skipped_commands,
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": f"{100.0 * completed_streams / len(roles):.3f}",
        "complete_outputs": complete_outputs,
        "output_completeness_pct": (
            f"{100.0 * complete_outputs / verifiable_outputs:.3f}"
            if verifiable_outputs else ""
        ),
        "setup_ms": f"{setup_ms:.3f}",
        "workload_elapsed_ms": f"{workload_ms:.3f}" if isinstance(workload_ms, float) else "",
        "status": status,
        "note": note or audit.note,
    }
    return samples, stream_rows, trial_row, audit.to_dict()
