"""Chạy một trial W2 và đánh giá toàn vẹn từng payload."""

from __future__ import annotations

import hashlib
import shlex
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


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


# Tính riêng tỷ lệ transfer đủ hoàn toàn và tỷ lệ byte nội dung đã xác thực.
def _verified_output_metrics(rows: list[dict]) -> dict:
    attempted = [
        row for row in rows
        if row["status"] in {"completed", "partial", "timeout", "failure"}
    ]
    expected_bytes = sum(int(row["expected_bytes"]) for row in rows)
    verified_bytes = sum(int(row["verified_bytes"]) for row in rows)
    attempted_expected_bytes = sum(
        int(row["expected_bytes"]) for row in attempted
    )
    attempted_verified_bytes = sum(
        int(row["verified_bytes"]) for row in attempted
    )
    fully_verified = sum(
        int(row["bytes_complete"])
        and int(row["lines_complete"])
        and int(row["hash_complete"])
        for row in rows
    )
    attempted_fully_verified = sum(
        int(row["bytes_complete"])
        and int(row["lines_complete"])
        and int(row["hash_complete"])
        for row in attempted
    )
    return {
        "fully_verified_output_rate_pct": (
            f"{100.0 * fully_verified / len(rows):.3f}" if rows else ""
        ),
        "attempted_fully_verified_output_rate_pct": (
            f"{100.0 * attempted_fully_verified / len(attempted):.3f}"
            if attempted else ""
        ),
        "expected_output_bytes": expected_bytes,
        "verified_output_bytes": verified_bytes,
        "verified_output_ratio_pct": (
            f"{100.0 * verified_bytes / expected_bytes:.3f}"
            if expected_bytes else ""
        ),
        "attempted_expected_output_bytes": attempted_expected_bytes,
        "attempted_verified_output_bytes": attempted_verified_bytes,
        "attempted_verified_output_ratio_pct": (
            f"{100.0 * attempted_verified_bytes / attempted_expected_bytes:.3f}"
            if attempted_expected_bytes else ""
        ),
    }


# Đọc tập dòng payload chuẩn để tính độ bao phủ nội dung.
def _load_expected_lines(payload_dir: Path, payload: dict) -> tuple[bytes, ...]:
    raw = (payload_dir / payload["name"]).read_bytes()
    lines = raw.splitlines(keepends=True)
    if len(lines) != int(payload["lines"]) or len(set(lines)) != len(lines):
        raise ValueError(f"payload không có các dòng duy nhất: {payload['name']}")
    return tuple(lines)


# Tạo dòng lỗi khi stream không hoàn tất phép truyền.
def _failed_transfer(
    trial: dict, role: str, index: int, stream,
    payload: dict, remote_path: str, sample_index: int,
    status: str, note: str,
) -> dict:
    return {
        **_stream_base(trial, role, index, stream),
        "payload_name": payload["name"],
        "remote_payload_path": remote_path,
        "sample_index": sample_index,
        "request_id": f"{trial['trial_id']}:{role}:{sample_index}",
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
        "raw_byte_ratio_pct": "0.000",
        "verified_bytes": 0,
        "verified_byte_ratio_pct": "0.000",
        "overrun_bytes": 0,
        "expected_lines": payload["lines"],
        "received_lines": 0,
        "valid_unique_lines": 0,
        "missing_lines": payload["lines"],
        "duplicate_lines": 0,
        "invalid_lines": 0,
        "content_coverage_pct": "0.000",
        "expected_sha256": payload["sha256"],
        "received_sha256": hashlib.sha256(b"").hexdigest(),
        "verified_sha256": hashlib.sha256(b"").hexdigest(),
        "verification_mode": (
            "terminal_content_reconstruction"
            if trial["protocol"] == "mosh" else "lossless_byte_stream"
        ),
        "throughput_mib_s": "",
        "completion_marker_received": 0,
        "bytes_complete": 0,
        "lines_complete": 0,
        "hash_complete": 0,
        "raw_capture_exact": 0,
        "output_complete": 0,
        "timed_out": int(status == "timeout"),
        "status": status,
        "note": note,
    }


