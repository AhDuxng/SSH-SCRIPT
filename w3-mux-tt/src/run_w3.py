#!/usr/bin/env python3
"""Lập lịch complete-block và chạy toàn bộ W3."""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR.parent))

from harness import provenance  # noqa: E402
from harness.experiment import (  # noqa: E402
    Scenario, build_schedule, render_matrix,
)
from harness.results import write_rows  # noqa: E402
from harness.settings import build_plan, load_settings  # noqa: E402
from constants import (
    AUDIT_FIELDS, EDITORS, KEYSTROKE_FIELDS, ORDER_FIELDS,
    PROBE_BYTES, PROBE_CHARACTERS, PROBE_LINES, PROBE_SHA256,
    SCENARIO_STREAMS, STREAM_FIELDS, TRIAL_FIELDS, PROTOCOLS,
)
from probe import ProbeSource
from trial import run_trial


def append_rows(handle, writer, rows):
    writer.writerows(rows)
    handle.flush()


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    settings = load_settings(config_path)
    scenarios = {
        name: Scenario(name, stream_count)
        for name, stream_count in SCENARIO_STREAMS.items()
    }
    run_id = settings.text("RUN_ID") or time.strftime("%Y%m%dT%H%M%S")
    plan = build_plan(
        settings, scenarios, default_seed=20260817, run_id=run_id,
        supported_protocols=PROTOCOLS,
        editors={name: name for name in EDITORS},
    )
    cfg = settings.values

    # Local echo prediction thay đổi hẳn ý nghĩa của độ trễ phím quan sát được,
    # nên nó phải cố định cho mọi lần chạy có thể so sánh với nhau.
    mosh_predict = settings.text("MOSH_PREDICT", "always").lower() or "always"
    if "mosh" in plan.protocols and mosh_predict != "always":
        raise ValueError(
            "W3 yêu cầu MOSH_PREDICT=always; giá trị hiện tại là "
            f"{mosh_predict!r}"
        )
    cfg["MOSH_PREDICT"] = mosh_predict

    trial_count = plan.trials
    live_progress_every = settings.integer("LIVE_PROGRESS_EVERY", 1, minimum=1)
    for key in (
        "WARMUP_SECONDS", "KEY_INTERVAL_SECONDS", "KEY_TIMEOUT_SECONDS",
        "STALL_THRESHOLD_SECONDS", "INTER_TRIAL_DELAY_SECONDS",
    ):
        if float(cfg.get(key, "0")) < 0:
            raise ValueError(f"{key} không được âm")
    if float(cfg.get("STALL_THRESHOLD_SECONDS", "1")) >= float(cfg.get("KEY_TIMEOUT_SECONDS", "2")):
        raise ValueError("STALL_THRESHOLD_SECONDS phải nhỏ hơn KEY_TIMEOUT_SECONDS")
    cursor_ready_timeout = float(cfg.get("EDITOR_CURSOR_READY_TIMEOUT_SECONDS", "3.0"))
    cursor_stable_seconds = float(cfg.get("EDITOR_CURSOR_STABLE_SECONDS", "0.20"))
    cursor_refresh_retries = int(cfg.get("EDITOR_CURSOR_REFRESH_RETRIES", "1"))
    if cursor_ready_timeout <= 0 or not 0 < cursor_stable_seconds < cursor_ready_timeout:
        raise ValueError(
            "EDITOR_CURSOR_READY_TIMEOUT_SECONDS phải lớn hơn "
            "EDITOR_CURSOR_STABLE_SECONDS > 0"
        )
    if cursor_refresh_retries < 0:
        raise ValueError("EDITOR_CURSOR_REFRESH_RETRIES không được âm")

    columns = int(cfg.get("TERMINAL_COLUMNS", "160"))
    rows = int(cfg.get("TERMINAL_ROWS", "48"))
    if "mosh" in protocols and (columns < 120 or rows < 40):
        raise ValueError("Mosh I4 cần terminal tối thiểu 120x40 để hiển thị bốn pane")

    probe = ProbeSource(cfg.get("PROBE_TEXT_FILE", "payloads/probe_text.c"))
    if (
        len(probe.data) != PROBE_BYTES
        or len(probe.text) != PROBE_CHARACTERS
        or probe.text.count("\n") != PROBE_LINES
        or probe.sha256 != PROBE_SHA256
    ):
        raise ValueError(
            "probe W3 sai đặc tả: cần 100 ký tự/100 byte, 6 newline và "
            f"SHA-256 {PROBE_SHA256}"
        )
    result_dir = plan.result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(plan.matrix, plan.trials, plan.seed, run_id)
    write_rows(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)

    metadata = {
        "run_id": run_id,
        "created_ts": time.time(),
        "experiment": "W3 Multiplexed Interactive Editing",
        "source_specification": "Thiết kế thí nghiệm.pdf, sections 3/5/6/7",
        "protocols": list(plan.protocols),
        "editors": list(plan.editors),
        "scenarios": [item.name for item in plan.scenarios],
        "experiment_matrix": {
            protocol: sorted(set(plan.matrix.scenarios_for(protocol)))
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
        "trials_per_configuration": plan.trials,
        "trial_total": len(schedule),
        "random_seed": plan.seed,
        "ordering": "randomized_complete_blocks",
        "probe_text": probe.text,
        "probe_characters": len(probe.text),
        "probe_bytes": len(probe.data),
        "probe_sha256": probe.sha256,
        "warmup_seconds": plan.warmup_seconds,
        "key_interval_seconds": float(cfg.get("KEY_INTERVAL_SECONDS", "0.2")),
        "key_timeout_seconds": float(cfg.get("KEY_TIMEOUT_SECONDS", "2")),
        "stall_threshold_seconds": float(cfg.get("STALL_THRESHOLD_SECONDS", "1")),
        "editor_cursor_ready_timeout_seconds": cursor_ready_timeout,
        "editor_cursor_stable_seconds": cursor_stable_seconds,
        "editor_cursor_refresh_retries": cursor_refresh_retries,
        "live_progress": cfg.get("LIVE_PROGRESS", "1"),
        "live_progress_every": live_progress_every,
        "terminal": {"columns": columns, "rows": rows, "type": cfg.get("TERMINAL_TYPE")},
        "mosh_semantics": (
            "one terminal session with a single editor process; Mosh is only "
            "evaluated in the single-editor scenario"
        ),
        "mosh_prediction": mosh_predict,
    }
    # Bằng chứng về binary thực sự phục vụ lần chạy này: bộ kết quả tự
    # chứng minh nó được đo bằng thuật toán nào, không phải suy luận sau.
    metadata["transport_provenance"] = provenance.collect(
        cfg, plan.protocols, PROJECT_DIR
    )
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(render_matrix(plan.matrix, plan.scenarios, plan.trials), flush=True)
    print(
        f"[PLAN] trials_per_combination={trial_count} total_trials={len(schedule)} "
        f"probe_chars={len(probe.text)} terminal={columns}x{rows} "
        f"mosh_predict={mosh_predict}",
        flush=True,
    )
    paths = {
        "keys": result_dir / "keystrokes.csv",
        "streams": result_dir / "streams.csv",
        "trials": result_dir / "trials.csv",
        "audit": result_dir / "stream_audit.csv",
    }
    handles = {name: path.open("w", newline="", encoding="utf-8") for name, path in paths.items()}
    fields = {
        "keys": KEYSTROKE_FIELDS,
        "streams": STREAM_FIELDS,
        "trials": TRIAL_FIELDS,
        "audit": AUDIT_FIELDS,
    }
    writers = {name: csv.DictWriter(handles[name], fieldnames=fields[name]) for name in handles}
    for writer in writers.values():
        writer.writeheader()

    cooldown = plan.inter_trial_delay_seconds
    try:
        for index, trial in enumerate(schedule, start=1):
            print(
                f"[RUN] {index:04d}/{len(schedule):04d} {trial['trial_id']} "
                f"logical_streams={trial['stream_count']}", flush=True,
            )
            key_rows, stream_rows, trial_row, audit_rows = run_trial(cfg, trial, probe)
            append_rows(handles["keys"], writers["keys"], key_rows)
            append_rows(handles["streams"], writers["streams"], stream_rows)
            append_rows(handles["trials"], writers["trials"], [trial_row])
            append_rows(handles["audit"], writers["audit"], audit_rows)
            print(
                f"[DONE] {trial['trial_id']} status={trial_row['status']} "
                f"keys={trial_row['completed_keystrokes']}/{trial_row['expected_keystrokes']} "
                f"streams={trial_row['completed_streams']}/{trial['stream_count']}",
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
