#!/usr/bin/env python3
import csv
import json
import random
import sys
import time
from pathlib import Path

from config import load_env, split_csv
from constants import ORDER_FIELDS, PROTOCOLS, SAMPLE_FIELDS, SETUP_FIELDS, WORKLOADS
from trial import run_trial
from workloads import workload_commands


# Tạo complete block và xáo trộn mọi tổ hợp protocol × workload trong từng block.
def build_schedule(protocols, workloads, commands, trial_count, seed, run_id, network_profile):
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
                "command": commands[workload],
            })
    return schedule


# Ghi một danh sách dictionary ra CSV theo schema cố định.
def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


# Đọc cấu hình, lập lịch và chạy tuần tự mọi trial W2.
def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg.get("SERVER_HOST") == "CHANGE_ME":
        raise ValueError("Set SERVER_HOST in config.env")

    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    workloads = split_csv(cfg.get("WORKLOADS", ",".join(WORKLOADS)))
    unknown_protocols = sorted(set(protocols) - set(PROTOCOLS))
    unknown_workloads = sorted(set(workloads) - set(WORKLOADS))
    if unknown_protocols:
        raise ValueError(f"unsupported protocols: {unknown_protocols}")
    if unknown_workloads:
        raise ValueError(f"unsupported workloads: {unknown_workloads}")

    trials = int(cfg.get("TRIALS_PER_COMBINATION", "5"))
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    max_lines = int(cfg.get("MAX_OUTPUT_LINES", "0"))
    if trials <= 0 or cooldown < 0 or max_lines < 0:
        raise ValueError("trial count must be positive; delays and limits must be non-negative")

    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    network_profile = cfg.get("NETWORK_PROFILE", "unspecified").strip() or "unspecified"
    seed = int(cfg.get("RANDOM_SEED", "20260724"))
    commands = workload_commands(cfg)
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(
        protocols, workloads, commands, trials, seed, run_id, network_profile
    )

    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    (result_dir / "metadata.json").write_text(json.dumps({
        "run_id": run_id,
        "created_ts": time.time(),
        "config_path": str(Path(cfg_path).resolve()),
        "network_profile": network_profile,
        "protocols": protocols,
        "workloads": workloads,
        "commands": {name: commands[name] for name in workloads},
        "ordering": "randomized_complete_blocks",
        "random_seed": seed,
        "trials_per_combination": trials,
        "trial_total": len(schedule),
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "2")),
        "sample_timeout_seconds": float(cfg.get("SAMPLE_TIMEOUT", "180")),
        "command_idle_timeout_seconds": float(cfg.get("COMMAND_IDLE_TIMEOUT", "20")),
        "max_output_lines": max_lines,
        "large_file_path": cfg.get("LARGE_FILE_PATH", "/tmp/w2_large_file.txt"),
        "large_file_size_bytes": int(cfg.get("LARGE_FILE_SIZE_BYTES", "16777216")),
        "session_setup_definition": "before client spawn to first shell prompt",
        "completion_gate": "unique marker with remote exit code received after full output",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    samples, setups = [], []
    for index, trial in enumerate(schedule):
        print(
            f"[RUN] order={trial['trial_order']:03d}/{len(schedule):03d} "
            f"block={trial['block_id']:02d}/{trials:02d} trial={trial['trial_id']}",
            flush=True,
        )
        sample, setup = run_trial(cfg, trial)
        samples.append(sample)
        setups.append(setup)
        write_csv(result_dir / "samples.csv", SAMPLE_FIELDS, samples)
        write_csv(result_dir / "setup_samples.csv", SETUP_FIELDS, setups)
        if cooldown > 0 and index + 1 < len(schedule):
            time.sleep(cooldown)

    print(f"Saved W2 raw results to {result_dir}")


if __name__ == "__main__":
    main()

