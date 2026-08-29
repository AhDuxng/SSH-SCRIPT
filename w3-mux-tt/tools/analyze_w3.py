#!/usr/bin/env python3
"""Kiểm tra tính đầy đủ và tổng hợp latency/reliability W3."""

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


SCENARIO_FIELDS = [
    "protocol", "editor", "scenario", "trials", "measurement_mode",
    "connection_valid_rate_pct", "expected_keystrokes", "completed_keystrokes",
    "keystroke_completion_rate_pct", "stall_count", "stall_rate_pct",
    "timeout_count", "timeout_rate_pct", "mean_ms", "median_ms", "p95_ms",
    "p99_ms", "expected_streams", "completed_streams",
    "stream_completion_rate_pct", "setup_mean_ms", "setup_median_ms",
    "setup_p95_ms", "setup_p99_ms",
]

STREAM_SUMMARY_FIELDS = [
    "protocol", "editor", "scenario", "stream_role", "trials",
    "measurement_mode", "expected_keystrokes", "completed_keystrokes",
    "keystroke_completion_rate_pct", "stall_count", "stall_rate_pct",
    "timeout_count", "timeout_rate_pct", "mean_ms", "median_ms", "p95_ms",
    "p99_ms", "completed_streams", "stream_completion_rate_pct",
]

COMPARE_FIELDS = [
    "editor", "scenario", "ssh_median_ms", "ssh3_median_ms",
    "ssh3_over_ssh_latency_ratio", "ssh_completion_rate_pct",
    "ssh3_completion_rate_pct", "verdict",
]







def validate(order_rows, key_rows, stream_rows, trial_rows, probe_chars):
    errors = []
    planned = {row["trial_id"]: int(row["stream_count"]) for row in order_rows}
    actual_trials = {row["trial_id"] for row in trial_rows}
    if set(planned) != actual_trials:
        errors.append(
            f"trial IDs sai: thiếu={sorted(set(planned) - actual_trials)} "
            f"thừa={sorted(actual_trials - set(planned))}"
        )
    key_groups = defaultdict(list)
    for row in key_rows:
        key_groups[(row["trial_id"], row["stream_role"])].append(row)
    stream_groups = defaultdict(list)
    for row in stream_rows:
        stream_groups[row["trial_id"]].append(row)
    for trial_id, stream_count in planned.items():
        expected_roles = {f"interactive_{index}" for index in range(stream_count)}
        actual_roles = {role for current, role in key_groups if current == trial_id}
        if actual_roles != expected_roles:
            errors.append(f"{trial_id}: roles={sorted(actual_roles)} expected={sorted(expected_roles)}")
        for role in expected_roles:
            rows = key_groups.get((trial_id, role), [])
            if len(rows) != probe_chars:
                errors.append(f"{trial_id}/{role}: keystrokes={len(rows)} expected={probe_chars}")
            indices = sorted(int(row["char_index"]) for row in rows)
            if indices != list(range(1, probe_chars + 1)):
                errors.append(f"{trial_id}/{role}: char_index không liên tục")
        if len(stream_groups.get(trial_id, [])) != stream_count:
            errors.append(f"{trial_id}: stream rows sai số lượng")
    if errors:
        raise ValueError("W3 result không đầy đủ:\n- " + "\n- ".join(errors[:30]))


def summarize_group(keys, streams, trials):
    completed = [row for row in keys if row["completed"] == "1"]
    values = [float(row["latency_ms"]) for row in completed if row["latency_ms"]]
    latency = latency_stats(values)
    setups = [float(row["setup_ms"]) for row in trials if row["setup_ms"]]
    setup = latency_stats(setups)
    stalls = sum(row["stall"] == "1" for row in keys)
    timeouts = sum(row["timeout"] == "1" for row in keys)
    complete_streams = sum(row["stream_complete"] == "1" for row in streams)
    return {
        "protocol": keys[0]["protocol"],
        "editor": keys[0]["editor"],
        "scenario": keys[0]["scenario"],
        "trials": len(trials),
        "measurement_mode": keys[0]["measurement_mode"],
        "connection_valid_rate_pct": rate_pct(sum(row["connection_valid"] == "1" for row in trials), len(trials)),
        "expected_keystrokes": len(keys),
        "completed_keystrokes": len(completed),
        "keystroke_completion_rate_pct": rate_pct(len(completed), len(keys)),
        "stall_count": stalls,
        "stall_rate_pct": rate_pct(stalls, len(keys)),
        "timeout_count": timeouts,
        "timeout_rate_pct": rate_pct(timeouts, len(keys)),
        "mean_ms": latency["mean_ms"],
        "median_ms": latency["median_ms"],
        "p95_ms": latency["p95_ms"],
        "p99_ms": latency["p99_ms"],
        "expected_streams": len(streams),
        "completed_streams": complete_streams,
        "stream_completion_rate_pct": rate_pct(complete_streams, len(streams)),
        "setup_mean_ms": setup["mean_ms"],
        "setup_median_ms": setup["median_ms"],
        "setup_p95_ms": setup["p95_ms"],
        "setup_p99_ms": setup["p99_ms"],
    }


