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
    independent_pane_trials = 0
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
            if int(trial["opened_transport_streams"]) != 1:
                errors.append(f"{trial_id}: Mosh must have one physical terminal")
            expected = (
                "editor_process_in_terminal" if count == 1
                else "tmux_pane_in_terminal"
            )
            if {row["transport_semantics"] for row in rows} != {expected}:
                errors.append(f"{trial_id}: wrong Mosh semantics, expected={expected}")
            if count > 1:
                independent_pane_trials += 1
                key_rows = keys_grouped[trial_id]
                if {
                    row["measurement_mode"] for row in key_rows
                } != {"local_prediction_selected_pane"}:
                    errors.append(
                        f"{trial_id}: Mosh pane measurement mode is not independent"
                    )
                if {
                    row["render_verification"] for row in key_rows
                } != {"tmux_selected_pane_vt100_cursor_cell"}:
                    errors.append(
                        f"{trial_id}: Mosh pane render verification is not independent"
                    )
                by_sample = defaultdict(list)
                for row in key_rows:
                    by_sample[row["sample_index"]].append(row)
                for sample_index, sample_rows in by_sample.items():
                    send_times = [row["send_ns"] for row in sample_rows]
                    if (
                        len(sample_rows) != count
                        or "" in send_times
                        or len(set(send_times)) != count
                    ):
                        errors.append(
                            f"{trial_id}: sample={sample_index} does not have "
                            f"{count} independent pane send timestamps"
                        )
                        break
    if errors:
        print("[FAIL] multiplex audit", file=sys.stderr)
        for error in errors[:50]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"[OK] multiplex audit passed for {len(trials)} trials")
    print(
        f"[OK] independent Mosh pane timing passed for "
        f"{independent_pane_trials} I2/I4 trials"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
