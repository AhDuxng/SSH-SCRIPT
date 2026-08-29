#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(REPO_DIR))

from harness import provenance
from harness.experiment import Scenario, build_schedule, render_matrix
from harness.results import write_rows
from harness.settings import build_plan, load_settings
from constants import (
    AUDIT_FIELDS, COMMANDS, ORDER_FIELDS, SAMPLE_FIELDS, SCENARIOS,
    STREAM_FIELDS, TRIAL_FIELDS, PROTOCOLS,
)
from stream_adapter import open_direct_w1_connection
from trial import run_trial


# Đọc cấu hình, chạy toàn bộ trial và ghi kết quả.
def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    settings = load_settings(config_path)
    scenarios = {
        name: Scenario(name, stream_count)
        for name, stream_count in SCENARIOS.items()
    }
    run_id = settings.text("RUN_ID") or time.strftime("%Y%m%dT%H%M%S")
    plan = build_plan(
        settings, scenarios, default_seed=20260811, run_id=run_id,
        supported_protocols=PROTOCOLS,
    )
    cfg = settings.values

    samples_per_stream_per_trial = settings.integer(
        "SAMPLES_PER_STREAM_PER_TRIAL", 100, minimum=1
    )
    if samples_per_stream_per_trial % len(COMMANDS) != 0:
        raise ValueError(
            "SAMPLES_PER_STREAM_PER_TRIAL phải là bội số dương của "
            f"{len(COMMANDS)}, nhận {samples_per_stream_per_trial}"
        )
    schedule = build_schedule(plan.matrix, plan.trials, plan.seed, run_id)

    print(render_matrix(plan.matrix, plan.scenarios, plan.trials), flush=True)
    print(
        f"[PLAN] trials_per_configuration={plan.trials} "
        f"samples_per_stream_per_trial={samples_per_stream_per_trial} "
        f"samples_per_stream_role={plan.trials * samples_per_stream_per_trial}",
        flush=True,
    )
    result_dir = plan.result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    write_rows(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    metadata = {
        "run_id": run_id,
        "created_time_ns": time.time_ns(),
        "config_path": str(settings.source),
        "protocols": list(plan.protocols),
        "scenarios": {item.name: item.stream_count for item in plan.scenarios},
        "experiment_matrix": {
            protocol: list(plan.matrix.scenarios_for(protocol))
            for protocol in plan.matrix.protocols()
        },
        "skipped_configurations": [
            {
                "protocol": item.protocol,
                "scenario": item.scenario.name,
                "reason": item.reason,
            }
            for item in plan.matrix.skipped
        ],
        "commands": list(COMMANDS),
        "trials_per_configuration": plan.trials,
        "samples_per_stream_per_trial": samples_per_stream_per_trial,
        "samples_per_stream_per_scenario": (
            plan.trials * samples_per_stream_per_trial
        ),
        "cycles_per_stream_per_trial": (
            samples_per_stream_per_trial // len(COMMANDS)
        ),
        "random_seed": plan.seed,
        "ordering": "randomized_complete_blocks",
        "connection_scope": "one new connection per trial",
        "stream_open_rule": "all roles opened and READY before warm-up and barrier",
        "setup_latency_definition": (
            "from immediately before connection open until transport audit "
            "and all workload roles are READY"
        ),
        "warmup_seconds": plan.warmup_seconds,
        "execution_mode": "direct command in a persistent remote Bash; no remote agent",
        "latency_definition": (
            "client completion-marker receive time minus client direct-command send time"
        ),
        "completion_definition": "the matching shell completion marker was received before timeout",
        "mosh_continue_after_timeout": (
            cfg.get("MOSH_CONTINUE_AFTER_TIMEOUT", "1") == "1"
        ),
        "output_completeness_definition": (
            "SSH/SSH3: output was delimited by ordered start/end markers; "
            "Mosh: not verifiable from concurrent screen-state updates"
        ),
        "mosh_limitation": (
            "roles are concurrent background jobs in one terminal session, "
            "not transport streams"
        ),
    }
    # Bằng chứng về binary thực sự phục vụ lần chạy này: bộ kết quả tự
    # chứng minh nó được đo bằng thuật toán nào, không phải suy luận sau.
    metadata["transport_provenance"] = provenance.collect(
        cfg, plan.protocols, PROJECT_DIR
    )
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
            cfg, trial, open_direct_w1_connection
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
        write_rows(result_dir / "samples.csv", SAMPLE_FIELDS, all_samples)
        write_rows(result_dir / "streams.csv", STREAM_FIELDS, all_streams)
        write_rows(result_dir / "trials.csv", TRIAL_FIELDS, all_trials)
        write_rows(result_dir / "stream_audit.csv", AUDIT_FIELDS, audits)
        if plan.inter_trial_delay_seconds and trial_index + 1 < len(schedule):
            time.sleep(plan.inter_trial_delay_seconds)
    print(
        f"Saved {len(schedule)} W1 trials to {result_dir}; "
        f"samples_per_stream_role={plan.trials * samples_per_stream_per_trial}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
