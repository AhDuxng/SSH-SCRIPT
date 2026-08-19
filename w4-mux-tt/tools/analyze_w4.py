#!/usr/bin/env python3
"""Validate W4 result cardinality and create scenario/background summaries."""

from __future__ import annotations

import csv
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path


INTERACTIVE_FIELDS = (
    "protocol", "editor", "scenario", "trials", "expected_keystrokes",
    "completed_keystrokes", "keystroke_completion_rate_pct", "stall_count",
    "stall_rate_pct", "timeout_count", "timeout_rate_pct", "mean_ms",
    "median_ms", "p95_ms", "p99_ms", "expected_streams",
    "completed_streams", "stream_completion_rate_pct", "setup_mean_ms",
    "setup_median_ms", "setup_p95_ms", "setup_p99_ms",
)
BACKGROUND_SUMMARY_FIELDS = (
    "protocol", "editor", "scenario", "stream_role", "workload_type", "samples",
    "completed_samples", "completion_rate_pct", "timeout_samples",
    "complete_outputs", "output_completeness_pct", "expected_bytes",
    "received_bytes", "mean_ms", "median_ms", "p95_ms", "p99_ms",
)
COMPARE_FIELDS = (
    "editor", "scenario", "ssh_median_ms", "ssh3_median_ms",
    "ssh3_over_ssh_latency_ratio", "ssh_completion_rate_pct",
    "ssh3_completion_rate_pct", "verdict",
)


def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def pct(n, d):
    return "" if not d else f"{100*n/d:.3f}"


def percentile(values, p):
    if not values:
        return ""
    values = sorted(values)
    pos = (len(values) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return values[lo] if lo == hi else values[lo] + (values[hi] - values[lo]) * (pos - lo)


def stats(values):
    return {
        "mean": f"{statistics.mean(values):.3f}" if values else "",
        "median": f"{statistics.median(values):.3f}" if values else "",
        "p95": f"{percentile(values, .95):.3f}" if values else "",
        "p99": f"{percentile(values, .99):.3f}" if values else "",
    }


def validate(order, keys, streams, trials):
    planned = {row["trial_id"]: int(row["logical_workload_count"]) for row in order}
    errors = []
    if set(planned) != {row["trial_id"] for row in trials}:
        errors.append("trial IDs do not match experiment_order.csv")
    key_groups, stream_groups = defaultdict(list), defaultdict(list)
    for row in keys:
        key_groups[row["trial_id"]].append(row)
    for row in streams:
        stream_groups[row["trial_id"]].append(row)
    for trial_id, count in planned.items():
        current = key_groups[trial_id]
        if len(current) != 100:
            errors.append(f"{trial_id}: keystrokes={len(current)} expected=100")
        indices = sorted(int(row["char_index"]) for row in current)
        if indices != list(range(1, 101)):
            errors.append(f"{trial_id}: char indices are not 1..100")
        if len(stream_groups[trial_id]) != count:
            errors.append(f"{trial_id}: stream summaries={len(stream_groups[trial_id])} expected={count}")
    if errors:
        raise ValueError("W4 results are incomplete:\n- " + "\n- ".join(errors[:40]))


def interactive_summary(keys, streams, trials):
    kg, sg, tg = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in keys:
        kg[(row["protocol"], row["editor"], row["scenario"])].append(row)
    for row in streams:
        if row["stream_role"] == "interactive_0":
            sg[(row["protocol"], row["editor"], row["scenario"])].append(row)
    for row in trials:
        tg[(row["protocol"], row["editor"], row["scenario"])].append(row)
    result = []
    for key in sorted(kg):
        current, stream_rows, trial_rows = kg[key], sg[key], tg[key]
        completed = [row for row in current if row["completed"] == "1"]
        values = [float(row["latency_ms"]) for row in completed if row["latency_ms"]]
        latency = stats(values)
        setup = stats([float(row["setup_ms"]) for row in trial_rows if row["setup_ms"]])
        stalls = sum(row["stall"] == "1" for row in current)
        timeouts = sum(row["timeout"] == "1" for row in current)
        complete_streams = sum(row["stream_complete"] == "1" for row in stream_rows)
        result.append({
            "protocol": key[0], "editor": key[1], "scenario": key[2], "trials": len(trial_rows),
            "expected_keystrokes": len(current), "completed_keystrokes": len(completed),
            "keystroke_completion_rate_pct": pct(len(completed), len(current)),
            "stall_count": stalls, "stall_rate_pct": pct(stalls, len(current)),
            "timeout_count": timeouts, "timeout_rate_pct": pct(timeouts, len(current)),
            "mean_ms": latency["mean"], "median_ms": latency["median"],
            "p95_ms": latency["p95"], "p99_ms": latency["p99"],
            "expected_streams": len(stream_rows), "completed_streams": complete_streams,
            "stream_completion_rate_pct": pct(complete_streams, len(stream_rows)),
            "setup_mean_ms": setup["mean"], "setup_median_ms": setup["median"],
            "setup_p95_ms": setup["p95"], "setup_p99_ms": setup["p99"],
        })
    return result


def background_summary(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["protocol"], row["editor"], row["scenario"], row["stream_role"], row["workload_type"])].append(row)
    result = []
    for key in sorted(groups):
        current = groups[key]
        completed = [row for row in current if row["status"] == "completed"]
        values = [float(row["completion_latency_ms"]) for row in completed if row["completion_latency_ms"]]
        latency = stats(values)
        complete_outputs = sum(row["output_complete"] == "1" for row in current)
        result.append({
            "protocol": key[0], "editor": key[1], "scenario": key[2],
            "stream_role": key[3], "workload_type": key[4], "samples": len(current),
            "completed_samples": len(completed), "completion_rate_pct": pct(len(completed), len(current)),
            "timeout_samples": sum(row["timed_out"] == "1" for row in current),
            "complete_outputs": complete_outputs,
            "output_completeness_pct": pct(complete_outputs, len(current)),
            "expected_bytes": sum(int(row["expected_bytes"] or 0) for row in current),
            "received_bytes": sum(int(row["received_bytes"] or 0) for row in current),
            "mean_ms": latency["mean"], "median_ms": latency["median"],
            "p95_ms": latency["p95"], "p99_ms": latency["p99"],
        })
    return result


