#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def percentile(values, pct):
    if not values:
        return ""
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (pct / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return values[int(rank)]
    return values[lo] + (values[hi] - values[lo]) * (rank - lo)


def fmt(value):
    return "" if value == "" else f"{value:.3f}"


def main():
    in_path = Path(sys.argv[1] if len(sys.argv) > 1 else "results_mux/mux_samples.csv")
    out_path = Path(sys.argv[2] if len(sys.argv) > 2 else "results_mux/mux_summary.csv")
    groups = defaultdict(list)
    with in_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            groups[(row["protocol"], row["profile"])].append(row)

    rows = []
    for (protocol, profile), records in sorted(groups.items()):
        latencies = [float(r["latency_ms"]) for r in records if r["status"] == "success" and r["latency_ms"]]
        total = len(records)
        success = len(latencies)
        timeouts = sum(1 for r in records if r["status"] == "timeout")
        failures = sum(1 for r in records if r["status"] == "failure")
        mean = statistics.mean(latencies) if latencies else ""
        stdev = statistics.stdev(latencies) if len(latencies) > 1 else 0.0 if latencies else ""
        rows.append({
            "protocol": protocol,
            "profile": profile,
            "samples": total,
            "success": success,
            "success_rate_pct": fmt((success / total) * 100.0 if total else ""),
            "mean_ms": fmt(mean),
            "p50_ms": fmt(percentile(latencies, 50)),
            "p95_ms": fmt(percentile(latencies, 95)),
            "p99_ms": fmt(percentile(latencies, 99)),
            "stddev_ms": fmt(stdev),
            "timeout_count": timeouts,
            "failure_count": failures,
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    print(f"Saved summary to {out_path}")


if __name__ == "__main__":
    main()
