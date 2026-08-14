#!/usr/bin/env python3
"""Tổng hợp độ trễ và tỷ lệ hoàn thành W2."""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


# Tính percentile bằng nội suy tuyến tính.
def percentile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return ""
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


# Định dạng số thực ổn định cho CSV.
def fmt(value):
    return "" if value == "" else f"{value:.3f}"


# Đọc CSV và kiểm tra các cột bắt buộc.
def load_csv(path: Path, required: set[str]):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} thiếu cột: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"không có dữ liệu trong {path}")
    return rows


# Tính Mean, Median, P95 và P99.
def latency_stats(values):
    return {
        "mean_ms": fmt(statistics.mean(values) if values else ""),
        "median_ms": fmt(statistics.median(values) if values else ""),
        "p95_ms": fmt(percentile(values, 0.95)),
        "p99_ms": fmt(percentile(values, 0.99)),
    }


# Kiểm tra mỗi trial có đúng một dòng cho từng output stream.
def validate_transfers(transfers, trials):
    grouped = defaultdict(list)
    for row in transfers:
        grouped[(row["trial_id"], row["stream_role"])].append(row)
    expected = {
        (trial["trial_id"], f"output_{index}")
        for trial in trials
        for index in range(int(trial["stream_count"]))
    }
    errors = []
    for key in sorted(expected):
        count = len(grouped.get(key, []))
        if count != 1:
            errors.append(f"{key[0]}/{key[1]}: cần 1 dòng, nhận {count}")
    unexpected = sorted(set(grouped) - expected)
    if unexpected:
        errors.append(f"có stream ngoài kế hoạch: {unexpected}")
    if errors:
        raise ValueError("sai số lượng phép truyền:\n- " + "\n- ".join(errors))


# Tổng hợp một nhóm phép truyền.
def summarize_group(rows):
    completed = [row for row in rows if row["status"] == "completed"]
    completion_values = [
        float(row["completion_latency_ms"])
        for row in completed if row["completion_latency_ms"]
    ]
    first_values = [
        float(row["first_byte_latency_ms"])
        for row in completed if row["first_byte_latency_ms"]
    ]
    throughput_values = [
        float(row["throughput_mib_s"])
        for row in completed if row["throughput_mib_s"]
    ]
    completion_stats = latency_stats(completion_values)
    first_stats = latency_stats(first_values)
    return {
        "expected_transfers": len(rows),
        "completed_transfers": len(completed),
        "partial_transfers": sum(row["status"] == "partial" for row in rows),
        "timeout_transfers": sum(row["status"] == "timeout" for row in rows),
        "transfer_completion_rate_pct": fmt(
            100.0 * len(completed) / len(rows)
        ),
        "output_completeness_pct": fmt(
            100.0 * sum(row["output_complete"] == "1" for row in rows)
            / len(rows)
        ),
        "completion_mean_ms": completion_stats["mean_ms"],
        "completion_median_ms": completion_stats["median_ms"],
        "completion_p95_ms": completion_stats["p95_ms"],
        "completion_p99_ms": completion_stats["p99_ms"],
        "first_byte_mean_ms": first_stats["mean_ms"],
        "first_byte_median_ms": first_stats["median_ms"],
        "first_byte_p95_ms": first_stats["p95_ms"],
        "first_byte_p99_ms": first_stats["p99_ms"],
        "throughput_mean_mib_s": fmt(
            statistics.mean(throughput_values) if throughput_values else ""
        ),
        "throughput_median_mib_s": fmt(
            statistics.median(throughput_values) if throughput_values else ""
        ),
    }


# Tổng hợp theo giao thức và kịch bản.
def summarize_scenarios(transfers, streams, trials):
    transfer_groups = defaultdict(list)
    stream_groups = defaultdict(list)
    trial_groups = defaultdict(list)
    for row in transfers:
        transfer_groups[(row["protocol"], row["scenario"])].append(row)
    for row in streams:
        stream_groups[(row["protocol"], row["scenario"])].append(row)
    for row in trials:
        trial_groups[(row["protocol"], row["scenario"])].append(row)

    output = []
    for key in sorted(transfer_groups):
        group = transfer_groups[key]
        stream_group = stream_groups[key]
        trial_group = trial_groups[key]
        setup_values = [
            float(row["setup_ms"])
            for row in trial_group
            if row["setup_ms"]
            and row["connection_valid"] == "1"
            and row["ready_streams"] == row["stream_count"]
        ]
        setup = latency_stats(setup_values)
        output.append({
            "protocol": key[0],
            "scenario": key[1],
            "trials": len(trial_group),
            "connection_valid_rate_pct": fmt(
                100.0
                * sum(row["connection_valid"] == "1" for row in trial_group)
                / len(trial_group)
            ),
            **summarize_group(group),
            "expected_streams": len(stream_group),
            "completed_streams": sum(
                row["stream_completed"] == "1" for row in stream_group
            ),
            "stream_completion_rate_pct": fmt(
                100.0
                * sum(row["stream_completed"] == "1" for row in stream_group)
                / len(stream_group)
            ),
            "setup_n": len(setup_values),
            "setup_mean_ms": setup["mean_ms"],
            "setup_median_ms": setup["median_ms"],
            "setup_p95_ms": setup["p95_ms"],
            "setup_p99_ms": setup["p99_ms"],
        })
    return output


# Tổng hợp riêng từng vai trò output.
def summarize_streams(transfers, streams):
    transfer_groups = defaultdict(list)
    stream_groups = defaultdict(list)
    for row in transfers:
        key = (row["protocol"], row["scenario"], row["stream_role"])
        transfer_groups[key].append(row)
    for row in streams:
        key = (row["protocol"], row["scenario"], row["stream_role"])
        stream_groups[key].append(row)

    output = []
    for key in sorted(transfer_groups):
        group = transfer_groups[key]
        stream_group = stream_groups[key]
        output.append({
            "protocol": key[0],
            "scenario": key[1],
            "stream_role": key[2],
            "trials": len(group),
            **summarize_group(group),
            "completed_streams": sum(
                row["stream_completed"] == "1" for row in stream_group
            ),
            "stream_completion_rate_pct": fmt(
                100.0
                * sum(row["stream_completed"] == "1" for row in stream_group)
                / len(stream_group)
            ),
        })
    return output


# Ghi bảng tổng hợp ra CSV.
def write_csv(path: Path, rows) -> None:
    if not rows:
        raise ValueError(f"không có dòng tổng hợp cho {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Tạo các bảng tổng hợp W2.
def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    transfers = load_csv(result_dir / "transfers.csv", {
        "trial_id", "protocol", "scenario", "stream_role", "status",
        "completion_latency_ms", "first_byte_latency_ms", "throughput_mib_s",
        "output_complete",
    })
    streams = load_csv(result_dir / "streams.csv", {
        "protocol", "scenario", "stream_role", "stream_completed",
    })
    trials = load_csv(result_dir / "trials.csv", {
        "trial_id", "protocol", "scenario", "stream_count",
        "connection_valid", "ready_streams", "setup_ms",
    })
    validate_transfers(transfers, trials)
    write_csv(
        result_dir / "scenario_summary.csv",
        summarize_scenarios(transfers, streams, trials),
    )
    write_csv(
        result_dir / "stream_summary.csv",
        summarize_streams(transfers, streams),
    )
    print(f"Saved W2 summaries to {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