def compare(rows):
    lookup = {(row["protocol"], row["editor"], row["scenario"]): row for row in rows}
    result = []
    for editor in ("vim", "nano"):
        for scenario in ("W4-CMD", "W4-OUTPUT", "W4-MIX"):
            ssh, ssh3 = lookup.get(("ssh", editor, scenario)), lookup.get(("ssh3", editor, scenario))
            if not ssh or not ssh3 or not ssh["median_ms"] or not ssh3["median_ms"]:
                continue
            ratio = float(ssh3["median_ms"]) / float(ssh["median_ms"])
            result.append({
                "editor": editor, "scenario": scenario,
                "ssh_median_ms": ssh["median_ms"], "ssh3_median_ms": ssh3["median_ms"],
                "ssh3_over_ssh_latency_ratio": f"{ratio:.3f}",
                "ssh_completion_rate_pct": ssh["keystroke_completion_rate_pct"],
                "ssh3_completion_rate_pct": ssh3["keystroke_completion_rate_pct"],
                "verdict": "CHECK_SSH3_SLOWER" if ratio > 1.05 else "OK",
            })
    return result


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    order, keys = load(root / "experiment_order.csv"), load(root / "keystrokes.csv")
    background, streams, trials = load(root / "background.csv"), load(root / "streams.csv"), load(root / "trials.csv")
    validate(order, keys, streams, trials)
    interactive = interactive_summary(keys, streams, trials)
    bg = background_summary(background)
    comparisons = compare(interactive)
    write(root / "scenario_summary.csv", INTERACTIVE_FIELDS, interactive)
    write(root / "background_summary.csv", BACKGROUND_SUMMARY_FIELDS, bg)
    write(root / "ssh3_vs_ssh.csv", COMPARE_FIELDS, comparisons)
    for row in comparisons:
        if row["verdict"] != "OK":
            print(f"[CHECK] {row['editor']} {row['scenario']}: SSH3/SSH={row['ssh3_over_ssh_latency_ratio']}")
    print(f"[OK] analyzed {len(keys)} keystrokes, {len(background)} background samples, {len(trials)} trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
