#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
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


# Đọc CSV và xác minh các cột bắt buộc.
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


# Tính đầy đủ các metric độ trễ cho một nhóm mẫu thành công.
def latency_stats(values):
    return {
        "min_ms": fmt(min(values) if values else ""),
        "mean_ms": fmt(statistics.mean(values) if values else ""),
        "median_ms": fmt(statistics.median(values) if values else ""),
        "p50_ms": fmt(percentile(values, 0.50)),
        "p90_ms": fmt(percentile(values, 0.90)),
        "p95_ms": fmt(percentile(values, 0.95)),
        "p99_ms": fmt(percentile(values, 0.99)),
        "max_ms": fmt(max(values) if values else ""),
        "stddev_ms": fmt(statistics.stdev(values) if len(values) > 1 else 0.0 if values else ""),
        "ci95_half_width_ms": fmt(
            1.96 * statistics.stdev(values) / math.sqrt(len(values))
            if len(values) > 1 else 0.0 if values else ""
        ),
    }


# Đếm tỷ lệ hoàn thành và từng loại lỗi của một nhóm.
def status_stats(group, values):
    counts = Counter(row["status"] for row in group)
    total = len(group)
    return {
        "samples": total,
        "success": len(values),
        "success_rate_pct": fmt(100.0 * len(values) / total if total else 0.0),
        "timeout_count": counts["timeout"],
        "eof_count": counts["eof"],
        "failure_count": counts["failure"],
        "trial_unavailable_count": counts["trial_unavailable"],
        "skipped_count": counts["skipped"],
    }


# Tổng hợp mẫu theo giao thức và từng lệnh hệ thống.
def summarize_commands(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["warmup"] == "0":
            groups[(row["protocol"], row["command"])].append(row)
    output = []
    for (protocol, command), group in sorted(groups.items()):
        successful = [row for row in group if row["status"] == "success" and row["latency_ms"]]
        values = [float(row["latency_ms"]) for row in successful]
        byte_values = [int(row["output_bytes"]) for row in successful if row["output_bytes"]]
        output.append({
            "protocol": protocol,
            "command": command,
            "connections": len({row["trial_id"] for row in group}),
            "loops": len({(row["trial_id"], row["loop_index"]) for row in group}),
            **status_stats(group, values),
            **latency_stats(values),
            "output_bytes_mean": fmt(statistics.mean(byte_values) if byte_values else ""),
            "output_bytes_min": min(byte_values) if byte_values else "",
            "output_bytes_max": max(byte_values) if byte_values else "",
        })
    return output


# Tổng hợp thời gian hoàn tất toàn bộ vòng năm lệnh.
def summarize_loops(rows):
    groups = defaultdict(list)
    for row in rows:
        if row["warmup"] == "0":
            groups[row["protocol"]].append(row)
    output = []
    for protocol, group in sorted(groups.items()):
        values = [
            float(row["loop_latency_ms"])
            for row in group
            if row["status"] == "success" and row["loop_latency_ms"]
        ]
        status = status_stats(group, values)
        loop_count = status.pop("samples")
        output.append({
            "protocol": protocol,
            "connections": len({row["trial_id"] for row in group}),
            "loops": loop_count,
            **status,
            **latency_stats(values),
        })
    return output


# Tổng hợp thời gian mở các session độc lập theo giao thức.
def summarize_setup(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["protocol"]].append(row)
    output = []
    for protocol, group in sorted(groups.items()):
        values = [
            float(row["session_setup_ms"])
            for row in group
            if row["status"] == "success" and row["session_setup_ms"]
        ]
        status = status_stats(group, values)
        session_count = status.pop("samples")
        output.append({
            "protocol": protocol,
            "sessions": session_count,
            **status,
            **latency_stats(values),
        })
    return output


# Ghi bảng summary ra CSV.
def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Đọc raw CSV và tạo lại ba bảng summary từ command line.
def main():
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    samples = load_csv(
        result_dir / "samples.csv",
        {"trial_id", "protocol", "loop_index", "warmup", "command", "status", "latency_ms", "output_bytes"},
    )
    loops = load_csv(
        result_dir / "loops.csv",
        {"trial_id", "protocol", "warmup", "status", "loop_latency_ms"},
    )
    setups = load_csv(
        result_dir / "setup_samples.csv",
        {"trial_id", "protocol", "status", "session_setup_ms"},
    )
    write_csv(result_dir / "summary.csv", summarize_commands(samples))
    write_csv(result_dir / "loop_summary.csv", summarize_loops(loops))
    write_csv(result_dir / "setup_summary.csv", summarize_setup(setups))
    print(f"Saved command, loop and session-setup summaries to {result_dir}")


if __name__ == "__main__":
    main()
