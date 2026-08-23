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


# Kiểm tra đủ mẫu và không trùng chỉ số trong từng output stream.
def validate_transfers(transfers, trials):
    grouped = defaultdict(list)
    for row in transfers:
        grouped[(row["trial_id"], row["stream_role"])].append(row)
    expected_groups = {
        (trial["trial_id"], f"output_{index}")
        for trial in trials
        for index in range(int(trial["stream_count"]))
    }
    samples_by_trial = {
        trial["trial_id"]: (
            int(trial["expected_transfers"]) // int(trial["stream_count"])
        )
        for trial in trials
    }
    errors = []
    for key in sorted(expected_groups):
        group = grouped.get(key, [])
        expected_samples = samples_by_trial[key[0]]
        indexes = [int(row["sample_index"]) for row in group]
        if len(group) != expected_samples:
            errors.append(
                f"{key[0]}/{key[1]}: cần {expected_samples} mẫu, "
                f"nhận {len(group)}"
            )
        if len(indexes) != len(set(indexes)):
            errors.append(f"{key[0]}/{key[1]}: trùng sample_index")
        if sorted(indexes) != list(range(1, expected_samples + 1)):
            errors.append(
                f"{key[0]}/{key[1]}: sample_index không phải "
                f"1..{expected_samples}"
            )
    unexpected = sorted(set(grouped) - expected_groups)
    if unexpected:
        errors.append(f"có stream ngoài kế hoạch: {unexpected}")
    if errors:
        raise ValueError("sai số lượng phép truyền:\n- " + "\n- ".join(errors))