# Truyền tuần tự toàn bộ mẫu trên một stream và kiểm tra từng output.
def run_stream(
    trial: dict, role: str, index: int, stream,
    payload: dict, remote_dir: str, sample_barrier,
    timeout: float, sample_count: int,
    live_progress: bool, live_every: int,
    continue_after_timeout: bool = False,
    barrier_timeout=None,
):
    remote_path = f"{remote_dir.rstrip('/')}/{payload['name']}"
    command = f"cat {shlex.quote(remote_path)}"
    started_ns = 0
    completed_ns = 0
    failure_note = ""
    rows = []
    try:
        started_ns = time.time_ns()
        for sample_index in range(1, sample_count + 1):
            request_id = f"{trial['trial_id']}:{role}:{sample_index}"
            try:
                # Mỗi vòng đều bắt đầu đủ S1/S2/S4 vai trò cùng lúc. Điều này
                # ngăn một stream chạy trước nhiều payload, đặc biệt quan trọng
                # với một viewport dùng chung của Mosh.
                sample_barrier.wait(timeout=barrier_timeout or timeout)
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
                expected_bytes = int(payload["bytes"])
                raw_byte_ratio_pct = (
                    100.0 * received_bytes / expected_bytes
                )
                observed_lines = output.splitlines(keepends=True)
                expected_line_list = payload["_expected_lines"]
                # Mosh xuất các bản cập nhật màn hình có thể vẽ lại dòng và
                # chèn cursor sequence. Tìm chính xác từng dòng deterministic
                # trong capture cho phép xác thực nội dung mà không nhầm raw
                # terminal-update bytes là payload bytes.
                expected_line_set = frozenset(expected_line_list)
                observed_counts = Counter(
                    line for line in observed_lines if line in expected_line_set
                )
                line_counts = [observed_counts.get(line, 0) for line in expected_line_list]
                valid_unique_lines = sum(count > 0 for count in line_counts)
                duplicate_lines = sum(max(0, count - 1) for count in line_counts)
                valid_occurrences = sum(line_counts)
                invalid_lines = max(0, len(observed_lines) - valid_occurrences)
                missing_lines = int(payload["lines"]) - valid_unique_lines
                content_coverage_pct = (
                    100.0 * valid_unique_lines / int(payload["lines"])
                )
                verified_output = b"".join(
                    line for line, count in zip(expected_line_list, line_counts)
                    if count > 0
                )
                verified_bytes = len(verified_output)
                verified_byte_ratio_pct = 100.0 * verified_bytes / expected_bytes
                verified_hash = hashlib.sha256(verified_output).hexdigest()
                bytes_complete = verified_bytes == expected_bytes
                lines_complete = valid_unique_lines == int(payload["lines"])
                hash_complete = verified_hash == payload["sha256"]
                raw_capture_exact = (
                    received_bytes == expected_bytes
                    and received_lines == int(payload["lines"])
                    and received_hash == payload["sha256"]
                )
                marker_received = bool(
                    result.get("completion_marker_received")
                )
                exit_ok = result.get("exit_code") == 0
                screen_verified = (
                    bytes_complete and lines_complete and hash_complete
                    and not result.get("output_truncated")
                )
                output_complete = (
                    marker_received and exit_ok and screen_verified
                    and (
                        trial["protocol"] == "mosh"
                        or (
                            raw_capture_exact
                            and not result.get("output_ambiguous")
                        )
                    )
                )
                timed_out = bool(result.get("timed_out"))
                status = "timeout" if timed_out else (
                    "completed" if output_complete else "partial"
                )
                completion_latency = result.get("completion_latency_ms")
                throughput = (
                    (verified_bytes / 1_048_576.0)
                    / (completion_latency / 1000.0)
                    if completion_latency and verified_bytes else None
                )
                problems = []
                if timed_out:
                    problems.append("không nhận dấu hoàn thành trước timeout")
                if result.get("output_ambiguous"):
                    problems.append("output terminal bị xen giữa các tác vụ")
                if result.get("output_truncated"):
                    problems.append("output vượt giới hạn vùng giữ")
                if not exit_ok:
                    problems.append(f"mã thoát={result.get('exit_code')}")
                if not bytes_complete:
                    problems.append(
                        f"byte xác thực={verified_bytes}/{payload['bytes']}"
                    )
                if not lines_complete:
                    problems.append(
                        f"dòng hợp lệ={valid_unique_lines}/{payload['lines']}"
                    )
                if not hash_complete:
                    problems.append("SHA-256 xác thực không khớp")
                if trial["protocol"] != "mosh" and not raw_capture_exact:
                    problems.append("capture byte-stream không khớp nguyên bản")
                note = "; ".join(problems)
                row = {
                    **_stream_base(trial, role, index, stream),
                    "payload_name": payload["name"],
                    "remote_payload_path": remote_path,
                    "sample_index": sample_index,
                    "request_id": request_id,
                    "send_time_ns": result["send_time_ns"],
                    "first_byte_time_ns": (
                        result.get("first_byte_time_ns") or ""
                    ),
                    "last_byte_time_ns": (
                        result.get("last_byte_time_ns") or ""
                    ),
                    "marker_time_ns": result.get("marker_time_ns") or "",
                    "first_byte_latency_ms": _fmt_ms(
                        result.get("first_byte_latency_ms")
                    ),
                    "completion_latency_ms": _fmt_ms(completion_latency),
                    "marker_latency_ms": _fmt_ms(
                        result.get("marker_latency_ms")
                    ),
                    "exit_code": (
                        "" if result.get("exit_code") is None
                        else result["exit_code"]
                    ),
                    "expected_bytes": payload["bytes"],
                    "received_bytes": received_bytes,
                    "raw_byte_ratio_pct": f"{raw_byte_ratio_pct:.3f}",
                    "verified_bytes": verified_bytes,
                    "verified_byte_ratio_pct": f"{verified_byte_ratio_pct:.3f}",
                    "overrun_bytes": max(0, received_bytes - expected_bytes),
                    "expected_lines": payload["lines"],
                    "received_lines": received_lines,
                    "valid_unique_lines": valid_unique_lines,
                    "missing_lines": missing_lines,
                    "duplicate_lines": duplicate_lines,
                    "invalid_lines": invalid_lines,
                    "content_coverage_pct": f"{content_coverage_pct:.3f}",
                    "expected_sha256": payload["sha256"],
                    "received_sha256": received_hash,
                    "verified_sha256": verified_hash,
                    "verification_mode": (
                        "terminal_content_reconstruction"
                        if trial["protocol"] == "mosh"
                        else "lossless_byte_stream"
                    ),
                    "throughput_mib_s": (
                        "" if throughput is None else f"{throughput:.3f}"
                    ),
                    "completion_marker_received": int(marker_received),
                    "bytes_complete": int(bytes_complete),
                    "lines_complete": int(lines_complete),
                    "hash_complete": int(hash_complete),
                    "raw_capture_exact": int(raw_capture_exact),
                    "output_complete": int(output_complete),
                    "timed_out": int(timed_out),
                    "status": status,
                    "note": note,
                }
                rows.append(row)
                if live_progress and (
                    status != "completed"
                    or sample_index == 1
                    or sample_index == sample_count
                    or sample_index % live_every == 0
                ):
                    print(
                        f"[LIVE] {trial['trial_id']} {role} "
                        f"sample={sample_index}/{sample_count} "
                        f"status={status} "
                        f"verified_bytes={verified_bytes}/{expected_bytes} "
                        f"coverage_pct={row['content_coverage_pct']} "
                        f"raw_byte_pct={row['raw_byte_ratio_pct']} "
                        f"latency_ms={row['completion_latency_ms'] or 'N/A'}",
                        flush=True,
                    )
                if timed_out:
                    failure_note = note
                    if continue_after_timeout:
                        continue
                    sample_barrier.abort()
                    for remaining in range(sample_index + 1, sample_count + 1):
                        rows.append(_failed_transfer(
                            trial, role, index, stream, payload, remote_path,
                            remaining, "skipped", failure_note,
                        ))
                    break
            except Exception as exc:
                failure_note = repr(exc)
                sample_barrier.abort()
                rows.append(_failed_transfer(
                    trial, role, index, stream, payload, remote_path,
                    sample_index, "failure", failure_note,
                ))
                for remaining in range(sample_index + 1, sample_count + 1):
                    rows.append(_failed_transfer(
                        trial, role, index, stream, payload, remote_path,
                        remaining, "skipped", failure_note,
                    ))
                break
    except Exception as exc:
        failure_note = repr(exc)
        rows = [
            _failed_transfer(
                trial, role, index, stream, payload, remote_path,
                sample_index, "barrier_failure", failure_note,
            )
            for sample_index in range(1, sample_count + 1)
        ]
    finally:
        completed_ns = time.time_ns()

    completed = sum(row["status"] == "completed" for row in rows)
    attempted = sum(
        row["status"] in {"completed", "partial", "timeout", "failure"}
        for row in rows
    )
    partial_count = sum(row["status"] == "partial" for row in rows)
    timeout_count = sum(row["status"] == "timeout" for row in rows)
    skipped_count = sum(row["status"] == "skipped" for row in rows)
    markers = sum(int(row["completion_marker_received"]) for row in rows)
    complete_outputs = sum(int(row["output_complete"]) for row in rows)
    coverage_rates = [float(row["content_coverage_pct"]) for row in rows]
    verified_byte_rates = [
        float(row["verified_byte_ratio_pct"]) for row in rows
    ]
    raw_byte_rates = [float(row["raw_byte_ratio_pct"]) for row in rows]
    verified_output = _verified_output_metrics(rows)
    summary = {
        **_stream_base(trial, role, index, stream),
        "payload_name": payload["name"],
        "expected_transfers": sample_count,
        "attempted_transfers": attempted,
        "completed_transfers": completed,
        "partial_transfers": partial_count,
        "timeout_transfers": timeout_count,
        "skipped_transfers": skipped_count,
        "transfer_completion_rate_pct": f"{100.0 * completed / sample_count:.3f}",
        "attempted_transfer_completion_rate_pct": (
            f"{100.0 * completed / attempted:.3f}" if attempted else ""
        ),
        "completion_markers_received": markers,
        "complete_outputs": complete_outputs,
        "output_completeness_pct": (
            f"{100.0 * complete_outputs / sample_count:.3f}"
        ),
        **verified_output,
        "mean_content_coverage_pct": (
            f"{sum(coverage_rates) / sample_count:.3f}"
        ),
        "mean_verified_byte_ratio_pct": (
            f"{sum(verified_byte_rates) / sample_count:.3f}"
        ),
        "mean_raw_byte_ratio_pct": (
            f"{sum(raw_byte_rates) / sample_count:.3f}"
        ),
        "byte_verification_rate_pct": (
            f"{100.0 * sum(int(row['bytes_complete']) for row in rows) / sample_count:.3f}"
        ),
        "hash_verification_rate_pct": (
            f"{100.0 * sum(int(row['hash_complete']) for row in rows) / sample_count:.3f}"
        ),
        "stream_completed": int(completed == sample_count),
        "started_time_ns": started_ns or "",
        "completed_time_ns": completed_ns,
        "elapsed_ms": (
            f"{(completed_ns - started_ns) / 1_000_000.0:.3f}"
            if started_ns else ""
        ),
        "note": failure_note,
    }
    return rows, summary


