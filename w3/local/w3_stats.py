#!/usr/bin/env python3
"""
w3_stats.py — Summarise a W3 benchmark CSV file.

Usage:
    python3 w3_stats.py results/w3_ssh2_default.csv

Output example:
    file       : results/w3_ssh2_default.csv
    samples    : 200
    ok         : 200
    fail       : 0
    ok_pct     : 100.00%
    mean_ms    : 63.148
    median_ms  : 61.902
    p95_ms     : 104.221
    p99_ms     : 131.555
    stdev_ms   : 15.308
    cv_pct     : 24.2%
    ci95_half  : ±2.121 ms
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics


def percentile(data: list[float], p: float) -> float:
    """Linear-interpolation percentile (matches numpy default)."""
    if not data:
        return math.nan
    data = sorted(data)
    k = (len(data) - 1) * (p / 100.0)
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[f] * (c - k) + data[c] * (k - f)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="W3 benchmark statistics",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("csvfile", help="CSV file produced by w3_measure.py")
    args = parser.parse_args()

    vals:       list[float] = []
    total_rows  = 0
    ok_count    = 0

    with open(args.csvfile, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip warmup rows
            if row.get("is_warmup", "0") == "1":
                continue
            total_rows += 1
            if row.get("ok", "0") == "1" and row.get("latency_ms", "") != "":
                ok_count += 1
                vals.append(float(row["latency_ms"]))

    fail_count = total_rows - ok_count
    ok_pct     = (ok_count / total_rows * 100.0) if total_rows else 0.0

    # ── Header ──────────────────────────────────────────────────────────────
    print(f"file       : {args.csvfile}")
    print(f"samples    : {total_rows}")
    print(f"ok         : {ok_count}")
    print(f"fail       : {fail_count}")
    print(f"ok_pct     : {ok_pct:.2f}%")

    if not vals:
        print("(no valid measurements)")
        return

    mean   = statistics.mean(vals)
    median = statistics.median(vals)
    p95    = percentile(vals, 95)
    p99    = percentile(vals, 99)

    # Sample stdev (ddof=1) — correct for finite samples drawn from a population
    stdev  = statistics.stdev(vals) if len(vals) > 1 else 0.0

    # Coefficient of Variation: relative dispersion as a percentage
    cv_pct = (stdev / mean * 100.0) if mean > 0 else 0.0

    # 95 % CI half-width using normal approximation (n large enough in practice)
    ci95   = 1.96 * stdev / math.sqrt(len(vals)) if len(vals) > 1 else 0.0

    print(f"mean_ms    : {mean:.3f}")
    print(f"median_ms  : {median:.3f}")
    print(f"p95_ms     : {p95:.3f}")
    print(f"p99_ms     : {p99:.3f}")
    print(f"stdev_ms   : {stdev:.3f}")       # sample stdev, ddof=1
    print(f"cv_pct     : {cv_pct:.1f}%")     # stability indicator
    print(f"ci95_half  : ±{ci95:.3f} ms")    # 95 % CI half-width


if __name__ == "__main__":
    main()