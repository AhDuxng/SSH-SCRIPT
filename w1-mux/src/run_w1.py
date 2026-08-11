#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import random
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(REPO_DIR))

from config import load_env, split_csv
from constants import (
    AUDIT_FIELDS, COMMANDS, ORDER_FIELDS, PROTOCOLS, SAMPLE_FIELDS, SCENARIOS,
    STREAM_FIELDS, TRIAL_FIELDS,
)
from stream_adapter import open_w1_connection
from trial import run_trial


# Tạo lịch trial theo randomized complete blocks.
def build_schedule(protocols, scenarios, trial_count, seed, run_id):
    schedule = []
    order = 0
    for block_id in range(1, trial_count + 1):
        combinations = [(protocol, scenario) for protocol in protocols for scenario in scenarios]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, scenario in combinations:
            order += 1
            trial_id = f"{protocol}_{scenario.lower()}_r{block_id:02d}"
            schedule.append({
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": trial_id,
                "trial_tag": f"o{order:03d}_{trial_id}",
                "protocol": protocol,
                "scenario": scenario,
                "stream_count": SCENARIOS[scenario],
            })
    return schedule


# Ghi các dòng dữ liệu theo schema CSV cố định.
def write_csv(path: Path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# Đọc cấu hình, chạy toàn bộ trial và ghi kết quả.
def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg["SERVER_HOST"] == "CHANGE_ME":
        raise ValueError("Set SERVER_HOST in config.env")
    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    scenarios = split_csv(cfg.get("SCENARIOS", ",".join(SCENARIOS)))
    unknown_protocols = sorted(set(protocols) - set(PROTOCOLS))
    unknown_scenarios = sorted(set(scenarios) - set(SCENARIOS))
    if unknown_protocols or unknown_scenarios:
        raise ValueError(
            f"unsupported protocols={unknown_protocols}, scenarios={unknown_scenarios}"
        )
    trials = int(cfg.get("TRIALS_PER_COMBINATION", "10"))
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    if trials <= 0 or cooldown < 0:
        raise ValueError("trial count must be positive and cooldown non-negative")

    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260811"))
    schedule = build_schedule(protocols, scenarios, trials, seed, run_id)
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    metadata = {
        "run_id": run_id,
        "created_time_ns": time.time_ns(),
        "config_path": str(Path(cfg_path).resolve()),
        "protocols": protocols,
        "scenarios": {name: SCENARIOS[name] for name in scenarios},
        "commands": list(COMMANDS),
        "trials_per_combination": trials,
        "random_seed": seed,
        "ordering": "randomized_complete_blocks",
        "connection_scope": "one new connection per trial",
        "stream_open_rule": "all roles opened and READY before warm-up and barrier",
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "5")),
        "latency_definition": "client result receive time minus client command send time",
        "completion_definition": "a matching result frame was received before timeout",
        "output_completeness_definition": "received byte count and SHA-256 equal sender metadata",
        "mosh_limitation": "roles are processes in one terminal session, not transport streams",
    }
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    all_samples, all_streams, all_trials, audits = [], [], [], []
    for trial_index, trial in enumerate(schedule):
        print(
            f"[RUN] {trial['trial_order']:03d}/{len(schedule):03d} "
            f"trial={trial['trial_id']} streams={trial['stream_count']}", flush=True,
        )
        samples, streams, trial_row, audit = run_trial(
            cfg, trial, open_w1_connection
        )
        all_samples.extend(samples)
        all_streams.extend(streams)
        all_trials.append(trial_row)
        conversation_id = audit["conversation_ids"][0] if audit["conversation_ids"] else ""
        semantics = "process_in_terminal" if trial["protocol"] == "mosh" else "transport_stream"
        for role in [f"command_{index}" for index in range(trial["stream_count"])]:
            audits.append({
                **{key: trial[key] for key in ORDER_FIELDS},
                "stream_role": role,
                "transport_stream_id": audit["stream_ids"].get(role, ""),
                "conversation_stream_id": conversation_id,
                "connection_valid": int(audit["valid"]),
                "connection_pid": audit["connection_pid"] or "",
                "socket_count": audit["socket_count"],
                "transport_semantics": semantics,
                "note": audit["note"],
            })
        write_csv(result_dir / "samples.csv", SAMPLE_FIELDS, all_samples)
        write_csv(result_dir / "streams.csv", STREAM_FIELDS, all_streams)
        write_csv(result_dir / "trials.csv", TRIAL_FIELDS, all_trials)
        write_csv(result_dir / "stream_audit.csv", AUDIT_FIELDS, audits)
        if cooldown and trial_index + 1 < len(schedule):
            time.sleep(cooldown)
    print(f"Saved {len(schedule)} W1 trials to {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