# Điền kết quả khi trial không thể mở connection.
def unavailable_rows(
    trial: dict, roles: list[str], payloads: list[dict],
    remote_dir: str, sample_count: int, status: str, note: str,
):
    dummy = type("UnavailableStream", (), {
        "stream_id": "", "conversation_id": ""
    })()
    transfers, streams = [], []
    for index, role in enumerate(roles):
        payload = payloads[index]
        remote_path = f"{remote_dir.rstrip('/')}/{payload['name']}"
        for sample_index in range(1, sample_count + 1):
            transfers.append(_failed_transfer(
                trial, role, index, dummy, payload, remote_path,
                sample_index, status, note,
            ))
        streams.append({
            **_stream_base(trial, role, index, dummy),
            "payload_name": payload["name"],
            "expected_transfers": sample_count,
            "attempted_transfers": 0,
            "completed_transfers": 0,
            "partial_transfers": 0,
            "timeout_transfers": 0,
            "skipped_transfers": 0,
            "transfer_completion_rate_pct": "0.000",
            "attempted_transfer_completion_rate_pct": "",
            "completion_markers_received": 0,
            "complete_outputs": 0,
            "output_completeness_pct": "0.000",
            "fully_verified_output_rate_pct": "0.000",
            "attempted_fully_verified_output_rate_pct": "",
            "expected_output_bytes": sample_count * int(payload["bytes"]),
            "verified_output_bytes": 0,
            "verified_output_ratio_pct": "0.000",
            "attempted_expected_output_bytes": 0,
            "attempted_verified_output_bytes": 0,
            "attempted_verified_output_ratio_pct": "",
            "mean_content_coverage_pct": "0.000",
            "mean_verified_byte_ratio_pct": "0.000",
            "mean_raw_byte_ratio_pct": "0.000",
            "byte_verification_rate_pct": "0.000",
            "hash_verification_rate_pct": "0.000",
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
    payload_dir = Path(cfg.get("PAYLOAD_DIR", "payloads"))
    selected_payloads = [
        {
            **payload,
            "_expected_lines": _load_expected_lines(payload_dir, payload),
        }
        for payload in selected_payloads
    ]
    remote_dir = cfg.get("W2_REMOTE_PAYLOAD_DIR", "/tmp/w2_mux_tt_payloads")
    timeout = float(cfg.get("TRANSFER_TIMEOUT", "120"))
    ready_timeout = float(cfg.get("STREAM_READY_TIMEOUT", "20"))
    warmup = float(cfg.get("WARMUP_SECONDS", "5"))
    live = cfg.get("LIVE_PROGRESS", "1") == "1"
    sample_count = int(cfg.get("SAMPLES_PER_STREAM_PER_TRIAL", "100"))
    live_every = int(cfg.get("LIVE_PROGRESS_EVERY", "10"))
    continue_after_timeout = (
        trial["protocol"] == "mosh"
        and cfg.get("MOSH_CONTINUE_AFTER_TIMEOUT", "1") == "1"
    )
    barrier_timeout = timeout + (
        float(cfg.get("MOSH_BARRIER_GRACE_SECONDS", "5"))
        if continue_after_timeout else 0.0
    )
    if sample_count <= 0 or live_every <= 0:
        raise ValueError("số mẫu và nhịp báo tiến trình phải dương")
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
        sample_barrier = threading.Barrier(
            len(roles), action=connection.prepare_sample
        )
        workload_started = time.perf_counter_ns()
        with ThreadPoolExecutor(max_workers=len(roles)) as pool:
            futures = {
                pool.submit(
                    run_stream,
                    trial, role, index, streams[role], selected_payloads[index],
                    remote_dir, sample_barrier, timeout, sample_count,
                    live, live_every, continue_after_timeout, barrier_timeout,
                ): role
                for index, role in enumerate(roles)
            }
            for future in as_completed(futures):
                transfer_rows, summary = future.result()
                transfers.extend(transfer_rows)
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
            trial, roles, selected_payloads, remote_dir,
            sample_count, status, note,
        )
    finally:
        try:
            connection.close()
        except Exception as exc:
            note = f"{note}; close={exc!r}" if note else f"close={exc!r}"

    transfers.sort(key=lambda row: (
        int(row["stream_index"]), int(row["sample_index"])
    ))
    stream_rows.sort(key=lambda row: int(row["stream_index"]))
    completed_transfers = sum(
        row["status"] == "completed" for row in transfers
    )
    attempted_transfers = sum(
        row["status"] in {"completed", "partial", "timeout", "failure"}
        for row in transfers
    )
    partial_transfers = sum(row["status"] == "partial" for row in transfers)
    timeout_transfers = sum(row["status"] == "timeout" for row in transfers)
    skipped_transfers = sum(row["status"] == "skipped" for row in transfers)
    completed_streams = sum(
        int(row["stream_completed"]) for row in stream_rows
    )
    complete_outputs = sum(int(row["output_complete"]) for row in transfers)
    coverage_rates = [
        float(row["content_coverage_pct"]) for row in transfers
    ]
    verified_byte_rates = [
        float(row["verified_byte_ratio_pct"]) for row in transfers
    ]
    raw_byte_rates = [float(row["raw_byte_ratio_pct"]) for row in transfers]
    expected = len(roles) * sample_count
    verified_output = _verified_output_metrics(transfers)
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
        "attempted_transfers": attempted_transfers,
        "completed_transfers": completed_transfers,
        "partial_transfers": partial_transfers,
        "timeout_transfers": timeout_transfers,
        "skipped_transfers": skipped_transfers,
        "transfer_completion_rate_pct": (
            f"{100.0 * completed_transfers / expected:.3f}"
        ),
        "attempted_transfer_completion_rate_pct": (
            f"{100.0 * completed_transfers / attempted_transfers:.3f}"
            if attempted_transfers else ""
        ),
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": (
            f"{100.0 * completed_streams / len(roles):.3f}"
        ),
        "complete_outputs": complete_outputs,
        "output_completeness_pct": (
            f"{100.0 * complete_outputs / expected:.3f}"
        ),
        **verified_output,
        "mean_content_coverage_pct": (
            f"{sum(coverage_rates) / expected:.3f}"
        ),
        "mean_verified_byte_ratio_pct": (
            f"{sum(verified_byte_rates) / expected:.3f}"
        ),
        "mean_raw_byte_ratio_pct": (
            f"{sum(raw_byte_rates) / expected:.3f}"
        ),
        "byte_verification_rate_pct": (
            f"{100.0 * sum(int(row['bytes_complete']) for row in transfers) / expected:.3f}"
        ),
        "hash_verification_rate_pct": (
            f"{100.0 * sum(int(row['hash_complete']) for row in transfers) / expected:.3f}"
        ),
        "setup_ms": f"{setup_ms:.3f}",
        "workload_elapsed_ms": (
            f"{workload_ms:.3f}" if isinstance(workload_ms, float) else ""
        ),
        "status": status,
        "note": note or audit.note,
    }
    return transfers, stream_rows, trial_row, audit.to_dict()
