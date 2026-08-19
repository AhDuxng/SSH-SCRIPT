#!/usr/bin/env python3
"""Schedule randomized complete blocks and run W4."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(REPO_DIR))
# W4 deliberately reuses the battle-tested VT100 parser from W3.
sys.path.append(str(REPO_DIR / "w3-mux-tt" / "src"))

from config import load_env, split_csv  # noqa: E402
from constants import (  # noqa: E402
    AUDIT_FIELDS, BACKGROUND_FIELDS, EDITORS, KEYSTROKE_FIELDS, ORDER_FIELDS,
    PAYLOAD_BYTES, PAYLOAD_LINES, PAYLOAD_NAME, PAYLOAD_SHA256, PROBE_BYTES,
    PROBE_CHARACTERS, PROBE_LINES, PROBE_SHA256, PROTOCOLS, SCENARIOS,
    STREAM_FIELDS, TRIAL_FIELDS,
)
from probe import ProbeSource  # noqa: E402
from trial import roles_for, run_trial  # noqa: E402


def build_schedule(protocols, editors, scenarios, trial_count, seed, run_id):
    schedule, order = [], 0
    for block_id in range(1, trial_count + 1):
        combinations = [(p, e, s) for p in protocols for e in editors for s in scenarios]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, editor, scenario in combinations:
            order += 1
            trial_id = f"{protocol}_{editor}_{scenario.lower()}_r{block_id:02d}"
            schedule.append({
                "run_id": run_id, "block_id": block_id, "trial_order": order,
                "trial_id": trial_id, "trial_tag": f"o{order:04d}_{trial_id}",
                "protocol": protocol, "editor": editor, "scenario": scenario,
                "logical_workload_count": len(roles_for(scenario)),
            })
    return schedule


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_payload(payload_dir: Path):
    path = payload_dir / PAYLOAD_NAME
    raw = path.read_bytes()
    if len(raw) != PAYLOAD_BYTES or raw.count(b"\n") != PAYLOAD_LINES:
        raise ValueError(f"payload W4 sai kích thước hoặc số dòng: {path}")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PAYLOAD_SHA256:
        raise ValueError(f"payload W4 sai SHA-256: {digest}")
    return {"name": PAYLOAD_NAME, "bytes": len(raw), "lines": PAYLOAD_LINES, "sha256": digest}


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg["SERVER_HOST"] == "CHANGE_ME":
        raise ValueError("phải đặt SERVER_HOST trong config.env")
    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    editors = split_csv(cfg.get("EDITORS", ",".join(EDITORS)))
    scenarios = split_csv(cfg.get("SCENARIOS", ",".join(SCENARIOS)))
    unknown = (
        sorted(set(protocols) - set(PROTOCOLS)), sorted(set(editors) - set(EDITORS)),
        sorted(set(scenarios) - set(SCENARIOS)),
    )
    if any(unknown):
        raise ValueError(f"ma trận W4 có giá trị lạ: {unknown}")
    if "mosh" in protocols and cfg.get("MOSH_PREDICT", "always").strip() != "always":
        raise ValueError("W4 yêu cầu MOSH_PREDICT=always")
    trials = int(cfg.get("TRIALS_PER_COMBINATION", "10"))
    if trials <= 0:
        raise ValueError("TRIALS_PER_COMBINATION phải dương")
    if float(cfg.get("STALL_THRESHOLD_SECONDS", "1")) >= float(cfg.get("KEY_TIMEOUT_SECONDS", "2")):
        raise ValueError("STALL_THRESHOLD_SECONDS phải nhỏ hơn KEY_TIMEOUT_SECONDS")

    probe = ProbeSource(cfg.get("PROBE_TEXT_FILE", "payloads/probe_text.c"))
    if (
        len(probe.data) != PROBE_BYTES or len(probe.text) != PROBE_CHARACTERS
        or probe.text.count("\n") != PROBE_LINES or probe.sha256 != PROBE_SHA256
    ):
        raise ValueError("probe W4 phải giống probe W3: 100 byte/ký tự và SHA-256 cố định")
    payload = load_payload(Path(cfg.get("PAYLOAD_DIR", "payloads")))
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260819"))
    schedule = build_schedule(protocols, editors, scenarios, trials, seed, run_id)
    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    metadata = {
        "run_id": run_id,
        "created_time_ns": time.time_ns(),
        "experiment": "W4 Interactive under Background Workloads",
        "source_specification": "Thiết kế thí nghiệm.pdf, sections 4/5/6/7",
        "protocols": protocols, "editors": editors, "scenarios": scenarios,
        "trials_per_combination": trials, "trial_total": len(schedule),
        "random_seed": seed, "ordering": "randomized_complete_blocks",
        "probe": {"bytes": len(probe.data), "characters": len(probe.text), "sha256": probe.sha256},
        "payload": payload,
        "commands_per_cycle": 5,
        "background_rule": "repeat continuously until interactive_0 finishes",
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "5")),
        "key_interval_seconds": float(cfg.get("KEY_INTERVAL_SECONDS", "0.2")),
        "key_timeout_seconds": float(cfg.get("KEY_TIMEOUT_SECONDS", "2")),
        "stall_threshold_seconds": float(cfg.get("STALL_THRESHOLD_SECONDS", "1")),
        "connection_scope": "one new measured connection per trial",
        "ssh_semantics": "one TCP ControlMaster; one SSH session channel per logical workload",
        "ssh3_semantics": "one QUIC connection/conversation; one bidirectional stream per logical workload",
        "mosh_semantics": "one UDP terminal session; workloads run in distinct visible tmux panes",
        "mosh_background_output_limitation": (
            "Mosh synchronizes screen state and may skip intermediate 1 MiB output; "
            "completion marker and visible update bytes are recorded separately from output completeness"
        ),
    }
    (result_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    print(
        f"[PLAN] trials_per_combination={trials} total_trials={len(schedule)} "
        f"probe_chars={len(probe.text)} payload_bytes={payload['bytes']} "
        f"mosh_predict={cfg.get('MOSH_PREDICT', 'always')}", flush=True,
    )

    paths = {
        "keys": (result_dir / "keystrokes.csv", KEYSTROKE_FIELDS),
        "background": (result_dir / "background.csv", BACKGROUND_FIELDS),
        "streams": (result_dir / "streams.csv", STREAM_FIELDS),
        "trials": (result_dir / "trials.csv", TRIAL_FIELDS),
        "audit": (result_dir / "stream_audit.csv", AUDIT_FIELDS),
    }
    handles, writers = {}, {}
    for name, (path, fields) in paths.items():
        handles[name] = path.open("w", newline="", encoding="utf-8")
        writers[name] = csv.DictWriter(handles[name], fieldnames=fields)
        writers[name].writeheader()
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    try:
        for index, trial in enumerate(schedule, start=1):
            print(
                f"[RUN] {index:04d}/{len(schedule):04d} {trial['trial_id']} "
                f"workloads={trial['logical_workload_count']}", flush=True,
            )
            keys, background, streams, trial_row, audit = run_trial(cfg, trial, probe)
            for name, rows in (
                ("keys", keys), ("background", background), ("streams", streams),
                ("trials", [trial_row]), ("audit", audit),
            ):
                writers[name].writerows(rows)
                handles[name].flush()
            print(
                f"[DONE] {trial['trial_id']} status={trial_row['status']} "
                f"keys={trial_row['completed_keystrokes']}/{trial_row['expected_keystrokes']} "
                f"background={trial_row['background_completed_samples']}/{trial_row['background_samples']}",
                flush=True,
            )
            if cooldown and index < len(schedule):
                time.sleep(cooldown)
    finally:
        for handle in handles.values():
            handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
