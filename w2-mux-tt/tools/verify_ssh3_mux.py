#!/usr/bin/env python3
import csv
import sys
from collections import defaultdict
from pathlib import Path


# Đọc toàn bộ dòng từ một tệp CSV.
def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Xác minh socket, conversation và StreamID của SSH3.
def verify(result_dir: Path):
    audits = [
        row for row in load(result_dir / "stream_audit.csv")
        if row["protocol"] == "ssh3"
    ]
    trials = {
        row["trial_id"]: row for row in load(result_dir / "trials.csv")
        if row["protocol"] == "ssh3"
    }
    grouped = defaultdict(list)
    for row in audits:
        grouped[row["trial_id"]].append(row)

    errors = []
    for trial_id, trial in sorted(trials.items()):
        rows = grouped.get(trial_id, [])
        expected = int(trial["stream_count"])
        stream_ids = [
            row["transport_stream_id"] for row in rows
            if row["transport_stream_id"]
        ]
        conversations = {
            row["conversation_stream_id"] for row in rows
            if row["conversation_stream_id"]
        }
        if len(rows) != expected:
            errors.append(f"{trial_id}: expected {expected} audit roles, got {len(rows)}")
        if len(stream_ids) != expected or len(set(stream_ids)) != expected:
            errors.append(f"{trial_id}: stream IDs missing or not unique: {stream_ids}")
        if len(conversations) != 1:
            errors.append(
                f"{trial_id}: expected one conversation ID, got {sorted(conversations)}"
            )
        if trial["connection_valid"] != "1" or trial["socket_count"] != "1":
            errors.append(
                f"{trial_id}: invalid connection audit "
                f"valid={trial['connection_valid']} udp_sockets={trial['socket_count']}"
            )
        if trial["ready_streams"] != str(expected):
            errors.append(f"{trial_id}: not all streams reached READY")

    if not trials:
        errors.append("no SSH3 trials found")
    if errors:
        raise SystemExit("SSH3 multiplex verification FAILED:\n- " + "\n- ".join(errors))
    print(
        f"SSH3 multiplex verification PASSED: {len(trials)} trials, "
        "one UDP socket and conversation with unique QUIC stream IDs per role"
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results"))
