#!/usr/bin/env python3
"""Schedule randomized complete blocks and run W4."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(REPO_DIR))
# W4 deliberately reuses the battle-tested VT100 parser from W3.
sys.path.append(str(REPO_DIR / "w3-mux-tt" / "src"))

from harness import provenance  # noqa: E402
from harness.experiment import (  # noqa: E402
    Scenario, build_schedule, render_matrix,
)
from harness.results import write_rows  # noqa: E402
from harness.settings import build_plan, load_settings  # noqa: E402
from constants import (  # noqa: E402
    AUDIT_FIELDS, BACKGROUND_FIELDS, EDITORS, KEYSTROKE_FIELDS, ORDER_FIELDS,
    PAYLOAD_BYTES, PAYLOAD_LINES, PAYLOAD_NAME, PAYLOAD_SHA256, PROBE_BYTES,
    PROBE_CHARACTERS, PROBE_LINES, PROBE_SHA256, SCENARIOS,
    STREAM_FIELDS, TRIAL_FIELDS,
)
from probe import ProbeSource  # noqa: E402
from trial import roles_for, run_trial  # noqa: E402


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
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    settings = load_settings(config_path)
    # Kịch bản của W4 mô tả *loại tải nền*, không phải số stream được
    # multiplex. Nhiều vai trò ở đây là tình huống cần đo chứ không phải thứ
    # đang được đánh giá, nên mọi giao thức đều tham gia; giao thức không
    # multiplex đơn giản chạy chúng trong cùng một terminal.
    scenarios = {
        name: Scenario(name, len(roles_for(name)), measures_multiplexing=False)
        for name in SCENARIOS
    }
    run_id = settings.text("RUN_ID") or time.strftime("%Y%m%dT%H%M%S")
    plan = build_plan(
        settings, scenarios, default_seed=20260819, run_id=run_id,
        editors={name: name for name in EDITORS},
    )
    cfg = settings.values

    if "mosh" in plan.protocols and settings.text("MOSH_PREDICT", "always") != "always":
        raise ValueError("W4 yêu cầu MOSH_PREDICT=always")
    stall = settings.number("STALL_THRESHOLD_SECONDS", 1.0, minimum=0.0)
    key_timeout = settings.number("KEY_TIMEOUT_SECONDS", 2.0, minimum=0.0)
    if stall >= key_timeout:
        raise ValueError("STALL_THRESHOLD_SECONDS phải nhỏ hơn KEY_TIMEOUT_SECONDS")
    final_timeout = settings.number("FINAL_OUTPUT_TIMEOUT_SECONDS", 10.0)
    final_hold = settings.number("FINAL_OUTPUT_HOLD_SECONDS", 12.0)
    if final_timeout <= 0 or final_hold <= final_timeout:
        raise ValueError(
            "FINAL_OUTPUT_HOLD_SECONDS phải lớn hơn FINAL_OUTPUT_TIMEOUT_SECONDS > 0"
        )

    probe = ProbeSource(cfg.get("PROBE_TEXT_FILE", "payloads/probe_text.c"))
    if (
        len(probe.data) != PROBE_BYTES or len(probe.text) != PROBE_CHARACTERS
        or probe.text.count("\n") != PROBE_LINES or probe.sha256 != PROBE_SHA256
    ):
        raise ValueError("probe W4 phải giống probe W3: 100 byte/ký tự và SHA-256 cố định")
    payload = load_payload(settings.path("PAYLOAD_DIR", "payloads"))
    result_dir = plan.result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule(plan.matrix, plan.trials, plan.seed, run_id)
    write_rows(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    metadata = {
        "run_id": run_id,
        "created_time_ns": time.time_ns(),
        "experiment": "W4 Interactive under Background Workloads",
        "source_specification": "Thiết kế thí nghiệm.pdf, sections 4/5/6/7",
        "protocols": list(plan.protocols),
        "editors": list(plan.editors),
        "scenarios": [item.name for item in plan.scenarios],
        "experiment_matrix": {
            protocol: sorted(set(plan.matrix.scenarios_for(protocol)))
            for protocol in plan.matrix.protocols()
        },
        "trials_per_configuration": plan.trials, "trial_total": len(schedule),
        "random_seed": plan.seed, "ordering": "randomized_complete_blocks",
        "probe": {"bytes": len(probe.data), "characters": len(probe.text), "sha256": probe.sha256},
        "payload": payload,
        "commands_per_cycle": 5,
        "background_rule": "repeat continuously until interactive_0 finishes",
        "warmup_seconds": plan.warmup_seconds,
        "key_interval_seconds": float(cfg.get("KEY_INTERVAL_SECONDS", "0.2")),
        "key_timeout_seconds": key_timeout,
        "stall_threshold_seconds": stall,
        "final_output_timeout_seconds": final_timeout,
        "final_output_hold_seconds": final_hold,
        "connection_scope": "one new measured connection per trial",
        "ssh_semantics": "one TCP ControlMaster; one SSH session channel per logical workload",
        "ssh3_semantics": "one QUIC connection/conversation; one bidirectional stream per logical workload",
        "mosh_semantics": "one UDP terminal session; workloads run in distinct visible tmux panes",
        "mosh_background_output_limitation": (
            "Mosh synchronizes screen state and may skip intermediate 1 MiB output; "
            "completion marker and visible update bytes are recorded separately from output completeness"
        ),
    }
    # Bằng chứng về binary thực sự phục vụ lần chạy này: bộ kết quả tự
    # chứng minh nó được đo bằng thuật toán nào, không phải suy luận sau.
    metadata["transport_provenance"] = provenance.collect(
        cfg, plan.protocols, PROJECT_DIR
    )
    (result_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
    print(render_matrix(plan.matrix, plan.scenarios, plan.trials), flush=True)
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
    cooldown = plan.inter_trial_delay_seconds
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
