#!/usr/bin/env python3
"""Validate W4 result cardinality and create scenario/background summaries."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from harness.results import read_rows, write_summary  # noqa: E402
from harness.statistics import (  # noqa: E402
    fmt, latency_stats, percentile, rate_pct, summarize_latency,
)


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
        latency = latency_stats(values)
        setup = latency_stats([float(row["setup_ms"]) for row in trial_rows if row["setup_ms"]])
        stalls = sum(row["stall"] == "1" for row in current)
        timeouts = sum(row["timeout"] == "1" for row in current)
        complete_streams = sum(row["stream_complete"] == "1" for row in stream_rows)
        result.append({
            "protocol": key[0], "editor": key[1], "scenario": key[2], "trials": len(trial_rows),
            "expected_keystrokes": len(current), "completed_keystrokes": len(completed),
            "keystroke_completion_rate_pct": rate_pct(len(completed), len(current)),
            "stall_count": stalls, "stall_rate_pct": rate_pct(stalls, len(current)),
            "timeout_count": timeouts, "timeout_rate_pct": rate_pct(timeouts, len(current)),
            "mean_ms": latency["mean_ms"], "median_ms": latency["median_ms"],
            "p95_ms": latency["p95_ms"], "p99_ms": latency["p99_ms"],
            "expected_streams": len(stream_rows), "completed_streams": complete_streams,
            "stream_completion_rate_pct": rate_pct(complete_streams, len(stream_rows)),
            "setup_mean_ms": setup["mean_ms"], "setup_median_ms": setup["median_ms"],
            "setup_p95_ms": setup["p95_ms"], "setup_p99_ms": setup["p99_ms"],
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
        latency = latency_stats(values)
        complete_outputs = sum(row["output_complete"] == "1" for row in current)
        result.append({
            "protocol": key[0], "editor": key[1], "scenario": key[2],
            "stream_role": key[3], "workload_type": key[4], "samples": len(current),
            "completed_samples": len(completed), "completion_rate_pct": rate_pct(len(completed), len(current)),
            "timeout_samples": sum(row["timed_out"] == "1" for row in current),
            "complete_outputs": complete_outputs,
            "output_completeness_pct": rate_pct(complete_outputs, len(current)),
            "expected_bytes": sum(int(row["expected_bytes"] or 0) for row in current),
            "received_bytes": sum(int(row["received_bytes"] or 0) for row in current),
            "mean_ms": latency["mean_ms"], "median_ms": latency["median_ms"],
            "p95_ms": latency["p95_ms"], "p99_ms": latency["p99_ms"],
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
    order, keys = read_rows(root / "experiment_order.csv"), read_rows(root / "keystrokes.csv")
    background, streams, trials = read_rows(root / "background.csv"), read_rows(root / "streams.csv"), read_rows(root / "trials.csv")
    validate(order, keys, streams, trials)
    interactive = interactive_summary(keys, streams, trials)
    bg = background_summary(background)
    comparisons = compare(interactive)
    write_summary(root / "scenario_summary.csv", interactive)
    write_summary(root / "background_summary.csv", bg)
    write_summary(root / "ssh3_vs_ssh.csv", comparisons)
    for row in comparisons:
        if row["verdict"] != "OK":
            print(f"[CHECK] {row['editor']} {row['scenario']}: SSH3/SSH={row['ssh3_over_ssh_latency_ratio']}")
    print(f"[OK] analyzed {len(keys)} keystrokes, {len(background)} background samples, {len(trials)} trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
