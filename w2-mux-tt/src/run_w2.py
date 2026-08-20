#!/usr/bin/env python3
"""Điều phối toàn bộ ma trận thí nghiệm W2."""

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

from config import load_env, split_csv
from constants import (
    AUDIT_FIELDS, ORDER_FIELDS, PAYLOAD_BYTES, PAYLOAD_LINE_BYTES,
    PAYLOAD_LINES, PAYLOAD_NAMES, PAYLOAD_PREFIXES, PAYLOAD_SHA256,
    PROTOCOLS, SCENARIOS, STREAM_FIELDS, TRANSFER_FIELDS, TRIAL_FIELDS,
)
from stream_adapter import open_direct_w2_connection
from trial import run_trial


# Tạo lịch trial theo khối hoàn chỉnh ngẫu nhiên.
def build_schedule(protocols, scenarios, trial_count, seed, run_id):
    schedule = []
    order = 0
    for block_id in range(1, trial_count + 1):
        combinations = [
            (protocol, scenario)
            for protocol in protocols for scenario in scenarios
        ]
        random.Random(seed + block_id).shuffle(combinations)
        for protocol, scenario in combinations:
            order += 1
            trial_id = f"{protocol}_{scenario.lower()}_r{block_id:02d}"
            schedule.append({
                "run_id": run_id,
                "block_id": block_id,
                "trial_order": order,
                "trial_id": trial_id,
                "trial_tag": f"o{order:03d}_{trial_id}",
                "protocol": protocol,
                "scenario": scenario,
                "stream_count": SCENARIOS[scenario],
            })
    return schedule


# Ghi các dòng dữ liệu theo schema CSV cố định.
def write_csv(path: Path, fields, rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    if not cfg.get("SERVER_HOST") or cfg["SERVER_HOST"] == "CHANGE_ME":
        raise ValueError("phải đặt SERVER_HOST trong config.env")
    protocols = split_csv(cfg.get("PROTOCOLS", ",".join(PROTOCOLS)))
    scenarios = split_csv(cfg.get("SCENARIOS", ",".join(SCENARIOS)))
    unknown_protocols = sorted(set(protocols) - set(PROTOCOLS))
    unknown_scenarios = sorted(set(scenarios) - set(SCENARIOS))
    if unknown_protocols or unknown_scenarios:
        raise ValueError(
            f"giao thức lạ={unknown_protocols}, kịch bản lạ={unknown_scenarios}"
        )
    trials = int(cfg.get("TRIALS_PER_COMBINATION", "10"))
    samples_per_stream = int(cfg.get("SAMPLES_PER_STREAM_PER_TRIAL", "100"))
    cooldown = float(cfg.get("INTER_TRIAL_DELAY_SECONDS", "3"))
    if trials <= 0 or samples_per_stream <= 0 or cooldown < 0:
        raise ValueError(
            "số trial và mẫu phải dương, thời gian nghỉ không được âm"
        )

    payload_dir = Path(cfg.get("PAYLOAD_DIR", "payloads"))
    payloads = load_payloads(payload_dir)
    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    seed = int(cfg.get("RANDOM_SEED", "20260814"))
    schedule = build_schedule(protocols, scenarios, trials, seed, run_id)
    print(
        f"[PLAN] trials_per_combination={trials} "
        f"samples_per_stream_per_trial={samples_per_stream} "
        f"payload_bytes={PAYLOAD_BYTES} payload_lines={PAYLOAD_LINES} "
        f"total_trials={len(schedule)}",
        flush=True,
    )

    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    write_csv(result_dir / "experiment_order.csv", ORDER_FIELDS, schedule)
    metadata = {
        "run_id": run_id,
        "created_time_ns": time.time_ns(),
        "config_path": str(Path(cfg_path).resolve()),
        "protocols": protocols,
        "scenarios": {name: SCENARIOS[name] for name in scenarios},
        "trials_per_combination": trials,
        "samples_per_stream_per_trial": samples_per_stream,
        "payloads": payloads,
        "random_seed": seed,
        "ordering": "randomized_complete_blocks",
        "connection_scope": "one new connection per trial",
        "stream_open_rule": "all roles opened and READY before warm-up and barrier",
        "sample_start_rule": (
            "all roles synchronize before every sample; Mosh receives a unique "
            "post-clear marker before the per-sample barrier is released"
        ),
        "sample_identity_rule": (
            "every trial/role/sample replaces the first 29 payload bytes of every "
            "line with a unique role-specific token while preserving 102400 bytes"
        ),
        "setup_latency_definition": (
            "from immediately before connection open until transport audit "
            "and all physical shells respond to the readiness probe"
        ),
        "warmup_seconds": float(cfg.get("WARMUP_SECONDS", "5")),
        "execution_mode": (
            "direct per-sample sed output command in persistent Bash; "
            "no remote agent"
        ),
        "completion_latency_definition": (
            "client last observed payload byte time minus client direct-command send time"
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
        "mosh_clear_timeout_seconds": float(
            cfg.get("MOSH_CLEAR_TIMEOUT", "10")
        ),
        "congestion_logging": {
            "enabled": congestion_enabled,
            "directory": str(congestion_dir.resolve()),
            "interval_seconds": float(
                cfg.get("CONGESTION_SAMPLE_INTERVAL_SECONDS", "0.10")
            ),
            "ssh": "Linux ss -tinp TCP_INFO sampled by ControlMaster PID",
            "ssh3": (
                "quic-go tracer: RTT, cwnd, bytes in flight, packet loss, "
                "congestion state and PTO"
            ),
        },
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
                "canonical reconstruction from all unique exact payload lines; "
                "raw terminal-update bytes are retained only as diagnostics"
            ),
        },
        "mosh_limitation": (
            "Mosh transports terminal screen state rather than a lossless byte stream; "
            "S2/S4 are concurrent processes in one terminal session"
        ),
    }
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
        write_csv(result_dir / "transfers.csv", TRANSFER_FIELDS, all_transfers)
        write_csv(result_dir / "streams.csv", STREAM_FIELDS, all_streams)
        write_csv(result_dir / "trials.csv", TRIAL_FIELDS, all_trials)
        write_csv(result_dir / "stream_audit.csv", AUDIT_FIELDS, audits)
        if cooldown and trial_index + 1 < len(schedule):
            time.sleep(cooldown)

    print(f"Saved {len(schedule)} W2 trials to {result_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
