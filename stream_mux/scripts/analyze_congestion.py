#!/usr/bin/env python3
"""Tổng hợp TCP_INFO và quic-go congestion cho các workload W1-W4."""

from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path


FIELDS = (
    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
    "protocol", "scenario", "stream_count", "endpoint", "collector", "log_file",
    "status", "window_scope", "workload_start_ns", "workload_end_ns",
    "event_count", "metric_samples", "collector_errors",
    "cc_algorithm", "rtt_mean_ms", "rtt_median_ms", "rtt_p95_ms",
    "rtt_min_ms", "rtt_max_ms", "cwnd_mean_bytes", "cwnd_min_bytes",
    "cwnd_max_bytes", "bytes_in_flight_max", "packet_loss_events",
    "recovery_transitions", "pto_max", "tcp_retrans_total_delta",
    "tcp_lost_packets_delta", "send_rate_mean_bps",
    "pacing_rate_mean_bps", "delivery_rate_mean_bps", "note",
)


# Đọc CSV thành danh sách dictionary.
def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Ghi CSV với schema cố định.
def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


# Tính percentile tuyến tính giống bộ phân tích latency.
def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


# Định dạng số có thể thiếu.
def fmt(value: float | int | None) -> str:
    return "" if value is None else f"{value:.3f}"


