#!/usr/bin/env python3
import csv
import json
import random
import sys
import time
from pathlib import Path

from config import load_env, split_csv
from constants import (
    CLOCK_FIELDS, ORDER_FIELDS, PROTOCOLS, SAMPLE_FIELDS, SETUP_FIELDS,
    TRIAL_FIELDS, WORKLOADS,
)
from trial import run_trial


# Tạo randomized complete blocks cho mọi tổ hợp protocol × workload.
def build_schedule(protocols, workloads, trial_count, seed, run_id, network_profile):
    schedule = []
    order = 0
    for block_id in range(1, trial_count + 1):
        combinations = [(protocol, workload) for protocol in protocols for workload in workloads]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, workload in combinations:
            order += 1
            schedule.append({
                "run_id": run_id,
                "network_profile": network_profile,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": f"{protocol}_{workload}_r{block_id:02d}",
                "protocol": protocol,
                "workload": workload,
            })
    return schedule


# Ghi danh sách dictionary ra CSV theo schema cố định.
def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# Đọc cấu hình, lập lịch và chạy tuần tự mọi connection W2.
def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg.get("SERVER_HOST") == "CHANGE_ME":
        raise ValueError("Set SERVER_HOST in config.env")

    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    workloads = split_csv(cfg.get("WORKLOADS", ",".join(WORKLOADS)))
    if set(protocols) - set(PROTOCOLS):
        raise ValueError(f"unsupported protocols: {sorted(set(protocols) - set(PROTOCOLS))}")
    if set(workloads) - set(WORKLOADS):
        raise ValueError(f"unsupported workloads: {sorted(set(workloads) - set(WORKLOADS))}")

    trials = int(cfg.get("TRIALS_PER_COMBINATION", "10"))
    samples_per_trial = int(cfg.get("SAMPLES_PER_TRIAL", "100"))
    warmup_samples = int(cfg.get("WARMUP_SAMPLES", "10"))
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    if min(trials, samples_per_trial) <= 0 or min(warmup_samples, cooldown) < 0:
        raise ValueError("trial/sample counts must be positive and delays non-negative")

    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    network_profile = cfg.get("NETWORK_PROFILE", "unspecified").strip() or "unspecified"
    seed = int(cfg.get("RANDOM_SEED", "20260724"))
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(protocols, workloads, trials, seed, run_id, network_profile)

    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    (result_dir / "metadata.json").write_text(json.dumps({
        "run_id": run_id,
        "created_ts": time.time(),
        "config_path": str(Path(cfg_path).resolve()),
        "network_profile": network_profile,
        "protocols": protocols,
        "workloads": workloads,
        "ordering": "randomized_complete_blocks",
        "random_seed": seed,
        "trials_per_combination": trials,
        "samples_per_trial": samples_per_trial,
        "warmup_samples_per_trial": warmup_samples,
        "connection_total": len(schedule),
        "expected_recorded_samples": len(schedule) * samples_per_trial,
        "sample_definition": "corrected server event timestamp to client observation",
        "session_setup_definition": "before client spawn to first shell prompt",
        "clock_method": "midpoint round-trip probes; median offset",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    samples, setups, clocks, trial_audits = [], [], [], []
    for index, trial in enumerate(schedule):
        print(
            f"[RUN] order={trial['trial_order']:03d}/{len(schedule):03d} "
            f"block={trial['block_id']:02d}/{trials:02d} trial={trial['trial_id']} "
            f"samples={samples_per_trial}", flush=True,
        )
        trial_rows, setup, clock, trial_audit = run_trial(cfg, trial, samples_per_trial)
        samples.extend(trial_rows)
        setups.append(setup)
        clocks.append(clock)
        trial_audits.append(trial_audit)
        write_csv(result_dir / "samples.csv", SAMPLE_FIELDS, samples)
        write_csv(result_dir / "setup_samples.csv", SETUP_FIELDS, setups)
        write_csv(result_dir / "clock_offsets.csv", CLOCK_FIELDS, clocks)
        write_csv(result_dir / "trials.csv", TRIAL_FIELDS, trial_audits)
        if cooldown > 0 and index + 1 < len(schedule):
            time.sleep(cooldown)

    print(f"Saved {len(samples)} W2 event samples from {len(schedule)} connections to {result_dir}")


if __name__ == "__main__":
    main()
