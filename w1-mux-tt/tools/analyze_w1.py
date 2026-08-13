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


# Kiểm tra đủ mẫu và không trùng chỉ số trong từng stream.
def validate_sample_counts(samples, trials):
    expected_by_trial = {
        row["trial_id"]: int(row["expected_commands"]) // int(row["stream_count"])
        for row in trials
    }
    grouped = defaultdict(list)
    for row in samples:
        grouped[(row["trial_id"], row["stream_role"])].append(row)

    expected_keys = {
        (row["trial_id"], f"command_{index}")
        for row in trials
        for index in range(int(row["stream_count"]))
    }
    errors = []
    for key in sorted(expected_keys):
        group = grouped.get(key, [])
        trial_id, role = key
        expected = expected_by_trial[trial_id]
        indexes = [int(row["sample_index"]) for row in group]
        if len(group) != expected:
            errors.append(
                f"{trial_id}/{role}: expected {expected} samples, got {len(group)}"
            )
        if len(set(indexes)) != len(indexes):
            errors.append(f"{trial_id}/{role}: duplicate sample_index")
        if sorted(indexes) != list(range(1, expected + 1)):
            errors.append(f"{trial_id}/{role}: sample_index is not 1..{expected}")
    unexpected = sorted(set(grouped) - expected_keys)
    if unexpected:
        errors.append(f"unexpected trial/stream groups: {unexpected}")
    if errors:
        raise ValueError("invalid W1 sample counts:\n- " + "\n- ".join(errors))


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
        verifiable = [row for row in group if row["output_verifiable"] == "1"]
        complete_outputs = sum(row["output_complete"] == "1" for row in verifiable)
        base = {
            "protocol": key[0], "scenario": key[1],
            "scope": "stream" if per_stream else "scenario",
            "stream_role": key[2] if per_stream else "all",
            "command": key[3] if per_stream else key[2],
            "samples": len(group), "completed": len(completed),
            "command_completion_rate_pct": fmt(100.0 * len(completed) / len(group)),
            "complete_outputs": complete_outputs,
            "output_completeness_pct": fmt(
                100.0 * complete_outputs / len(verifiable) if verifiable else ""
            ),
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
        setup_values = [
            float(row["setup_ms"])
            for row in trial_group
            if row["setup_ms"]
            and row["connection_valid"] == "1"
            and row["ready_streams"] == row["stream_count"]
        ]
        setup = latency_stats(setup_values)
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
                100.0
                * sum(
                    row["output_complete"] == "1"
                    for row in sample_group
                    if row["output_verifiable"] == "1"
                )
                / sum(row["output_verifiable"] == "1" for row in sample_group)
                if any(row["output_verifiable"] == "1" for row in sample_group)
                else ""
            ),
            "setup_n": len(setup_values),
            "setup_mean_ms": setup["mean_ms"],
            "setup_median_ms": setup["median_ms"],
            "setup_p95_ms": setup["p95_ms"],
            "setup_p99_ms": setup["p99_ms"],
            **latency_stats(latencies),
        })
    return output


# Tổng hợp riêng từng stream role, không gộp các stream trong cùng kịch bản.
def summarize_streams(samples, streams, trials):
    sample_groups, stream_groups = defaultdict(list), defaultdict(list)
    trial_groups = defaultdict(list)
    for row in samples:
        sample_groups[(row["protocol"], row["scenario"], row["stream_role"])].append(row)
    for row in streams:
        stream_groups[(row["protocol"], row["scenario"], row["stream_role"])].append(row)
    for row in trials:
        trial_groups[(row["protocol"], row["scenario"])].append(row)

    output = []
    for key in sorted(sample_groups):
        sample_group = sample_groups[key]
        stream_group = stream_groups[key]
        trial_group = trial_groups[(key[0], key[1])]
        completed = [row for row in sample_group if row["status"] == "completed"]
        latencies = [float(row["latency_ms"]) for row in completed if row["latency_ms"]]
        elapsed = [
            float(row["elapsed_ms"])
            for row in stream_group
            if row.get("elapsed_ms") and row["stream_completed"] == "1"
        ]
        elapsed_stats = latency_stats(elapsed)
        output.append({
            "protocol": key[0],
            "scenario": key[1],
            "stream_role": key[2],
            "trials": len(stream_group),
            "expected_samples": sum(
                int(row["expected_commands"]) // int(row["stream_count"])
                for row in trial_group
            ),
            "samples": len(sample_group),
            "completed_commands": len(completed),
            "command_completion_rate_pct": fmt(100.0 * len(completed) / len(sample_group)),
            "completed_streams": sum(row["stream_completed"] == "1" for row in stream_group),
            "stream_completion_rate_pct": fmt(
                100.0 * sum(row["stream_completed"] == "1" for row in stream_group)
                / len(stream_group)
            ),
            "output_completeness_pct": fmt(
                100.0
                * sum(
                    row["output_complete"] == "1"
                    for row in sample_group
                    if row["output_verifiable"] == "1"
                )
                / sum(row["output_verifiable"] == "1" for row in sample_group)
                if any(row["output_verifiable"] == "1" for row in sample_group)
                else ""
            ),
            "stream_elapsed_mean_ms": elapsed_stats["mean_ms"],
            "stream_elapsed_median_ms": elapsed_stats["median_ms"],
            "stream_elapsed_p95_ms": elapsed_stats["p95_ms"],
            "stream_elapsed_p99_ms": elapsed_stats["p99_ms"],
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
        "trial_id", "protocol", "scenario", "stream_role", "sample_index",
        "cycle_index", "command_index", "command", "status", "latency_ms",
        "output_complete", "output_verifiable",
    })
    streams = load_csv(result_dir / "streams.csv", {
        "protocol", "scenario", "stream_completed",
    })
    trials = load_csv(result_dir / "trials.csv", {
        "protocol", "scenario", "connection_valid", "setup_ms",
        "ready_streams", "stream_count", "expected_commands",
    })
    validate_sample_counts(samples, trials)
    command_rows = summarize_commands(samples, per_stream=False)
    command_rows += summarize_commands(samples, per_stream=True)
    write_csv(result_dir / "command_summary.csv", command_rows)
    write_csv(result_dir / "scenario_summary.csv", summarize_scenarios(samples, streams, trials))
    write_csv(
        result_dir / "stream_summary.csv",
        summarize_streams(samples, streams, trials),
    )
    print(f"Saved W1 summaries to {result_dir}")


if __name__ == "__main__":
    main()
