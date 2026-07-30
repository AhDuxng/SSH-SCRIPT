#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


# Tinh percentile tren toan bo mau ky tu thanh cong cua mot to hop.
def percentile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return ""
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# Dinh dang so thuc cho CSV.
def fmt(value):
    return "" if value == "" else f"{value:.3f}"


# Doc samples.csv va bao ro neu day la schema cu.
def load_samples(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"trial_id", "protocol", "target", "profile", "status", "latency_ms"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"samples.csv thieu cac cot {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"khong co mau trong {path}")
    return rows


# Gom va tinh metric giong cach cu, nhung van dem connection trial doc lap.
def summarize_samples(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["protocol"], row["target"], row["profile"])].append(row)

    summaries = []
    for (protocol, target, profile), group in sorted(groups.items()):
        successful = [
            float(row["latency_ms"])
            for row in group
            if row["status"] == "success" and row["latency_ms"]
        ]
        status_counts = Counter(row["status"] for row in group)
        total = len(group)
        success = len(successful)
        mean = statistics.mean(successful) if successful else ""
        stddev = statistics.stdev(successful) if len(successful) > 1 else 0.0 if successful else ""
        summaries.append({
            "protocol": protocol,
            "target": target,
            "profile": profile,
            "connections": len({row["trial_id"] for row in group}),
            "samples": total,
            "success": success,
            "success_rate_pct": fmt(100.0 * success / total),
            "mean_ms": fmt(mean),
            "median_ms": fmt(statistics.median(successful) if successful else ""),
            "p50_ms": fmt(percentile(successful, 0.50)),
            "p90_ms": fmt(percentile(successful, 0.90)),
            "p95_ms": fmt(percentile(successful, 0.95)),
            "p99_ms": fmt(percentile(successful, 0.99)),
            "stddev_ms": fmt(stddev),
            "timeout_count": status_counts["timeout"],
            "trial_unavailable_count": status_counts["trial_unavailable"],
            "eof_count": status_counts["eof"],
            "failure_count": status_counts["failure"],
            "stall_count": sum(int(row.get("stall") or 0) for row in group),
        })
    return summaries


# Ghi danh sach thong ke ra summary.csv.
def write_summary(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Dieu phoi phan tich pooled-sample tu command line.
def main():
    samples_path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results/samples.csv")
    summary_path = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/results/summary.csv")
    summaries = summarize_samples(load_samples(samples_path))
    write_summary(summary_path, summaries)
    print(f"Saved pooled-sample summary to {summary_path}")


if __name__ == "__main__":
    main()
