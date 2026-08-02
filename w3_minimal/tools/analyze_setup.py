#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


# Tinh percentile noi suy tren cac phien doc lap.
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


# Doc tung mau session setup doc lap.
def load_setup_samples(path):
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"trial_id", "protocol", "status", "session_setup_ms"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"setup_samples.csv thieu cac cot {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"khong co mau setup trong {path}")
    return rows


# Thong ke setup theo giao thuc, moi dong dau vao la mot phien moi.
def summarize_setup(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["protocol"]].append(row)

    summaries = []
    for protocol, group in sorted(groups.items()):
        valid = [
            float(row["session_setup_ms"])
            for row in group
            if row["status"] == "success" and row.get("session_setup_ms")
        ]
        counts = Counter(row["status"] for row in group)
        total = len(group)
        summaries.append({
            "protocol": protocol,
            "sessions": total,
            "success": len(valid),
            "success_rate_pct": fmt(100.0 * len(valid) / total if total else 0.0),
            "mean_ms": fmt(statistics.mean(valid) if valid else ""),
            "median_ms": fmt(statistics.median(valid) if valid else ""),
            "p90_ms": fmt(percentile(valid, 0.90)),
            "p95_ms": fmt(percentile(valid, 0.95)),
            "timeout_count": counts["timeout"],
            "eof_count": counts["eof"],
            "failure_count": counts["failure"],
        })
    return summaries


# Ghi bang thong ke session setup.
def write_summary(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Tao setup_summary.csv tu setup_samples.csv.
def main():
    samples_path = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results/setup_samples.csv")
    output_path = Path(sys.argv[2] if len(sys.argv) > 2 else "artifacts/results/setup_summary.csv")
    summaries = summarize_setup(load_setup_samples(samples_path))
    write_summary(output_path, summaries)
    print(f"Saved session-setup summary to {output_path}")


if __name__ == "__main__":
    main()
