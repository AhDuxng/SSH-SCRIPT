#!/usr/bin/env python3
"""Điều phối toàn bộ ma trận thí nghiệm W2."""

from __future__ import annotations

import hashlib
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
    AUDIT_FIELDS, ORDER_FIELDS, PAYLOAD_BYTES, PAYLOAD_LINE_BYTES,
    PAYLOAD_LINES, PAYLOAD_NAMES, PAYLOAD_PREFIXES, PAYLOAD_SHA256,
    SCENARIOS, STREAM_FIELDS, TRANSFER_FIELDS, TRIAL_FIELDS, PROTOCOLS,
)
from stream_adapter import open_direct_w2_connection
from trial import run_trial


# Đọc và kiểm tra manifest payload đã tạo.
def load_payloads(payload_dir: Path) -> list[dict]:
    manifest_path = payload_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"không có manifest payload: {manifest_path}")
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        document.get("payload_bytes") != PAYLOAD_BYTES
        or document.get("line_bytes") != PAYLOAD_LINE_BYTES
    ):
        raise ValueError("manifest không đúng kích thước payload/dòng W2")
    payloads = sorted(
        document.get("payloads", []), key=lambda item: item["stream_index"]
    )
    if len(payloads) != 4:
        raise ValueError("W2 cần đúng bốn payload trong manifest")
    for expected_index, payload in enumerate(payloads):
        if payload["stream_index"] != expected_index:
            raise ValueError("chỉ số payload không liên tục từ 0 đến 3")
        if payload["name"] != PAYLOAD_NAMES[expected_index]:
            raise ValueError(f"sai tên payload: {payload['name']}")
        if payload.get("line_prefix") != PAYLOAD_PREFIXES[expected_index]:
            raise ValueError(f"sai prefix payload: {payload['name']}")
        if payload["bytes"] != PAYLOAD_BYTES or payload["lines"] != PAYLOAD_LINES:
            raise ValueError(f"payload không đúng đặc tả PDF: {payload['name']}")
        path = payload_dir / payload["name"]
        if not path.exists():
            raise FileNotFoundError(path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != PAYLOAD_SHA256[expected_index] or digest != payload["sha256"]:
            raise ValueError(f"SHA-256 payload không đúng đặc tả: {path}")
    return payloads


# Đọc cấu hình, chạy trial và ghi toàn bộ kết quả.
def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    settings = load_settings(config_path)
    scenarios = {
        name: Scenario(name, stream_count)
        for name, stream_count in SCENARIOS.items()
    }
    run_id = settings.text("RUN_ID") or time.strftime("%Y%m%dT%H%M%S")
    plan = build_plan(
        settings, scenarios, default_seed=20260814, run_id=run_id,
        supported_protocols=PROTOCOLS,
    )
    cfg = settings.values

    samples_per_stream = settings.integer(
        "SAMPLES_PER_STREAM_PER_TRIAL", 100, minimum=1
    )
    payloads = load_payloads(settings.path("PAYLOAD_DIR", "payloads"))
    schedule = build_schedule(plan.matrix, plan.trials, plan.seed, run_id)

    print(render_matrix(plan.matrix, plan.scenarios, plan.trials), flush=True)
    print(
        f"[PLAN] trials_per_configuration={plan.trials} "
        f"samples_per_stream_per_trial={samples_per_stream} "
        f"payload_bytes={PAYLOAD_BYTES} payload_lines={PAYLOAD_LINES} "
        f"total_trials={len(schedule)}",
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
        "trials_per_configuration": plan.trials,
        "samples_per_stream_per_trial": samples_per_stream,
        "payloads": payloads,
        "random_seed": plan.seed,
        "ordering": "randomized_complete_blocks",
        "connection_scope": "one new connection per trial",
        "stream_open_rule": "all roles opened and READY before warm-up and barrier",
        "sample_start_rule": (
            "all roles synchronize on a barrier before every sample; each "
            "command clears its own terminal area before emitting the start marker"
        ),
        "mosh_terminal": {
            "columns": int(cfg.get("W2_MOSH_COLUMNS", "4096")),
            "rows": int(cfg.get("W2_MOSH_ROWS", "144")),
        },
        "sample_identity_rule": (
            "every trial/role/sample replaces the first 29 payload bytes of every "
            "line with a unique role-specific token while preserving 102400 bytes"
        ),
        "setup_latency_definition": (
            "from immediately before connection open until transport audit "
            "and all physical shells respond to the readiness probe"
        ),
        "warmup_seconds": plan.warmup_seconds,
        "execution_mode": (
            "direct per-sample sed output command in persistent Bash; "
            "no remote agent"
        ),
        "completion_latency_definition": (
            "client last observed payload byte time minus client direct-command send time"
        ),
        "command_visible_latency_definition": (
            "client completion-marker observation time minus direct-command send time; "
            "for Mosh this measures command-visible terminal response, not lossless output"
        ),
        "first_byte_latency_definition": (
            "client first observed payload byte time minus client direct-command send time"
        ),
        "transfer_completion_definition": (
            "completion marker and exit zero, with verified bytes, unique lines "
            "and canonical SHA-256 equal to the deterministic payload manifest; "
            "SSH/SSH3 additionally require an exact raw byte-stream capture"
        ),
        "mosh_continue_after_timeout": (
            cfg.get("MOSH_CONTINUE_AFTER_TIMEOUT", "1") == "1"
        ),
        "mosh_barrier_grace_seconds": float(
            cfg.get("MOSH_BARRIER_GRACE_SECONDS", "5")
        ),
        "mosh_screen_verification": (
            "Mosh is evaluated only in the single-workload scenario; "
            "deterministic payload rows are matched on the reconstructed "
            "viewport as they appear"
        ),
        "content_complete_definition": (
            "client observation time of the moment every expected payload line "
            "of the sample has been seen; measured identically for SSH, SSH3 "
            "and Mosh and therefore comparable across all three"
        ),
        "content_coverage_definition": (
            "unique exact payload lines observed divided by expected payload "
            "lines; duplicate and invalid terminal lines are excluded"
        ),
        "raw_byte_ratio_definition": (
            "received bytes divided by expected bytes; may exceed 100 when "
            "terminal updates redraw or duplicate content"
        ),
        "verified_byte_ratio_definition": (
            "bytes belonging to unique, exact deterministic payload lines divided "
            "by expected bytes; redraw duplicates and terminal control bytes excluded"
        ),
        "verification_modes": {
            "ssh": "lossless byte-stream capture must match byte count and SHA-256",
            "ssh3": "lossless byte-stream capture must match byte count and SHA-256",
            "mosh": (
                "canonical reconstruction from the stable ANSI terminal screen; "
                "raw terminal-update bytes are retained only as diagnostics"
            ),
        },
        "mosh_limitation": (
            "Mosh transports terminal screen state rather than a lossless byte stream; "
            "S2/S4 are concurrent processes in one terminal session"
        ),
    }
    # Bằng chứng về binary thực sự phục vụ lần chạy này: bộ kết quả tự
    # chứng minh nó được đo bằng thuật toán nào, không phải suy luận sau.
    metadata["transport_provenance"] = provenance.collect(
        cfg, plan.protocols, PROJECT_DIR
    )
    (result_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    all_transfers, all_streams, all_trials, audits = [], [], [], []
    for trial_index, trial in enumerate(schedule):
        print(
            f"[RUN] {trial['trial_order']:03d}/{len(schedule):03d} "
            f"trial={trial['trial_id']} streams={trial['stream_count']}",
            flush=True,
        )
        transfers, streams, trial_row, audit = run_trial(
            cfg, trial, payloads, open_direct_w2_connection
        )
        all_transfers.extend(transfers)
        all_streams.extend(streams)
        all_trials.append(trial_row)
        conversation_id = (
            audit["conversation_ids"][0] if audit["conversation_ids"] else ""
        )
        semantics = (
            "process_in_terminal"
            if trial["protocol"] == "mosh" else "transport_stream"
        )
        for role in [
            f"output_{index}" for index in range(trial["stream_count"])
        ]:
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
        write_rows(result_dir / "transfers.csv", TRANSFER_FIELDS, all_transfers)
        write_rows(result_dir / "streams.csv", STREAM_FIELDS, all_streams)
        write_rows(result_dir / "trials.csv", TRIAL_FIELDS, all_trials)
        write_rows(result_dir / "stream_audit.csv", AUDIT_FIELDS, audits)
        if plan.inter_trial_delay_seconds and trial_index + 1 < len(schedule):
            time.sleep(plan.inter_trial_delay_seconds)

    print(f"Saved {len(schedule)} W2 trials to {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
