#!/usr/bin/env python3
import csv
import sys
from pathlib import Path


EXPECTED_ROLES = {
    "c0_only": {"interactive"},
    "c0_bg4": {"interactive", "log", "output", "ping", "sysmon"},
    "c0_bg4_heavy": {"interactive", "log", "output_heavy", "ping", "sysmon"},
}


# Tách trường CSV dùng dấu cộng thành một tập giá trị.
def split_plus(value):
    return {item for item in (value or "").split("+") if item}


# Chuyển danh sách role:stream_id thành ánh xạ.
def parse_role_map(value):
    out = {}
    for item in split_plus(value):
        role, stream_id = item.rsplit(":", 1)
        out[role] = stream_id
    return out


# Xác minh mỗi phiên SSH3 dùng một connection và các stream riêng biệt.
def main():
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    stream_path = result_dir / "ssh3_stream_audit.csv"
    socket_path = result_dir / "ssh3_audit.csv"
    order_path = result_dir / "experiment_order.csv"
    failures = []

    with stream_path.open(newline="", encoding="utf-8") as f:
        stream_rows = list(csv.DictReader(f))
    with socket_path.open(newline="", encoding="utf-8") as f:
        socket_rows = list(csv.DictReader(f))
    with order_path.open(newline="", encoding="utf-8") as f:
        expected_trials = [row for row in csv.DictReader(f) if row["protocol"] == "ssh3"]

    summaries = {
        (row["trial_id"], row["target"], row["profile"]): row
        for row in socket_rows
        if row["protocol"] == "ssh3" and row["role"] == "trial_summary"
    }

    rows_by_trial = {}
    for row in stream_rows:
        if row["protocol"] == "ssh3":
            rows_by_trial.setdefault(row["trial_id"], []).append(row)

    checked = 0
    for expected_trial in expected_trials:
        trial_id = expected_trial["trial_id"]
        candidates = rows_by_trial.get(trial_id, [])
        if len(candidates) != 1:
            failures.append(f"{trial_id}: expected exactly one stream audit row, got {len(candidates)}")
            continue
        row = candidates[0]
        expected = EXPECTED_ROLES.get(expected_trial["profile"])
        if expected is None:
            continue
        roles = parse_role_map(row.get("stream_roles", ""))
        label = f"{row['trial_id']}/{row['target']}/{row['profile']}"
        if set(roles) != expected:
            failures.append(
                f"{label}: roles={sorted(roles)}, expected={sorted(expected)}"
            )
            continue
        if len(set(roles.values())) != len(expected):
            failures.append(f"{label}: stream IDs are not unique: {roles}")
            continue
        conversation_ids = split_plus(row.get("conversation_stream_ids", ""))
        if len(conversation_ids) != 1:
            failures.append(
                f"{label}: expected one conversation stream ID, got {sorted(conversation_ids)}"
            )
            continue
        expected_background = expected - {"interactive"}
        ready_roles = split_plus(row.get("ready_roles", ""))
        if ready_roles != expected_background:
            failures.append(
                f"{label}: ready roles={sorted(ready_roles)}, expected={sorted(expected_background)}"
            )
            continue
        byte_roles = parse_role_map(row.get("byte_roles", ""))
        if set(byte_roles) != expected_background or any(int(value) <= 0 for value in byte_roles.values()):
            failures.append(f"{label}: invalid byte counters: {byte_roles}")
            continue
        summary = summaries.get((row["trial_id"], row["target"], row["profile"]))
        if not summary or summary["multiplex_hint"] != "single_udp_socket_observed_for_all_launchers":
            hint = summary["multiplex_hint"] if summary else "missing_trial_summary"
            failures.append(f"{label}: UDP socket proof failed: {hint}")
            continue
        checked += 1

    expected_checks = len(expected_trials)
    if checked != expected_checks or checked == 0:
        failures.append(f"verified {checked}/{expected_checks} SSH3 target/profile trials")

    if failures:
        print("SSH3 multiplex verification FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(f"SSH3 multiplex verification PASSED: {checked} trials; one UDP socket, one conversation, unique stream per role")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
