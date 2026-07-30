#!/usr/bin/env python3
import csv
import json
import random
import sys
import time
from pathlib import Path

try:
    import pexpect  # noqa: F401
except ImportError:
    print("Missing dependency: pexpect. Install requirements.txt", file=sys.stderr)
    sys.exit(1)

from config import load_env, split_csv
from constants import DEFAULT_PROBE_TEXT_FILE, DEFAULT_TARGETS, PROFILES, SAMPLE_FIELDS
from probe import ProbeSource
from trial import run_trial


# Tạo các block đầy đủ rồi random thứ tự trong từng block.
def build_schedule(protocols, targets, profiles, trial_count, seed, run_id):
    schedule = []
    order = 0
    for block_id in range(1, trial_count + 1):
        combinations = [
            (protocol, target, profile, bgs)
            for protocol in protocols
            for target in targets
            for profile in profiles
            for bgs in (PROFILES[profile],)
        ]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, target, profile, bgs in combinations:
            order += 1
            trial_id = f"{protocol}_{target}_{profile}_r{block_id:02d}"
            schedule.append({
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": trial_id,
                "trial_tag": f"o{order:03d}_{trial_id}",
                "protocol": protocol,
                "target": target,
                "profile": profile,
                "bgs": list(bgs),
            })
    return schedule


# Đọc config, ghi lịch chạy và lần lượt thực thi mỗi trial.
def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    out_path = result_dir / "samples.csv"
    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260724"))
    trial_count = int(cfg.get("TRIALS_PER_COMBINATION", "5"))

    generated = (
        "samples.csv", "trials.csv", "experiment_order.csv", "metadata.json",
        "ssh3_audit.csv", "ssh3_stream_audit.csv", "connection_audit.csv",
        "channel_counters.csv",
    )
    for name in generated:
        (result_dir / name).unlink(missing_ok=True)

    protocols = split_csv(cfg.get("PROTOCOLS", "ssh,ssh3,mosh"))
    targets = split_csv(cfg.get("TARGETS", DEFAULT_TARGETS))
    profiles = split_csv(cfg.get("PROFILE_NAMES", ",".join(PROFILES)))
    unknown_profiles = sorted(set(profiles) - set(PROFILES))
    if unknown_profiles:
        raise ValueError(f"Unknown profiles: {unknown_profiles}")
    schedule = build_schedule(protocols, targets, profiles, trial_count, seed, run_id)
    probe_path = Path(cfg.get("PROBE_TEXT_FILE", DEFAULT_PROBE_TEXT_FILE))
    probe_chars = ProbeSource(probe_path.read_text(encoding="utf-8")).source_total

    order_fields = [
        "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
        "protocol", "target", "profile", "background_channels",
    ]
    with (result_dir / "experiment_order.csv").open("w", newline="", encoding="utf-8") as handle:
        output = csv.DictWriter(handle, fieldnames=order_fields)
        output.writeheader()
        for trial in schedule:
            output.writerow({
                **{key: trial[key] for key in order_fields if key != "background_channels"},
                "background_channels": "+".join(trial["bgs"]),
            })

    metadata = {
        "run_id": run_id,
        "created_ts": time.time(),
        "random_seed": seed,
        "ordering": "randomized_complete_blocks",
        "trials_per_combination": trial_count,
        "trial_total": len(schedule),
        "protocols": protocols,
        "targets": targets,
        "profiles": profiles,
        "config_path": str(Path(cfg_path).resolve()),
        "probe_text_file": str(probe_path),
        "characters_per_trial": probe_chars,
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "5")),
        "normal_output_rate_bps": int(cfg.get("NORMAL_OUTPUT_RATE_BPS", "102400")),
        "heavy_output_rate_bps": int(cfg.get("HEAVY_OUTPUT_RATE_BPS", "1048576")),
        "timeout_penalty_ms": float(cfg.get("TIMEOUT_PENALTY_MS", "2000")),
        "mosh_predict": cfg.get("MOSH_PREDICT", "always"),
    }
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
        writer.writeheader()
        for trial in schedule:
            print(
                f"[RUN] order={trial['trial_order']:03d}/{len(schedule):03d} "
                f"block={trial['block_id']:02d}/{trial_count:02d} "
                f"trial={trial['trial_id']} channels={1 + len(trial['bgs'])}",
                flush=True,
            )
            run_trial(
                cfg,
                trial,
                trial["protocol"],
                trial["target"],
                trial["profile"],
                trial["bgs"],
                writer,
            )
            handle.flush()

    print(f"Saved {len(schedule)} independent trials to {out_path}")


if __name__ == "__main__":
    main()
