#!/usr/bin/env python3
"""Xác minh bằng chứng một connection và đúng semantics stream của W3."""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    trials = load(result_dir / "trials.csv")
    audits = load(result_dir / "stream_audit.csv")
    keystrokes = load(result_dir / "keystrokes.csv")
    grouped = defaultdict(list)
    for row in audits:
        grouped[row["trial_id"]].append(row)
    keys_grouped = defaultdict(list)
    for row in keystrokes:
        keys_grouped[row["trial_id"]].append(row)
    errors = []
    for trial in trials:
        trial_id = trial["trial_id"]
        protocol = trial["protocol"]
        count = int(trial["stream_count"])
        rows = grouped.get(trial_id, [])
        if len(rows) != count:
            errors.append(f"{trial_id}: audit roles={len(rows)} expected={count}")
            continue
        if trial["connection_valid"] != "1" or trial["socket_count"] != "1":
            errors.append(f"{trial_id}: connection/socket audit invalid")
        if protocol == "ssh":
            if int(trial["opened_transport_streams"]) != count:
                errors.append(f"{trial_id}: SSH channel count mismatch")
            if {row["transport_semantics"] for row in rows} != {"ssh_session_channel"}:
                errors.append(f"{trial_id}: wrong SSH semantics")
        elif protocol == "ssh3":
            ids = [row["transport_stream_id"] for row in rows]
            conversations = {row["conversation_stream_id"] for row in rows}
            if "" in ids or len(set(ids)) != count:
                errors.append(f"{trial_id}: QUIC StreamIDs missing/not unique: {ids}")
            if "" in conversations or len(conversations) != 1:
                errors.append(f"{trial_id}: conversation IDs invalid: {conversations}")
            if int(trial["opened_transport_streams"]) != count:
                errors.append(f"{trial_id}: SSH3 stream count mismatch")
        elif protocol == "mosh":
            # Mosh chỉ được đo ở kịch bản một editor, nên mọi trial phải là
            # đúng một terminal session với một editor process trong đó.
            if int(trial["opened_transport_streams"]) != 1 or count != 1:
                errors.append(
                    f"{trial_id}: Mosh phải có đúng một terminal và một editor"
                )
            if {row["transport_semantics"] for row in rows} != {
                "editor_process_in_terminal"
            }:
                errors.append(f"{trial_id}: sai semantics của Mosh")
            if {
                row["measurement_mode"] for row in keys_grouped[trial_id]
            } != {"local_prediction"}:
                errors.append(f"{trial_id}: sai measurement_mode của Mosh")
    if errors:
        print("[FAIL] multiplex audit", file=sys.stderr)
        for error in errors[:50]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"[OK] multiplex audit passed for {len(trials)} trials")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
