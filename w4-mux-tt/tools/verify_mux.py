#!/usr/bin/env python3
"""Verify W4's one-connection transport topology from stream audit evidence."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def expected_roles(scenario):
    roles = ["interactive_0"]
    if scenario in {"W4-CMD", "W4-MIX"}:
        roles.append("command_0")
    if scenario in {"W4-OUTPUT", "W4-MIX"}:
        roles.append("output_0")
    return set(roles)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    trials = load(root / "trials.csv")
    audits = load(root / "stream_audit.csv")
    streams = load(root / "streams.csv")
    grouped = defaultdict(list)
    stream_grouped = defaultdict(list)
    for row in audits:
        grouped[row["trial_id"]].append(row)
    for row in streams:
        stream_grouped[row["trial_id"]].append(row)
    errors = []
    for trial in trials:
        rows = grouped[trial["trial_id"]]
        measured_streams = stream_grouped[trial["trial_id"]]
        count = int(trial["logical_workload_count"])
        roles = expected_roles(trial["scenario"])
        if len(rows) != count:
            errors.append(f"{trial['trial_id']}: audit rows={len(rows)} expected={count}")
            continue
        if {row["stream_role"] for row in rows} != roles:
            errors.append(f"{trial['trial_id']}: audited stream roles do not match {sorted(roles)}")
            continue
        if trial["connection_valid"] != "1" or trial["socket_count"] != "1":
            errors.append(f"{trial['trial_id']}: invalid connection/socket evidence")
            # A trial that never opened cannot possibly produce a final editor
            # file or meaningful stream IDs.  Avoid reporting those downstream
            # consequences as independent verification failures.
            continue
        if len(measured_streams) != count:
            errors.append(
                f"{trial['trial_id']}: measured stream rows={len(measured_streams)} "
                f"expected={count}"
            )
            continue
        if {row["stream_role"] for row in measured_streams} != roles:
            errors.append(f"{trial['trial_id']}: measured stream roles do not match {sorted(roles)}")
            continue
        keystrokes_complete = (
            trial["completed_keystrokes"] == trial["expected_keystrokes"]
        )
        if not keystrokes_complete:
            errors.append(
                f"{trial['trial_id']}: interactive keystrokes="
                f"{trial['completed_keystrokes']}/{trial['expected_keystrokes']}"
            )
        interactive = next(
            (row for row in measured_streams if row["stream_role"] == "interactive_0"),
            None,
        )
        if keystrokes_complete and (
            interactive is None or interactive["complete_outputs"] != "1"
        ):
            errors.append(
                f"{trial['trial_id']}: final 100-byte editor output not verified"
            )
        for row in measured_streams:
            if row["stream_role"] == "interactive_0":
                continue
            if row["stream_complete"] != "1":
                errors.append(
                    f"{trial['trial_id']}: {row['stream_role']} stream incomplete: "
                    f"{row['completed_units']}/{row['expected_units']} samples"
                )
        protocol = trial["protocol"]
        if protocol == "ssh":
            if int(trial["opened_transport_streams"]) != count:
                errors.append(f"{trial['trial_id']}: SSH channel count mismatch")
            if {row["transport_semantics"] for row in rows} != {"ssh_session_channel"}:
                errors.append(f"{trial['trial_id']}: SSH semantics mismatch")
        elif protocol == "ssh3":
            ids = [row["transport_stream_id"] for row in rows]
            conversations = {row["conversation_stream_id"] for row in rows}
            if "" in ids or len(set(ids)) != count:
                errors.append(f"{trial['trial_id']}: QUIC StreamIDs invalid: {ids}")
            if "" in conversations or len(conversations) != 1:
                errors.append(f"{trial['trial_id']}: SSH3 conversation mismatch")
            if int(trial["opened_transport_streams"]) != count:
                errors.append(f"{trial['trial_id']}: SSH3 stream count mismatch")
        else:
            errors.append(
                f"{trial['trial_id']}: giao thức {protocol} không được W4 đánh giá"
            )
    if errors:
        print("[FAIL] W4 multiplex audit", file=sys.stderr)
        for error in errors[:50]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"[OK] W4 multiplex audit passed for {len(trials)} trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