# Tổng hợp một nhóm phép truyền.
def summarize_group(rows):
    completed = [row for row in rows if row["status"] == "completed"]
    attempted = [
        row for row in rows
        if row["status"] not in {"skipped", "trial_unavailable", "barrier_failure"}
    ]
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
    visible_values = [
        float(row["marker_latency_ms"])
        for row in attempted
        if row.get("completion_marker_received") == "1"
        and row.get("marker_latency_ms")
    ]
    coverage_values = [
        float(row["content_coverage_pct"])
        for row in rows if row["content_coverage_pct"]
    ]
    raw_byte_values = [
        float(row["raw_byte_ratio_pct"])
        for row in rows if row["raw_byte_ratio_pct"]
    ]
    verified_byte_values = [
        float(row.get("verified_byte_ratio_pct") or row["content_coverage_pct"])
        for row in rows
        if row.get("verified_byte_ratio_pct") or row["content_coverage_pct"]
    ]
    attempted_coverage_values = [
        float(row["content_coverage_pct"])
        for row in attempted if row["content_coverage_pct"]
    ]
    attempted_verified_values = [
        float(row.get("verified_byte_ratio_pct") or row["content_coverage_pct"])
        for row in attempted
        if row.get("verified_byte_ratio_pct") or row["content_coverage_pct"]
    ]
    expected_output_bytes = sum(int(row["expected_bytes"]) for row in rows)
    verified_output_bytes = sum(int(row["verified_bytes"]) for row in rows)
    attempted_expected_output_bytes = sum(
        int(row["expected_bytes"]) for row in attempted
    )
    attempted_verified_output_bytes = sum(
        int(row["verified_bytes"]) for row in attempted
    )
    fully_verified_outputs = sum(
        row["bytes_complete"] == "1"
        and row["lines_complete"] == "1"
        and row["hash_complete"] == "1"
        for row in rows
    )
    attempted_fully_verified_outputs = sum(
        row["bytes_complete"] == "1"
        and row["lines_complete"] == "1"
        and row["hash_complete"] == "1"
        for row in attempted
    )
    completion_stats = latency_stats(completion_values)
    first_stats = latency_stats(first_values)
    visible_stats = latency_stats(visible_values)
    return {
        "expected_transfers": len(rows),
        "attempted_transfers": len(attempted),
        "completed_transfers": len(completed),
        "partial_transfers": sum(row["status"] == "partial" for row in rows),
        "timeout_transfers": sum(row["status"] == "timeout" for row in rows),
        "skipped_transfers": sum(row["status"] == "skipped" for row in rows),
        "completion_marker_rate_pct": fmt(
            100.0 * sum(row["completion_marker_received"] == "1" for row in rows)
            / len(rows)
        ),
        "command_visible_n": len(visible_values),
        "command_visible_mean_ms": visible_stats["mean_ms"],
        "command_visible_median_ms": visible_stats["median_ms"],
        "command_visible_p95_ms": visible_stats["p95_ms"],
        "command_visible_p99_ms": visible_stats["p99_ms"],
        "transfer_completion_rate_pct": fmt(
            100.0 * len(completed) / len(rows)
        ),
        "attempted_transfer_completion_rate_pct": fmt(
            100.0 * len(completed) / len(attempted) if attempted else ""
        ),
        "output_completeness_pct": fmt(
            100.0 * sum(row["output_complete"] == "1" for row in rows)
            / len(rows)
        ),
        "fully_verified_outputs": fully_verified_outputs,
        "fully_verified_output_rate_pct": fmt(
            100.0 * fully_verified_outputs / len(rows)
        ),
        "attempted_fully_verified_outputs": attempted_fully_verified_outputs,
        "attempted_fully_verified_output_rate_pct": fmt(
            100.0 * attempted_fully_verified_outputs / len(attempted)
            if attempted else ""
        ),
        "expected_output_bytes": expected_output_bytes,
        "verified_output_bytes": verified_output_bytes,
        "verified_output_ratio_pct": fmt(
            100.0 * verified_output_bytes / expected_output_bytes
            if expected_output_bytes else ""
        ),
        "attempted_expected_output_bytes": attempted_expected_output_bytes,
        "attempted_verified_output_bytes": attempted_verified_output_bytes,
        "attempted_verified_output_ratio_pct": fmt(
            100.0 * attempted_verified_output_bytes
            / attempted_expected_output_bytes
            if attempted_expected_output_bytes else ""
        ),
        "mean_content_coverage_pct": fmt(
            statistics.mean(coverage_values) if coverage_values else ""
        ),
        "attempted_mean_content_coverage_pct": fmt(
            statistics.mean(attempted_coverage_values)
            if attempted_coverage_values else ""
        ),
        "mean_verified_byte_ratio_pct": fmt(
            statistics.mean(verified_byte_values) if verified_byte_values else ""
        ),
        "attempted_mean_verified_byte_ratio_pct": fmt(
            statistics.mean(attempted_verified_values)
            if attempted_verified_values else ""
        ),
        "mean_raw_byte_ratio_pct": fmt(
            statistics.mean(raw_byte_values) if raw_byte_values else ""
        ),
        "byte_verification_rate_pct": fmt(
            100.0 * sum(row["bytes_complete"] == "1" for row in rows)
            / len(rows)
        ),
        "hash_verification_rate_pct": fmt(
            100.0 * sum(row["hash_complete"] == "1" for row in rows)
            / len(rows)
        ),
        "raw_capture_exact_rate_pct": (
            "" if rows[0]["protocol"] == "mosh" else fmt(
                100.0 * sum(
                    row.get("raw_capture_exact", row["output_complete"]) == "1"
                    for row in rows
                ) / len(rows)
            )
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
            "trials": len(stream_group),
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


# So sánh trực tiếp SSH3/SSH để phát hiện chênh lệch thay vì suy diễn từ hình.
def compare_ssh3_to_ssh(scenario_rows):
    lookup = {
        (row["protocol"], row["scenario"]): row for row in scenario_rows
    }
    output = []
    for scenario in ("W2-S1", "W2-S2", "W2-S4"):
        ssh = lookup.get(("ssh", scenario))
        ssh3 = lookup.get(("ssh3", scenario))
        if not ssh or not ssh3:
            continue
        ssh_latency = (
            float(ssh["completion_median_ms"])
            if ssh["completion_median_ms"] else None
        )
        ssh3_latency = (
            float(ssh3["completion_median_ms"])
            if ssh3["completion_median_ms"] else None
        )
        ssh_throughput = (
            float(ssh["throughput_mean_mib_s"])
            if ssh["throughput_mean_mib_s"] else None
        )
        ssh3_throughput = (
            float(ssh3["throughput_mean_mib_s"])
            if ssh3["throughput_mean_mib_s"] else None
        )
        latency_ratio = (
            ssh3_latency / ssh_latency
            if ssh_latency and ssh3_latency else None
        )
        throughput_ratio = (
            ssh3_throughput / ssh_throughput
            if ssh_throughput and ssh3_throughput else None
        )
        if latency_ratio is None:
            verdict = "insufficient_completed_transfers"
        elif latency_ratio > 1.05:
            verdict = "ssh3_slower"
        elif latency_ratio < 0.95:
            verdict = "ssh3_faster"
        else:
            verdict = "within_5_pct"
        output.append({
            "scenario": scenario,
            "ssh_completion_median_ms": fmt(
                ssh_latency if ssh_latency is not None else ""
            ),
            "ssh3_completion_median_ms": fmt(
                ssh3_latency if ssh3_latency is not None else ""
            ),
            "ssh3_over_ssh_latency_ratio": fmt(
                latency_ratio if latency_ratio is not None else ""
            ),
            "ssh_throughput_mean_mib_s": fmt(
                ssh_throughput if ssh_throughput is not None else ""
            ),
            "ssh3_throughput_mean_mib_s": fmt(
                ssh3_throughput if ssh3_throughput is not None else ""
            ),
            "ssh3_over_ssh_throughput_ratio": fmt(
                throughput_ratio if throughput_ratio is not None else ""
            ),
            "verdict": verdict,
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


# Tạo bảng riêng diễn giải đúng độ đầy đủ output quan sát qua terminal Mosh.
def mosh_output_rows(scenario_rows):
    return [
        {
            "scenario": row["scenario"],
            "planned_transfers": row["expected_transfers"],
            "attempted_transfers": row["attempted_transfers"],
            "fully_verified_transfers": row["fully_verified_outputs"],
            "command_visible_rate_pct": row["completion_marker_rate_pct"],
            "command_visible_mean_ms": row["command_visible_mean_ms"],
            "command_visible_median_ms": row["command_visible_median_ms"],
            "command_visible_p95_ms": row["command_visible_p95_ms"],
            "fully_verified_output_rate_pct": (
                row["fully_verified_output_rate_pct"]
            ),
            "attempted_fully_verified_output_rate_pct": (
                row["attempted_fully_verified_output_rate_pct"]
            ),
            "expected_output_bytes": row["expected_output_bytes"],
            "verified_output_bytes": row["verified_output_bytes"],
            "verified_output_ratio_pct": row["verified_output_ratio_pct"],
            "verification_scope": "terminal_observed_deterministic_content",
            "raw_lossless_stream_verification": "not_applicable",
        }
        for row in scenario_rows if row["protocol"] == "mosh"
    ]


# Tạo các bảng tổng hợp W2.
def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    transfers = load_csv(result_dir / "transfers.csv", {
        "trial_id", "protocol", "scenario", "stream_role", "status",
        "completion_latency_ms", "first_byte_latency_ms", "marker_latency_ms",
        "throughput_mib_s",
        "output_complete", "content_coverage_pct", "raw_byte_ratio_pct",
        "verified_bytes", "expected_bytes", "bytes_complete", "lines_complete",
        "hash_complete",
        "completion_marker_received",
        "sample_index",
    })
    streams = load_csv(result_dir / "streams.csv", {
        "protocol", "scenario", "stream_role", "stream_completed",
    })
    trials = load_csv(result_dir / "trials.csv", {
        "trial_id", "protocol", "scenario", "stream_count",
        "connection_valid", "ready_streams", "setup_ms", "expected_transfers",
    })
    validate_transfers(transfers, trials)
    scenario_rows = summarize_scenarios(transfers, streams, trials)
    write_csv(result_dir / "scenario_summary.csv", scenario_rows)
    write_csv(
        result_dir / "stream_summary.csv",
        summarize_streams(transfers, streams),
    )
    mosh_rows = mosh_output_rows(scenario_rows)
    if mosh_rows:
        write_csv(result_dir / "mosh_output_completeness.csv", mosh_rows)
    comparisons = compare_ssh3_to_ssh(scenario_rows)
    if comparisons:
        write_csv(result_dir / "ssh3_vs_ssh.csv", comparisons)
        for row in comparisons:
            if row["verdict"] == "ssh3_slower":
                print(
                    f"[CHECK] {row['scenario']}: SSH3 chậm hơn SSH "
                    f"{row['ssh3_over_ssh_latency_ratio']}x theo median",
                    flush=True,
                )
    print(f"Saved W2 summaries to {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
