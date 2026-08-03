#!/usr/bin/env python3
import csv
import json
import random
import sys
import time
from pathlib import Path

from config import load_env, split_csv
from constants import (
    COMMANDS, LOOP_FIELDS, ORDER_FIELDS, PROTOCOLS, SAMPLE_FIELDS, SETUP_FIELDS,
)
from trial import run_trial


# Tạo complete block và xáo trộn thứ tự giao thức trong từng block.
def build_schedule(protocols, trial_count, seed, run_id):
    schedule = []
    order = 0
    for block_id in range(1, trial_count + 1):
        block = list(protocols)
        random.Random(seed + block_id).shuffle(block)
        for protocol in block:
            order += 1
            schedule.append({
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": f"{protocol}_r{block_id:02d}",
                "protocol": protocol,
            })
    return schedule


# Ghi một danh sách dictionary ra CSV theo schema cố định.
def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# Đọc cấu hình, lập lịch và chạy tuần tự mọi trial W1.
def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg.get("SERVER_HOST") == "CHANGE_ME":
        raise ValueError("Set SERVER_HOST in config.env")

    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    unknown = sorted(set(protocols) - set(PROTOCOLS))
    if unknown:
        raise ValueError(f"unsupported protocols: {unknown}")
    trials = int(cfg.get("TRIALS_PER_PROTOCOL", "5"))
    measured_loops = int(cfg.get("LOOPS_PER_TRIAL", "10"))
    warmup_loops = int(cfg.get("WARMUP_LOOPS", "1"))
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    if trials <= 0 or measured_loops <= 0 or warmup_loops < 0 or cooldown < 0:
        raise ValueError("trial/loop counts must be positive and delays must be non-negative")

    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260724"))
    total_loops = warmup_loops + measured_loops
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(protocols, trials, seed, run_id)

    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    (result_dir / "metadata.json").write_text(json.dumps({
        "run_id": run_id,
        "created_ts": time.time(),
        "config_path": str(Path(cfg_path).resolve()),
        "protocols": protocols,
        "commands": list(COMMANDS),
        "ordering": "randomized_complete_blocks",
        "random_seed": seed,
        "trials_per_protocol": trials,
        "measured_loops_per_trial": measured_loops,
        "warmup_loops_per_trial": warmup_loops,
        "session_setup_definition": "before client spawn to first shell prompt",
        "sequential_gate": "next command sent only after previous shell prompt received",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    all_samples, all_loops, all_setups = [], [], []
    for index, trial in enumerate(schedule):
        print(
            f"[RUN] order={trial['trial_order']:03d}/{len(schedule):03d} "
            f"block={trial['block_id']:02d}/{trials:02d} trial={trial['trial_id']}",
            flush=True,
        )
        samples, loops, setup = run_trial(cfg, trial, total_loops, warmup_loops)
        all_samples.extend(samples)
        all_loops.extend(loops)
        all_setups.append(setup)
        write_csv(result_dir / "samples.csv", SAMPLE_FIELDS, all_samples)
        write_csv(result_dir / "loops.csv", LOOP_FIELDS, all_loops)
        write_csv(result_dir / "setup_samples.csv", SETUP_FIELDS, all_setups)
        if cooldown > 0 and index + 1 < len(schedule):
            time.sleep(cooldown)

    print(f"Saved W1 raw results to {result_dir}")


if __name__ == "__main__":
    main()

