"""Chạy một trial W2 và đánh giá toàn vẹn từng payload."""

from __future__ import annotations

import hashlib
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


# Tạo phần định danh chung cho một output stream.
def _stream_base(trial: dict, role: str, index: int, stream) -> dict:
    return {
        **trial,
        "stream_role": role,
        "stream_index": index,
        "transport_stream_id": stream.stream_id,
        "conversation_stream_id": stream.conversation_id,
    }


# Định dạng số mili giây có thể bị thiếu.
def _fmt_ms(value) -> str:
    return "" if value is None else f"{value:.3f}"


# Tạo dòng lỗi khi stream không hoàn tất phép truyền.
def _failed_transfer(
    trial: dict, role: str, index: int, stream,
    payload: dict, remote_path: str, status: str, note: str,
) -> dict:
    return {
        **_stream_base(trial, role, index, stream),
        "payload_name": payload["name"],
        "remote_payload_path": remote_path,
        "request_id": f"{trial['trial_id']}:{role}",
        "send_time_ns": "",
        "first_byte_time_ns": "",
        "last_byte_time_ns": "",
        "marker_time_ns": "",
        "first_byte_latency_ms": "",
        "completion_latency_ms": "",
        "marker_latency_ms": "",
        "exit_code": "",
        "expected_bytes": payload["bytes"],
        "received_bytes": 0,
        "expected_lines": payload["lines"],
        "received_lines": 0,
        "expected_sha256": payload["sha256"],
        "received_sha256": hashlib.sha256(b"").hexdigest(),
        "throughput_mib_s": "",
        "completion_marker_received": 0,
        "bytes_complete": 0,
        "lines_complete": 0,
        "hash_complete": 0,
        "output_complete": 0,
        "timed_out": int(status == "timeout"),
        "status": status,
        "note": note,
    }


# Truyền một payload và so sánh byte, dòng cùng SHA-256.
def run_stream(
    trial: dict, role: str, index: int, stream,
    payload: dict, remote_dir: str, barrier,
    timeout: float, live_progress: bool,
):
    remote_path = f"{remote_dir.rstrip('/')}/{payload['name']}"
    command = f"cat -- {shlex.quote(remote_path)}"
    request_id = f"{trial['trial_id']}:{role}"
    started_ns = 0
    completed_ns = 0
    note = ""
    try:
        barrier.wait(timeout=timeout)
        started_ns = time.time_ns()
        result = stream.execute(
            request_id,
            command,
            payload["line_prefix"].encode("ascii"),
            timeout,
        )
        output = result["stdout"]
        received_bytes = len(output)
        received_lines = output.count(b"\n")
        received_hash = hashlib.sha256(output).hexdigest()
        bytes_complete = received_bytes == int(payload["bytes"])
        lines_complete = received_lines == int(payload["lines"])
        hash_complete = received_hash == payload["sha256"]
        marker_received = bool(result.get("completion_marker_received"))
        exit_ok = result.get("exit_code") == 0
        output_complete = (
            marker_received and exit_ok and bytes_complete
            and lines_complete and hash_complete
            and not result.get("output_truncated")
            and not result.get("output_ambiguous")
        )
        status = "completed" if output_complete else "partial"
        completion_latency = result.get("completion_latency_ms")
        throughput = (
            (received_bytes / 1_048_576.0) / (completion_latency / 1000.0)
            if completion_latency and received_bytes else None
        )
        problems = []
        if result.get("output_ambiguous"):
            problems.append("output terminal bị xen giữa các tác vụ")
        if result.get("output_truncated"):
            problems.append("output vượt giới hạn vùng giữ")
        if not exit_ok:
            problems.append(f"mã thoát={result.get('exit_code')}")
        if not bytes_complete:
            problems.append(f"byte={received_bytes}/{payload['bytes']}")
        if not lines_complete:
            problems.append(f"dòng={received_lines}/{payload['lines']}")
        if not hash_complete:
            problems.append("SHA-256 không khớp")
        note = "; ".join(problems)
        row = {
            **_stream_base(trial, role, index, stream),
            "payload_name": payload["name"],
            "remote_payload_path": remote_path,
            "request_id": request_id,
            "send_time_ns": result["send_time_ns"],
            "first_byte_time_ns": result.get("first_byte_time_ns") or "",
            "last_byte_time_ns": result.get("last_byte_time_ns") or "",
            "marker_time_ns": result.get("marker_time_ns") or "",
            "first_byte_latency_ms": _fmt_ms(
                result.get("first_byte_latency_ms")
            ),
            "completion_latency_ms": _fmt_ms(completion_latency),
            "marker_latency_ms": _fmt_ms(result.get("marker_latency_ms")),
            "exit_code": (
                "" if result.get("exit_code") is None else result["exit_code"]
            ),
            "expected_bytes": payload["bytes"],
            "received_bytes": received_bytes,
            "expected_lines": payload["lines"],
            "received_lines": received_lines,
            "expected_sha256": payload["sha256"],
            "received_sha256": received_hash,
            "throughput_mib_s": (
                "" if throughput is None else f"{throughput:.3f}"
            ),
            "completion_marker_received": int(marker_received),
            "bytes_complete": int(bytes_complete),
            "lines_complete": int(lines_complete),
            "hash_complete": int(hash_complete),
            "output_complete": int(output_complete),
            "timed_out": 0,
            "status": status,
            "note": note,
        }
    except TimeoutError as exc:
        note = str(exc)
        row = _failed_transfer(
            trial, role, index, stream, payload, remote_path, "timeout", note
        )
    except Exception as exc:
        note = repr(exc)
        row = _failed_transfer(
            trial, role, index, stream, payload, remote_path, "failure", note
        )
    finally:
        completed_ns = time.time_ns()

    stream_completed = int(bool(row["completion_marker_received"]))
    summary = {
        **_stream_base(trial, role, index, stream),
        "payload_name": payload["name"],
        "expected_transfers": 1,
        "completed_transfers": int(row["status"] == "completed"),
        "transfer_completion_rate_pct": (
            "100.000" if row["status"] == "completed" else "0.000"
        ),
        "completion_marker_received": row["completion_marker_received"],
        "output_complete": row["output_complete"],
        "stream_completed": stream_completed,
        "started_time_ns": started_ns or "",
        "completed_time_ns": completed_ns,
        "elapsed_ms": (
            f"{(completed_ns - started_ns) / 1_000_000.0:.3f}"
            if started_ns else ""
        ),
        "note": note,
    }
    if live_progress:
        print(
            f"[LIVE] {trial['trial_id']} {role} status={row['status']} "
            f"bytes={row['received_bytes']}/{row['expected_bytes']} "
            f"latency_ms={row['completion_latency_ms'] or 'N/A'}",
            flush=True,
        )
    return row, summary


