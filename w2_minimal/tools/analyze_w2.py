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
    return "" if value == "" else f"{value:.6f}"


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


# Tính các metric chung và thêm prefix/suffix vào tên cột.
def numeric_stats(values, prefix, suffix=""):
    result = {
        f"{prefix}_min{suffix}": fmt(min(values) if values else ""),
        f"{prefix}_mean{suffix}": fmt(statistics.mean(values) if values else ""),
        f"{prefix}_median{suffix}": fmt(statistics.median(values) if values else ""),
        f"{prefix}_p50{suffix}": fmt(percentile(values, 0.50)),
        f"{prefix}_p90{suffix}": fmt(percentile(values, 0.90)),
        f"{prefix}_p95{suffix}": fmt(percentile(values, 0.95)),
        f"{prefix}_p99{suffix}": fmt(percentile(values, 0.99)),
        f"{prefix}_max{suffix}": fmt(max(values) if values else ""),
        f"{prefix}_stddev{suffix}": fmt(
            statistics.stdev(values) if len(values) > 1 else 0.0 if values else ""
        ),
    }
    return result


# Tính metric latency với tên cột tương thích W1/W3.
def latency_stats(values):
    base = numeric_stats(values, "latency", "_ms")
    renamed = {key.replace("latency_", "", 1): value for key, value in base.items()}
    renamed["ci95_half_width_ms"] = fmt(
        1.96 * statistics.stdev(values) / math.sqrt(len(values))
        if len(values) > 1 else 0.0 if values else ""
    )
    return renamed


# Đếm tỷ lệ hoàn thành và từng loại lỗi của một nhóm trial.
def status_stats(group, values):
    counts = Counter(row["status"] for row in group)
    total = len(group)
    return {
        "samples": total,
        "success": len(values),
        "success_rate_pct": f"{100.0 * len(values) / total if total else 0.0:.3f}",
        "timeout_count": counts["timeout"],
        "eof_count": counts["eof"],
        "failure_count": counts["failure"],
        "command_error_count": counts["command_error"],
    }


# Tổng hợp latency, dung lượng và throughput theo protocol × workload.
def summarize_samples(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["protocol"], row["workload"])].append(row)
    output = []
    for (protocol, workload), group in sorted(groups.items()):
        successful = [row for row in group if row["status"] == "success"]
        latencies = [float(row["latency_ms"]) for row in successful if row["latency_ms"]]
        byte_values = [float(row["output_bytes"]) for row in successful if row["output_bytes"]]
        line_values = [float(row["output_lines"]) for row in successful if row["output_lines"]]
        throughput = [
            float(row["throughput_mib_s"])
            for row in successful if row["throughput_mib_s"]
        ]
        output.append({
            "protocol": protocol,
            "workload": workload,
            "connections": len({row["trial_id"] for row in group}),
            **status_stats(group, latencies),
            **latency_stats(latencies),
            **numeric_stats(byte_values, "output_bytes"),
            **numeric_stats(line_values, "output_lines"),
            **numeric_stats(throughput, "throughput_mib_s"),
        })
    return output


# Tổng hợp thời gian mở session độc lập theo giao thức.
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
        sessions = status.pop("samples")
        output.append({
            "protocol": protocol,
            "sessions": sessions,
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


# Đọc raw CSV và tạo lại summary workload cùng session setup.
def main():
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    samples = load_csv(
        result_dir / "samples.csv",
        {
            "trial_id", "protocol", "workload", "status", "latency_ms",
            "output_bytes", "output_lines", "throughput_mib_s",
        },
    )
    setups = load_csv(
        result_dir / "setup_samples.csv",
        {"trial_id", "protocol", "status", "session_setup_ms"},
    )
    write_csv(result_dir / "summary.csv", summarize_samples(samples))
    write_csv(result_dir / "setup_summary.csv", summarize_setup(setups))
    print(f"Saved W2 workload and session-setup summaries to {result_dir}")


if __name__ == "__main__":
    main()
