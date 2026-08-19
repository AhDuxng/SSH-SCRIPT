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


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    trials, audits = load(root / "trials.csv"), load(root / "stream_audit.csv")
    grouped = defaultdict(list)
    for row in audits:
        grouped[row["trial_id"]].append(row)
    errors = []
    for trial in trials:
        rows = grouped[trial["trial_id"]]
        count = int(trial["logical_workload_count"])
        if len(rows) != count:
            errors.append(f"{trial['trial_id']}: audit rows={len(rows)} expected={count}")
            continue
        if trial["connection_valid"] != "1" or trial["socket_count"] != "1":
            errors.append(f"{trial['trial_id']}: invalid connection/socket evidence")
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
            if int(trial["opened_transport_streams"]) != 1:
                errors.append(f"{trial['trial_id']}: Mosh must expose one terminal")
            if {row["transport_semantics"] for row in rows} != {"tmux_pane_in_terminal"}:
                errors.append(f"{trial['trial_id']}: Mosh pane semantics mismatch")
    if errors:
        print("[FAIL] W4 multiplex audit", file=sys.stderr)
        for error in errors[:50]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"[OK] W4 multiplex audit passed for {len(trials)} trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
