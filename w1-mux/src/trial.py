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


# Tạo một mẫu lỗi chưa có kết quả lệnh.
def _failed_sample(trial, role, index, stream, command_index, command, status, note):
    return {
        **_sample_base(trial, role, index, stream),
        "command_index": command_index,
        "command": command,
        "request_id": f"{trial['trial_id']}:{role}:{command_index}",
        "send_time_ns": "",
        "completion_time_ns": "",
        "latency_ms": "",
        "exit_code": "",
        "expected_bytes": "",
        "received_bytes": "",
        "expected_sha256": "",
        "received_sha256": "",
        "output_complete": 0,
        "timed_out": int(status == "timeout"),
        "status": status,
        "stderr_bytes": "",
        "note": note,
    }


# Chạy tuần tự năm lệnh trên một stream.
def run_stream(trial, role, index, stream, barrier, command_timeout, live_progress):
    rows = []
    started_ns = 0
    completed_ns = 0
    failure_note = ""
    try:
        barrier.wait(timeout=command_timeout)
        started_ns = time.time_ns()
        for command_index, command in enumerate(COMMANDS, start=1):
            request_id = f"{trial['trial_id']}:{role}:{command_index}"
            try:
                result = stream.execute(request_id, command, command_timeout)
                stdout = result["stdout"]
                received_hash = hashlib.sha256(stdout).hexdigest()
                expected_bytes = int(result["expected_stdout_bytes"])
                expected_hash = str(result["expected_stdout_sha256"])
                output_complete = (
                    len(stdout) == expected_bytes and received_hash == expected_hash
                )
                timed_out = bool(result.get("timed_out"))
                status = "timeout" if timed_out else "completed"
                note = str(result.get("error", ""))
                row = {
                    **_sample_base(trial, role, index, stream),
                    "command_index": command_index,
                    "command": command,
                    "request_id": request_id,
                    "send_time_ns": result["send_time_ns"],
                    "completion_time_ns": result["completion_time_ns"],
                    "latency_ms": f"{result['latency_ms']:.3f}",
                    "exit_code": "" if result.get("exit_code") is None else result["exit_code"],
                    "expected_bytes": expected_bytes,
                    "received_bytes": len(stdout),
                    "expected_sha256": expected_hash,
                    "received_sha256": received_hash,
                    "output_complete": int(output_complete),
                    "timed_out": int(timed_out),
                    "status": status,
                    "stderr_bytes": len(result["stderr"]),
                    "note": note,
                }
                rows.append(row)
                if live_progress:
                    print(
                        f"[LIVE] {trial['trial_id']} {role} command={command_index}/5 "
                        f"status={status} latency_ms={row['latency_ms']} "
                        f"complete={int(output_complete)}",
                        flush=True,
                    )
                if timed_out:
                    failure_note = note or "remote command timed out"
                    for rest_index in range(command_index + 1, len(COMMANDS) + 1):
                        rows.append(_failed_sample(
                            trial, role, index, stream, rest_index,
                            COMMANDS[rest_index - 1], "skipped", failure_note,
                        ))
                    break
            except TimeoutError as exc:
                failure_note = str(exc)
                rows.append(_failed_sample(
                    trial, role, index, stream, command_index, command,
                    "timeout", failure_note,
                ))
                for rest_index in range(command_index + 1, len(COMMANDS) + 1):
                    rows.append(_failed_sample(
                        trial, role, index, stream, rest_index,
                        COMMANDS[rest_index - 1], "skipped", failure_note,
                    ))
                break
            except Exception as exc:
                failure_note = repr(exc)
                rows.append(_failed_sample(
                    trial, role, index, stream, command_index, command,
                    "failure", failure_note,
                ))
                for rest_index in range(command_index + 1, len(COMMANDS) + 1):
                    rows.append(_failed_sample(
                        trial, role, index, stream, rest_index,
                        COMMANDS[rest_index - 1], "skipped", failure_note,
                    ))
                break
    except Exception as exc:
        failure_note = repr(exc)
        for command_index, command in enumerate(COMMANDS, start=1):
            rows.append(_failed_sample(
                trial, role, index, stream, command_index, command,
                "barrier_failure", failure_note,
            ))
    finally:
        completed_ns = time.time_ns()

    completed = sum(row["status"] == "completed" for row in rows)
    complete_outputs = sum(int(row["output_complete"]) for row in rows)
    expected = len(COMMANDS)
    summary = {
        **_sample_base(trial, role, index, stream),
        "expected_commands": expected,
        "completed_commands": completed,
        "command_completion_rate_pct": f"{100.0 * completed / expected:.3f}",
        "complete_outputs": complete_outputs,
        "output_completeness_pct": f"{100.0 * complete_outputs / expected:.3f}",
        "stream_completed": int(completed == expected),
        "started_time_ns": started_ns or "",
        "completed_time_ns": completed_ns,
        "elapsed_ms": f"{(completed_ns - started_ns) / 1_000_000.0:.3f}" if started_ns else "",
        "note": failure_note,
    }
    return rows, summary


# Điền đủ dữ liệu khi trial không thể chạy.
def unavailable_rows(trial: dict, roles: list[str], status: str, note: str):
    samples, streams = [], []
    dummy = type("UnavailableStream", (), {"stream_id": "", "conversation_id": ""})()
    for index, role in enumerate(roles):
        for command_index, command in enumerate(COMMANDS, start=1):
            samples.append(_failed_sample(
                trial, role, index, dummy, command_index, command, status, note
            ))
        streams.append({
            **_sample_base(trial, role, index, dummy),
            "expected_commands": len(COMMANDS), "completed_commands": 0,
            "command_completion_rate_pct": "0.000", "complete_outputs": 0,
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
                    timeout, live,
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
        samples, stream_rows = unavailable_rows(trial, roles, status, note)
    finally:
        try:
            connection.close()
        except Exception as exc:
            note = f"{note}; close={exc!r}" if note else f"close={exc!r}"

    samples.sort(key=lambda row: (int(row["stream_index"]), int(row["command_index"])))
    stream_rows.sort(key=lambda row: int(row["stream_index"]))
    completed_commands = sum(int(row["completed_commands"]) for row in stream_rows)
    complete_outputs = sum(int(row["complete_outputs"]) for row in stream_rows)
    completed_streams = sum(int(row["stream_completed"]) for row in stream_rows)
    expected_commands = len(COMMANDS) * len(roles)
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
        "completed_commands": completed_commands,
        "command_completion_rate_pct": f"{100.0 * completed_commands / expected_commands:.3f}",
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": f"{100.0 * completed_streams / len(roles):.3f}",
        "complete_outputs": complete_outputs,
        "output_completeness_pct": f"{100.0 * complete_outputs / expected_commands:.3f}",
        "setup_ms": f"{setup_ms:.3f}",
        "workload_elapsed_ms": f"{workload_ms:.3f}" if isinstance(workload_ms, float) else "",
        "status": status,
        "note": note or audit.note,
    }
    return samples, stream_rows, trial_row, audit.to_dict()