# Đọc an toàn từng event JSONL, đồng thời giữ lỗi cú pháp trong note.
def read_events(path: Path) -> tuple[list[dict], list[str]]:
    events, errors = [], []
    with path.open(encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            try:
                events.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                errors.append(f"line {line_number}: {exc}")
    return events, errors


# Tổng hợp một file congestion JSONL.
def summarize_trial(
    order: dict, path: Path, endpoint: str, collector: str,
    workload_window_ns: tuple[int | None, int | None],
) -> dict:
    protocol = order["protocol"]
    base = {key: order[key] for key in FIELDS if key in order}
    if not path.exists() or path.stat().st_size == 0:
        return {
            **base, "endpoint": endpoint, "collector": collector,
            "log_file": str(path),
            "status": "missing", "window_scope": "missing",
            "workload_start_ns": "",
            "workload_end_ns": "", "event_count": 0, "metric_samples": 0,
            "collector_errors": 1, "cc_algorithm": "", "rtt_mean_ms": "",
            "rtt_median_ms": "", "rtt_p95_ms": "", "rtt_min_ms": "",
            "rtt_max_ms": "", "cwnd_mean_bytes": "", "cwnd_min_bytes": "",
            "cwnd_max_bytes": "", "bytes_in_flight_max": "",
            "packet_loss_events": 0, "recovery_transitions": 0,
            "pto_max": "", "tcp_retrans_total_delta": "",
            "tcp_lost_packets_delta": "", "send_rate_mean_bps": "",
            "pacing_rate_mean_bps": "", "delivery_rate_mean_bps": "",
            "note": "congestion log missing",
        }
    events, parse_errors = read_events(path)
    workload_start, workload_end = workload_window_ns
    if workload_start is not None and workload_end is not None:
        workload_events = [
            event for event in events
            if workload_start <= int(event.get("time_ns", 0)) <= workload_end
        ]
        window_scope = "measured_workload"
    else:
        workload_events = events
        event_times = [
            int(event["time_ns"]) for event in events if event.get("time_ns")
        ]
        workload_start = min(event_times) if event_times else None
        workload_end = max(event_times) if event_times else None
        window_scope = "connection_lifetime"
    metrics = [
        event for event in workload_events
        if event.get("event") in {"metrics", "metrics_final"}
    ]
    rtts = []
    cwnds = []
    flights = []
    retrans = []
    lost_packets = []
    send_rates = []
    pacing_rates = []
    delivery_rates = []
    algorithms = []
    for event in events:
        if event.get("cc_algorithm"):
            algorithms.append(str(event["cc_algorithm"]))
    for event in metrics:
        if protocol == "ssh3" and event.get("smoothed_rtt_us") is not None:
            rtts.append(float(event["smoothed_rtt_us"]) / 1000.0)
        elif event.get("rtt_ms") is not None:
            rtts.append(float(event["rtt_ms"]))
        if event.get("cwnd_bytes") is not None:
            cwnds.append(float(event["cwnd_bytes"]))
        if event.get("bytes_in_flight") is not None:
            flights.append(float(event["bytes_in_flight"]))
        if event.get("retrans_total") is not None:
            retrans.append(int(event["retrans_total"]))
        if event.get("lost_packets") is not None:
            lost_packets.append(int(event["lost_packets"]))
        if event.get("send_rate_bps") is not None:
            send_rates.append(float(event["send_rate_bps"]))
        if event.get("pacing_rate_bps") is not None:
            pacing_rates.append(float(event["pacing_rate_bps"]))
        if event.get("delivery_rate_bps") is not None:
            delivery_rates.append(float(event["delivery_rate_bps"]))
        if event.get("cc_algorithm"):
            algorithms.append(str(event["cc_algorithm"]))
    # Chỉ lỗi nằm trong workload mới làm hỏng phép đo. Lúc vừa bật sampler,
    # socket có thể chưa xuất hiện trong một nhịp mà không ảnh hưởng workload.
    collector_errors = sum(
        event.get("event") == "collector_error" for event in workload_events
    ) + len(parse_errors)
    if window_scope == "measured_workload":
        collector_errors += sum(
            event.get("event") == "socket_not_found"
            for event in workload_events
        )
    status = "ok" if metrics and not collector_errors else (
        "partial" if metrics else "invalid"
    )
    pto_values = [
        int(event["value"]) for event in workload_events
        if event.get("event") == "pto_count" and event.get("value") is not None
    ]
    notes = list(parse_errors)
    if not metrics:
        notes.append(f"no congestion metrics inside {window_scope}")
    if collector_errors:
        notes.append(f"collector_errors={collector_errors}")
    row = {
        **base,
        "endpoint": endpoint,
        "collector": collector,
        "log_file": str(path),
        "status": status,
        "window_scope": window_scope,
        "workload_start_ns": workload_start or "",
        "workload_end_ns": workload_end or "",
        "event_count": len(events),
        "metric_samples": len(metrics),
        "collector_errors": collector_errors,
        "cc_algorithm": ",".join(sorted(set(algorithms))),
        "rtt_mean_ms": fmt(statistics.mean(rtts) if rtts else None),
        "rtt_median_ms": fmt(statistics.median(rtts) if rtts else None),
        "rtt_p95_ms": fmt(percentile(rtts, 0.95)),
        "rtt_min_ms": fmt(min(rtts) if rtts else None),
        "rtt_max_ms": fmt(max(rtts) if rtts else None),
        "cwnd_mean_bytes": fmt(statistics.mean(cwnds) if cwnds else None),
        "cwnd_min_bytes": fmt(min(cwnds) if cwnds else None),
        "cwnd_max_bytes": fmt(max(cwnds) if cwnds else None),
        "bytes_in_flight_max": fmt(max(flights) if flights else None),
        "packet_loss_events": sum(
            event.get("event") == "packet_lost" for event in workload_events
        ),
        "recovery_transitions": sum(
            event.get("event") == "congestion_state"
            and event.get("state") == "recovery" for event in workload_events
        ),
        "pto_max": max(pto_values) if pto_values else "",
        "tcp_retrans_total_delta": (
            max(retrans) - min(retrans) if retrans else ""
        ),
        "tcp_lost_packets_delta": (
            max(lost_packets) - min(lost_packets) if lost_packets else ""
        ),
        "send_rate_mean_bps": fmt(
            statistics.mean(send_rates) if send_rates else None
        ),
        "pacing_rate_mean_bps": fmt(
            statistics.mean(pacing_rates) if pacing_rates else None
        ),
        "delivery_rate_mean_bps": fmt(
            statistics.mean(delivery_rates) if delivery_rates else None
        ),
        "note": "; ".join(notes),
    }
    return row


# Đọc các mốc đo của W1-W4 và tạo cửa sổ phía client theo trial.
def client_workload_windows(result_dir: Path) -> dict[str, tuple[int, int]]:
    specifications = (
        ("samples.csv", "send_time_ns", ("completion_time_ns",)),
        ("transfers.csv", "send_time_ns", ("marker_time_ns", "last_byte_time_ns")),
        ("keystrokes.csv", "send_ns", ("render_ns",)),
        ("background.csv", "send_time_ns", ("completion_time_ns",)),
    )
    values: dict[str, dict[str, list[int]]] = {}
    for filename, start_field, end_fields in specifications:
        path = result_dir / filename
        if not path.exists():
            continue
        for row in read_csv(path):
            trial_id = row.get("trial_id", "")
            if not trial_id:
                continue
            bucket = values.setdefault(trial_id, {"starts": [], "ends": []})
            if row.get(start_field):
                bucket["starts"].append(int(row[start_field]))
            for field in end_fields:
                if row.get(field):
                    bucket["ends"].append(int(row[field]))
    return {
        trial_id: (min(bucket["starts"]), max(bucket["ends"]))
        for trial_id, bucket in values.items()
        if bucket["starts"] and bucket["ends"]
    }


# Lấy cửa sổ workload theo chính đồng hồ server, tránh phụ thuộc đồng bộ NTP.
def server_workload_window(trial: dict) -> tuple[int | None, int | None]:
    start = trial.get("server_workload_start_ns", "")
    end = trial.get("server_workload_end_ns", "")
    return (int(start) if start else None, int(end) if end else None)


# Ghép file tracer ssh3-server với trial dựa trên cửa sổ connection bao workload.
def match_server_quic_log(
    paths: list[Path], client_path: Path,
    server_window: tuple[int | None, int | None], used: set[Path],
) -> Path:
    client_ids = set()
    if client_path.exists():
        client_events, _ = read_events(client_path)
        client_ids = {
            str(event["connection_id"]) for event in client_events
            if event.get("connection_id")
        }
    if client_ids:
        for path in paths:
            if path in used:
                continue
            events, _ = read_events(path)
            server_ids = {
                str(event["connection_id"]) for event in events
                if event.get("connection_id")
            }
            if client_ids & server_ids:
                used.add(path)
                return path
    workload_start, workload_end = server_window
    if workload_start is None or workload_end is None:
        return Path("missing-ssh3-server-workload-window.jsonl")
    candidates = []
    for path in paths:
        if path in used:
            continue
        events, _ = read_events(path)
        times = [int(event.get("time_ns", 0)) for event in events if event.get("time_ns")]
        if not times:
            continue
        first, last = min(times), max(times)
        overlap = max(0, min(last, workload_end) - max(first, workload_start))
        if overlap > 0:
            candidates.append((overlap, -(last - first), path))
    if not candidates:
        return Path("missing-ssh3-server-congestion.jsonl")
    path = max(candidates, key=lambda item: (item[0], item[1]))[2]
    used.add(path)
    return path


# Tổng hợp mọi trial SSH/SSH3 và tùy chọn bắt buộc log hợp lệ.
def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    congestion_dir = result_dir / "congestion"
    client_dir = congestion_dir / "client"
    orders = read_csv(result_dir / "experiment_order.csv")
    trials = read_csv(result_dir / "trials.csv")
    trials_by_id = {row["trial_id"]: row for row in trials}
    client_windows = client_workload_windows(result_dir)
    expected = [row for row in orders if row["protocol"] in {"ssh", "ssh3"}]
    server_dir = congestion_dir / "server"
    server_quic_logs = sorted(server_dir.glob("*.ssh3_server_quic.jsonl"))
    used_server_quic: set[Path] = set()
    rows = []
    for order in expected:
        trial = trials_by_id.get(order["trial_id"], {})
        client_window = client_windows.get(order["trial_id"], (None, None))
        server_window = server_workload_window(trial)
        if order["protocol"] == "ssh":
            client_path = client_dir / f"{order['trial_tag']}.ssh_tcp.jsonl"
            server_path = server_dir / (
                f"{order['run_id']}.{order['trial_tag']}.ssh_server_tcp.jsonl"
            )
            collector = "linux_ss_tcp_info"
        else:
            client_path = client_dir / f"{order['trial_tag']}.ssh3_quic.jsonl"
            server_path = match_server_quic_log(
                server_quic_logs, client_path, server_window, used_server_quic
            )
            collector = "quic_go_tracer"
        rows.append(summarize_trial(
            order, client_path, "client", collector, client_window
        ))
        rows.append(summarize_trial(
            order, server_path, "server", collector, server_window
        ))
    congestion_dir.mkdir(parents=True, exist_ok=True)
    write_csv(congestion_dir / "summary.csv", rows)
    invalid = [row for row in rows if row["status"] != "ok"]
    print(
        f"Saved {len(rows)} client/server congestion rows for "
        f"{len(expected)} SSH/SSH3 trials to "
        f"{congestion_dir / 'summary.csv'}"
    )
    if invalid:
        print("Congestion audit incomplete:")
        for row in invalid:
            print(
                f"- {row['trial_id']} endpoint={row['endpoint']}: "
                f"status={row['status']} metrics={row['metric_samples']} "
                f"scope={row['window_scope']} note={row['note']}"
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
