#!/usr/bin/env python3
import argparse
import csv
import math
import statistics

def percentile(data, p):
    if not data:
        return math.nan
    data = sorted(data)
    k = (len(data) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[f] * (c - k) + data[c] * (k - f)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csvfile")
    args = parser.parse_args()

    vals = []
    total = 0
    ok_count = 0

    with open(args.csvfile, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if row["ok"] == "1" and row["latency_ms"] != "":
                ok_count += 1
                vals.append(float(row["latency_ms"]))

    fail_count = total - ok_count
    ok_pct = (ok_count / total * 100.0) if total else 0.0

    print(f"file       : {args.csvfile}")
    print(f"samples    : {total}")
    print(f"ok         : {ok_count}")
    print(f"fail       : {fail_count}")
    print(f"ok_pct     : {ok_pct:.2f}%")

    if vals:
        print(f"mean_ms    : {statistics.mean(vals):.3f}")
        print(f"median_ms  : {statistics.median(vals):.3f}")
        print(f"p95_ms     : {percentile(vals, 95):.3f}")
        print(f"p99_ms     : {percentile(vals, 99):.3f}")
        print(f"std_ms     : {statistics.pstdev(vals):.3f}")

if __name__ == "__main__":
    main()