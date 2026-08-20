#!/usr/bin/env python3
"""Lập lịch complete-block và chạy toàn bộ W3."""

from __future__ import annotations

import csv
import json
import random
import sys
import time
from pathlib import Path

from config import load_env, split_csv
from constants import (
    AUDIT_FIELDS, EDITORS, KEYSTROKE_FIELDS, ORDER_FIELDS, PROTOCOLS,
    PROBE_BYTES, PROBE_CHARACTERS, PROBE_LINES, PROBE_SHA256,
    SCENARIO_STREAMS, STREAM_FIELDS, TRIAL_FIELDS,
)
from probe import ProbeSource
from trial import run_trial


def write_csv(path: Path, fields: list[str], rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def append_rows(handle, writer, rows):
    writer.writerows(rows)
    handle.flush()


def build_schedule(protocols, editors, scenarios, trials, seed, run_id):
    schedule = []
    order = 0
    for block_id in range(1, trials + 1):
        combinations = [
            (protocol, editor, scenario)
            for protocol in protocols
            for editor in editors
            for scenario in scenarios
        ]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, editor, scenario in combinations:
            order += 1
            trial_id = f"{protocol}_{editor}_{scenario.lower()}_r{block_id:02d}"
            schedule.append({
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": trial_id,
                "trial_tag": f"o{order:04d}_{trial_id}",
                "protocol": protocol,
                "editor": editor,
                "scenario": scenario,
                "stream_count": SCENARIO_STREAMS[scenario],
            })
    return schedule


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg["SERVER_HOST"] == "CHANGE_ME":
        raise ValueError("phải đặt SERVER_HOST trong config.env")

    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    editors = split_csv(cfg.get("EDITORS", ",".join(EDITORS)))
    scenarios = split_csv(cfg.get("SCENARIOS", ",".join(SCENARIO_STREAMS)))
    unknown = {
        "protocols": sorted(set(protocols) - set(PROTOCOLS)),
        "editors": sorted(set(editors) - set(EDITORS)),
        "scenarios": sorted(set(scenarios) - set(SCENARIO_STREAMS)),
    }
    if any(unknown.values()):
        raise ValueError(f"giá trị ma trận không hợp lệ: {unknown}")
    if len(protocols) != len(set(protocols)) or len(editors) != len(set(editors)) or len(scenarios) != len(set(scenarios)):
        raise ValueError("ma trận không được chứa giá trị lặp")

    # W3 đo lợi thế local echo của Mosh, vì vậy không cho một biến môi trường
    # vô tình chuyển prediction sang adaptive/never hoặc bỏ hẳn tùy chọn.
    mosh_predict = cfg.get("MOSH_PREDICT", "always").strip().lower() or "always"
    if "mosh" in protocols and mosh_predict != "always":
        raise ValueError(
            "W3 yêu cầu MOSH_PREDICT=always; giá trị hiện tại là "
            f"{mosh_predict!r}"
        )
    cfg["MOSH_PREDICT"] = mosh_predict

    trial_count = int(cfg.get("TRIALS_PER_COMBINATION", "10"))
    if trial_count <= 0:
        raise ValueError("TRIALS_PER_COMBINATION phải dương")
    live_progress_every = int(cfg.get("LIVE_PROGRESS_EVERY", "1"))
    if live_progress_every <= 0:
        raise ValueError("LIVE_PROGRESS_EVERY phải dương")
    for key in (
        "WARMUP_SECONDS", "KEY_INTERVAL_SECONDS", "KEY_TIMEOUT_SECONDS",
        "STALL_THRESHOLD_SECONDS", "INTER_TRIAL_DELAY_SECONDS",
    ):
        if float(cfg.get(key, "0")) < 0:
            raise ValueError(f"{key} không được âm")
    if float(cfg.get("STALL_THRESHOLD_SECONDS", "1")) >= float(cfg.get("KEY_TIMEOUT_SECONDS", "2")):
        raise ValueError("STALL_THRESHOLD_SECONDS phải nhỏ hơn KEY_TIMEOUT_SECONDS")

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
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260817"))
    schedule = build_schedule(protocols, editors, scenarios, trial_count, seed, run_id)
    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)

    metadata = {
        "run_id": run_id,
        "created_ts": time.time(),
        "experiment": "W3 Multiplexed Interactive Editing",
        "source_specification": "Thiết kế thí nghiệm.pdf, sections 3/5/6/7",
        "protocols": protocols,
        "editors": editors,
        "scenarios": scenarios,
        "trials_per_combination": trial_count,
        "trial_total": len(schedule),
        "random_seed": seed,
        "ordering": "randomized_complete_blocks",
        "probe_text": probe.text,
        "probe_characters": len(probe.text),
        "probe_bytes": len(probe.data),
        "probe_sha256": probe.sha256,
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "5")),
        "key_interval_seconds": float(cfg.get("KEY_INTERVAL_SECONDS", "0.2")),
        "key_timeout_seconds": float(cfg.get("KEY_TIMEOUT_SECONDS", "2")),
        "stall_threshold_seconds": float(cfg.get("STALL_THRESHOLD_SECONDS", "1")),
        "live_progress": cfg.get("LIVE_PROGRESS", "1"),
        "live_progress_every": live_progress_every,
        "terminal": {"columns": columns, "rows": rows, "type": cfg.get("TERMINAL_TYPE")},
        "mosh_semantics": (
            "one terminal session; logical editors are independently selected "
            "tmux panes measured round-robin"
        ),
        "mosh_pane_switch_timing": "pane selection and repaint complete before send_ns",
        "mosh_pane_order": "rotating_round_robin_per_character",
        "mosh_pane_select_timeout_seconds": float(
            cfg.get("MOSH_PANE_SELECT_TIMEOUT_SECONDS", "2.0")
        ),
        "mosh_pane_select_retries": int(cfg.get("MOSH_PANE_SELECT_RETRIES", "3")),
        "mosh_pane_select_retry_delay_seconds": float(
            cfg.get("MOSH_PANE_SELECT_RETRY_DELAY_SECONDS", "0.05")
        ),
        "mosh_prediction": mosh_predict,
    }
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

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

    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
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