def summarize_scenarios(key_rows, stream_rows, trial_rows):
    key_groups, stream_groups, trial_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in key_rows:
        key_groups[(row["protocol"], row["editor"], row["scenario"])].append(row)
    for row in stream_rows:
        stream_groups[(row["protocol"], row["editor"], row["scenario"])].append(row)
    for row in trial_rows:
        trial_groups[(row["protocol"], row["editor"], row["scenario"])].append(row)
    return [
        summarize_group(key_groups[key], stream_groups[key], trial_groups[key])
        for key in sorted(key_groups)
    ]


def summarize_streams(key_rows, stream_rows):
    key_groups, result_groups = defaultdict(list), defaultdict(list)
    for row in key_rows:
        key_groups[(row["protocol"], row["editor"], row["scenario"], row["stream_role"])].append(row)
    for row in stream_rows:
        result_groups[(row["protocol"], row["editor"], row["scenario"], row["stream_role"])].append(row)
    output = []
    for key in sorted(key_groups):
        keys = key_groups[key]
        results = result_groups[key]
        completed = [row for row in keys if row["completed"] == "1"]
        values = [float(row["latency_ms"]) for row in completed if row["latency_ms"]]
        latency = latency_stats(values)
        stalls = sum(row["stall"] == "1" for row in keys)
        timeouts = sum(row["timeout"] == "1" for row in keys)
        complete_streams = sum(row["stream_complete"] == "1" for row in results)
        output.append({
            "protocol": key[0], "editor": key[1], "scenario": key[2], "stream_role": key[3],
            "trials": len(results), "measurement_mode": keys[0]["measurement_mode"],
            "expected_keystrokes": len(keys), "completed_keystrokes": len(completed),
            "keystroke_completion_rate_pct": rate_pct(len(completed), len(keys)),
            "stall_count": stalls, "stall_rate_pct": rate_pct(stalls, len(keys)),
            "timeout_count": timeouts, "timeout_rate_pct": rate_pct(timeouts, len(keys)),
            "mean_ms": latency["mean_ms"], "median_ms": latency["median_ms"],
            "p95_ms": latency["p95_ms"], "p99_ms": latency["p99_ms"],
            "completed_streams": complete_streams,
            "stream_completion_rate_pct": rate_pct(complete_streams, len(results)),
        })
    return output


def compare_ssh(summary):
    lookup = {(row["protocol"], row["editor"], row["scenario"]): row for row in summary}
    output = []
    for editor in ("vim", "nano"):
        for scenario in ("W3-I1", "W3-I2", "W3-I4"):
            ssh = lookup.get(("ssh", editor, scenario))
            ssh3 = lookup.get(("ssh3", editor, scenario))
            if not ssh or not ssh3 or not ssh["median_ms"] or not ssh3["median_ms"]:
                continue
            ratio = float(ssh3["median_ms"]) / float(ssh["median_ms"])
            verdict = "CHECK_SSH3_SLOWER" if ratio > 1.05 else "OK"
            output.append({
                "editor": editor, "scenario": scenario,
                "ssh_median_ms": ssh["median_ms"], "ssh3_median_ms": ssh3["median_ms"],
                "ssh3_over_ssh_latency_ratio": f"{ratio:.3f}",
                "ssh_completion_rate_pct": ssh["keystroke_completion_rate_pct"],
                "ssh3_completion_rate_pct": ssh3["keystroke_completion_rate_pct"],
                "verdict": verdict,
            })
    return output


def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    order_rows = read_rows(result_dir / "experiment_order.csv")
    key_rows = read_rows(result_dir / "keystrokes.csv")
    stream_rows = read_rows(result_dir / "streams.csv")
    trial_rows = read_rows(result_dir / "trials.csv")
    if not key_rows:
        raise ValueError("keystrokes.csv không có dữ liệu")
    probe_chars = int(key_rows[0]["char_total"])
    validate(order_rows, key_rows, stream_rows, trial_rows, probe_chars)
    scenario_summary = summarize_scenarios(key_rows, stream_rows, trial_rows)
    stream_summary = summarize_streams(key_rows, stream_rows)
    comparison = compare_ssh(scenario_summary)
    write_summary(result_dir / "scenario_summary.csv", scenario_summary)
    write_summary(result_dir / "stream_summary.csv", stream_summary)
    write_summary(result_dir / "ssh3_vs_ssh.csv", comparison)
    for row in comparison:
        if row["verdict"] != "OK":
            print(
                f"[CHECK] {row['editor']} {row['scenario']}: SSH3/SSH median="
                f"{row['ssh3_over_ssh_latency_ratio']}", flush=True,
            )
    incomplete = [row for row in trial_rows if row.get("status") != "completed"]
    if incomplete:
        trial_ids = ", ".join(row["trial_id"] for row in incomplete[:5])
        suffix = " ..." if len(incomplete) > 5 else ""
        print(
            f"[WARN] analyzed {len(key_rows)} keystrokes from {len(trial_rows)} "
            f"trials; completed={len(trial_rows) - len(incomplete)}/{len(trial_rows)}; "
            f"incomplete={trial_ids}{suffix}",
            flush=True,
        )
    else:
        print(
            f"[OK] analyzed {len(key_rows)} keystrokes from {len(trial_rows)} "
            f"trials; completed={len(trial_rows)}/{len(trial_rows)}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
