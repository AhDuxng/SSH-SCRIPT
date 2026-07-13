#!/usr/bin/env python3
import csv
import math
import statistics
import sys
from collections import defaultdict


def percentile(xs, p):
    if not xs:
        return ""
    xs = sorted(xs)
    k = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[int(k)]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt(x):
    return "" if x == "" else f"{x:.3f}"


def main():
    in_path = sys.argv[1] if len(sys.argv) > 1 else "results/samples.csv"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "results/summary.csv"

    groups = defaultdict(list)
    with open(in_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            target = row.get("target") or "shell"
            groups[(row["protocol"], target, row["profile"])].append(row)

    means = {}
    summaries = []
    for (protocol, target, profile), rows in sorted(groups.items()):
        lat = [float(r["latency_ms"]) for r in rows if r["status"] == "success" and r["latency_ms"]]
        total = len(rows)
        success = len(lat)
        timeouts = sum(1 for r in rows if r["status"] == "timeout")
        mismatch = sum(1 for r in rows if r["status"] == "token_mismatch")
        failures = sum(1 for r in rows if r["status"] in ("failure", "eof"))
        unavailable = sum(1 for r in rows if r["status"] == "target_unavailable")
        stalls = sum(int(r["stall"] or 0) for r in rows)
        ch_open_fail = max(int(r["channel_open_failures"] or 0) for r in rows) if rows else 0

        mean = statistics.mean(lat) if lat else ""
        std = statistics.stdev(lat) if len(lat) > 1 else 0.0 if lat else ""
        if lat:
            means[(protocol, target, profile)] = mean

        base = means.get((protocol, target, "c0_only"), "")
        inflation = ""
        if lat and base and profile != "c0_only":
            inflation = ((mean - base) / base) * 100.0

        summaries.append({
            "protocol": protocol,
            "target": target,
            "profile": profile,
            "samples": total,
            "success": success,
            "success_rate_pct": fmt((success / total) * 100.0 if total else ""),
            "mean_ms": fmt(mean),
            "p50_ms": fmt(percentile(lat, 50)),
            "p95_ms": fmt(percentile(lat, 95)),
            "p99_ms": fmt(percentile(lat, 99)),
            "stddev_ms": fmt(std),
            "latency_inflation_vs_c0_pct": fmt(inflation),
            "interactive_stall_count": stalls,
            "channel_open_failure_count": ch_open_fail,
            "timeout_count": timeouts,
            "token_mismatch_count": mismatch,
            "target_unavailable_count": unavailable,
            "failure_count": failures,
        })

    fields = list(summaries[0].keys()) if summaries else []
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summaries)

    print(f"Saved summary to {out_path}")


if __name__ == "__main__":
    main()
