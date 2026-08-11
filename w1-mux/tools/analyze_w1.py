#!/usr/bin/env python3
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
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# Định dạng số thực ổn định cho CSV.
def fmt(value):
    return "" if value == "" else f"{value:.3f}"


# Đọc CSV và kiểm tra các cột bắt buộc.
def load_csv(path, required):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"no rows in {path}")
    return rows


# Tính Mean, Median, P95 và P99.
def latency_stats(values):
    return {
        "mean_ms": fmt(statistics.mean(values) if values else ""),
        "median_ms": fmt(statistics.median(values) if values else ""),
        "p95_ms": fmt(percentile(values, 0.95)),
        "p99_ms": fmt(percentile(values, 0.99)),
    }


# Tổng hợp metric lệnh theo kịch bản hoặc từng stream.
def summarize_commands(rows, per_stream=False):
    groups = defaultdict(list)
    for row in rows:
        key = [row["protocol"], row["scenario"]]
        if per_stream:
            key.append(row["stream_role"])
        key.append(row["command"])
        groups[tuple(key)].append(row)
    output = []
    for key, group in sorted(groups.items()):
        completed = [row for row in group if row["status"] == "completed"]
        values = [float(row["latency_ms"]) for row in completed if row["latency_ms"]]
        complete_outputs = sum(row["output_complete"] == "1" for row in group)
        base = {
            "protocol": key[0], "scenario": key[1],
            "scope": "stream" if per_stream else "scenario",
            "stream_role": key[2] if per_stream else "all",
            "command": key[3] if per_stream else key[2],
            "samples": len(group), "completed": len(completed),
            "command_completion_rate_pct": fmt(100.0 * len(completed) / len(group)),
            "complete_outputs": complete_outputs,
            "output_completeness_pct": fmt(100.0 * complete_outputs / len(group)),
            "timeout_count": sum(row["status"] == "timeout" for row in group),
            **latency_stats(values),
        }
        output.append(base)
    return output


# Tổng hợp latency và completion của từng kịch bản.
def summarize_scenarios(samples, streams, trials):
    sample_groups, stream_groups, trial_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in samples:
        sample_groups[(row["protocol"], row["scenario"])].append(row)
    for row in streams:
        stream_groups[(row["protocol"], row["scenario"])].append(row)
    for row in trials:
        trial_groups[(row["protocol"], row["scenario"])].append(row)
    output = []
    for key in sorted(sample_groups):
        sample_group = sample_groups[key]
        stream_group = stream_groups[key]
        trial_group = trial_groups[key]
        completed_samples = [row for row in sample_group if row["status"] == "completed"]
        latencies = [float(row["latency_ms"]) for row in completed_samples if row["latency_ms"]]
        output.append({
            "protocol": key[0], "scenario": key[1],
            "trials": len(trial_group),
            "connection_valid_rate_pct": fmt(
                100.0 * sum(row["connection_valid"] == "1" for row in trial_group) / len(trial_group)
            ),
            "expected_commands": len(sample_group),
            "completed_commands": len(completed_samples),
            "command_completion_rate_pct": fmt(100.0 * len(completed_samples) / len(sample_group)),
            "expected_streams": len(stream_group),
            "completed_streams": sum(row["stream_completed"] == "1" for row in stream_group),
            "stream_completion_rate_pct": fmt(
                100.0 * sum(row["stream_completed"] == "1" for row in stream_group) / len(stream_group)
            ),
            "output_completeness_pct": fmt(
                100.0 * sum(row["output_complete"] == "1" for row in sample_group) / len(sample_group)
            ),
            **latency_stats(latencies),
        })
    return output


# Ghi một bảng tổng hợp ra CSV.
def write_csv(path, rows):
    if not rows:
        raise ValueError(f"no summary rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Tạo toàn bộ bảng thống kê W1.
def main():
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    samples = load_csv(result_dir / "samples.csv", {
        "protocol", "scenario", "stream_role", "command", "status",
        "latency_ms", "output_complete",
    })
    streams = load_csv(result_dir / "streams.csv", {
        "protocol", "scenario", "stream_completed",
    })
    trials = load_csv(result_dir / "trials.csv", {
        "protocol", "scenario", "connection_valid",
    })
    command_rows = summarize_commands(samples, per_stream=False)
    command_rows += summarize_commands(samples, per_stream=True)
    write_csv(result_dir / "command_summary.csv", command_rows)
    write_csv(result_dir / "scenario_summary.csv", summarize_scenarios(samples, streams, trials))
    print(f"Saved W1 summaries to {result_dir}")


if __name__ == "__main__":
    main()