# Điền kết quả khi trial không thể mở connection.
def unavailable_rows(
    trial: dict, roles: list[str], payloads: list[dict],
    remote_dir: str, status: str, note: str,
):
    dummy = type("UnavailableStream", (), {
        "stream_id": "", "conversation_id": ""
    })()
    transfers, streams = [], []
    for index, role in enumerate(roles):
        payload = payloads[index]
        remote_path = f"{remote_dir.rstrip('/')}/{payload['name']}"
        transfer = _failed_transfer(
            trial, role, index, dummy, payload, remote_path, status, note
        )
        transfers.append(transfer)
        streams.append({
            **_stream_base(trial, role, index, dummy),
            "payload_name": payload["name"],
            "expected_transfers": 1,
            "completed_transfers": 0,
            "transfer_completion_rate_pct": "0.000",
            "completion_marker_received": 0,
            "output_complete": 0,
            "stream_completed": 0,
            "started_time_ns": "",
            "completed_time_ns": "",
            "elapsed_ms": "",
            "note": note,
        })
    return transfers, streams


# Mở connection, đồng bộ các stream rồi chạy một trial.
def run_trial(
    cfg: dict, trial: dict, payloads: list[dict], connection_factory,
):
    roles = [f"output_{index}" for index in range(trial["stream_count"])]
    selected_payloads = payloads[:trial["stream_count"]]
    remote_dir = cfg.get("W2_REMOTE_PAYLOAD_DIR", "/tmp/w2_mux_tt_payloads")
    timeout = float(cfg.get("TRANSFER_TIMEOUT", "120"))
    ready_timeout = float(cfg.get("STREAM_READY_TIMEOUT", "20"))
    warmup = float(cfg.get("WARMUP_SECONDS", "5"))
    live = cfg.get("LIVE_PROGRESS", "1") == "1"
    connection = connection_factory(
        cfg, trial["protocol"], roles, trial["trial_tag"]
    )
    setup_started = time.perf_counter_ns()
    workload_started = 0
    transfers, stream_rows = [], []
    note = ""
    try:
        streams = connection.open(ready_timeout)
        setup_ms = (time.perf_counter_ns() - setup_started) / 1_000_000.0
        time.sleep(warmup)
        connection.prepare_workload(ready_timeout)
        barrier = threading.Barrier(len(roles))
        workload_started = time.perf_counter_ns()
        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            futures = {
                pool.submit(
                    run_stream,
                    trial, role, index, streams[role], selected_payloads[index],
                    remote_dir, barrier, timeout, live,
                ): role
                for index, role in enumerate(roles)
            }
            for future in as_completed(futures):
                transfer, summary = future.result()
                transfers.append(transfer)
                stream_rows.append(summary)
        workload_ms = (time.perf_counter_ns() - workload_started) / 1_000_000.0
        if all(row["status"] == "completed" for row in transfers):
            status = "completed"
        elif all(row["completion_marker_received"] for row in transfers):
            status = "partial"
        else:
            status = "failed"
    except Exception as exc:
        note = repr(exc)
        setup_ms = (time.perf_counter_ns() - setup_started) / 1_000_000.0
        workload_ms = ""
        status = "trial_unavailable"
        transfers, stream_rows = unavailable_rows(
            trial, roles, selected_payloads, remote_dir, status, note
        )
    finally:
        try:
            connection.close()
        except Exception as exc:
            note = f"{note}; close={exc!r}" if note else f"close={exc!r}"

    transfers.sort(key=lambda row: int(row["stream_index"]))
    stream_rows.sort(key=lambda row: int(row["stream_index"]))
    completed_transfers = sum(
        row["status"] == "completed" for row in transfers
    )
    completed_streams = sum(
        int(row["stream_completed"]) for row in stream_rows
    )
    complete_outputs = sum(int(row["output_complete"]) for row in transfers)
    expected = len(roles)
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
        "ready_streams": sum(
            bool(row["started_time_ns"]) for row in stream_rows
        ),
        "expected_transfers": expected,
        "completed_transfers": completed_transfers,
        "transfer_completion_rate_pct": (
            f"{100.0 * completed_transfers / expected:.3f}"
        ),
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": (
            f"{100.0 * completed_streams / expected:.3f}"
        ),
        "complete_outputs": complete_outputs,
        "output_completeness_pct": (
            f"{100.0 * complete_outputs / expected:.3f}"
        ),
        "setup_ms": f"{setup_ms:.3f}",
        "workload_elapsed_ms": (
            f"{workload_ms:.3f}" if isinstance(workload_ms, float) else ""
        ),
        "status": status,
        "note": note or audit.note,
    }
    return transfers, stream_rows, trial_row, audit.to_dict()
