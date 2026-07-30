import csv
import math
import statistics
import time
from collections import Counter
from pathlib import Path

import pexpect

from constants import DEFAULT_PROBE_TEXT_FILE, TRIAL_FIELDS
from probe import ProbeSource
from protocol_runner import ProtocolRunner
from terminal_io import drain_pending_output, probe_once_ms


# Trả về ý nghĩa của phép đo theo từng giao thức.
def measurement_mode(protocol):
    return "local_prediction" if protocol == "mosh" else "remote_terminal_echo"


# Tính percentile bằng nội suy tuyến tính.
def percentile(values, probability):
    ordered = sorted(values)
    if not ordered:
        return ""
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# Tạo một dòng CSV cho một ký tự đã đánh số.
def sample_row(trial, protocol, target, profile, item, status, latency_ms, stall, bgs, failures, note):
    return {
        "run_id": trial["run_id"],
        "block_id": trial["block_id"],
        "trial_order": trial["trial_order"],
        "trial_id": trial["trial_id"],
        "ts": time.time(),
        "protocol": protocol,
        "measurement_mode": measurement_mode(protocol),
        "target": target,
        "profile": profile,
        "sample_idx": item.char_index - 1,
        "char_index": item.char_index,
        "char_total": item.char_total,
        "source_offset": item.source_offset,
        "source_char_total": item.source_char_total,
        "source_line": item.line,
        "source_column": item.column,
        "token": "\\n" if item.character == "\n" else item.character,
        "status": status,
        "latency_ms": latency_ms,
        "stall": stall,
        "background_channels": "+".join(bgs),
        "channel_count": 1 if protocol == "mosh" else 1 + len(bgs),
        "channel_open_failures": failures,
        "note": note,
    }


# Ghi đủ 80 vị trí khi cả connection không thể đo.
def write_failed_payload(writer, trial, protocol, target, profile, bgs, source, status, note):
    for item in source.items():
        writer.writerow(
            sample_row(trial, protocol, target, profile, item, status, "", 0, bgs, len(bgs), note)
        )


# Tổng hợp kết quả của một connection độc lập.
def write_trial_summary(path, trial, protocol, target, profile, rows, expected, timeout_ms, started, note, connection_valid, channel_ready):
    successful = [float(row["latency_ms"]) for row in rows if row["status"] == "success"]
    incomplete_count = expected - len(successful)
    incomplete_rate = 100.0 * incomplete_count / expected if expected else 100.0
    publish_p95 = incomplete_rate <= 5.0
    timeout_count = sum(row["status"] == "timeout" for row in rows)
    effective = [float(row["latency_ms"]) if row["status"] == "success" else timeout_ms for row in rows]
    status_counts = Counter(row["status"] for row in rows)
    result = {
        "run_id": trial["run_id"],
        "block_id": trial["block_id"],
        "trial_order": trial["trial_order"],
        "trial_id": trial["trial_id"],
        "protocol": protocol,
        "measurement_mode": measurement_mode(protocol),
        "target": target,
        "profile": profile,
        "expected_chars": expected,
        "completed_chars": len(successful),
        "completion_rate_pct": f"{100.0 * len(successful) / expected:.3f}" if expected else "",
        "incomplete_count": incomplete_count,
        "incomplete_rate_pct": f"{incomplete_rate:.3f}",
        "median_ms": f"{statistics.median(successful):.3f}" if successful else "",
        "p95_ms": f"{percentile(successful, 0.95):.3f}" if successful and publish_p95 else "",
        "p95_publishable": int(publish_p95),
        "effective_mean_ms": f"{statistics.mean(effective):.3f}" if effective else "",
        "effective_p95_ms": f"{percentile(effective, 0.95):.3f}" if effective else "",
        "timeout_count": timeout_count,
        "status_counts": "+".join(f"{key}:{status_counts[key]}" for key in sorted(status_counts)),
        "stall_count": sum(int(row["stall"]) for row in rows),
        "connection_valid": int(bool(connection_valid)),
        "channel_ready": int(bool(channel_ready)),
        "started_ts": f"{started:.6f}",
        "ended_ts": f"{time.time():.6f}",
        "note": note,
    }
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        output = csv.DictWriter(handle, fieldnames=TRIAL_FIELDS)
        if not exists:
            output.writeheader()
        output.writerow(result)


# Chạy một connection độc lập và đo trọn vẹn file probe.
def run_trial(cfg, trial, protocol, target, profile, bgs, writer):
    interval = float(cfg.get("TOKEN_INTERVAL", "0.20"))
    timeout = float(cfg.get("TOKEN_TIMEOUT", "2.00"))
    timeout_ms = float(cfg.get("TIMEOUT_PENALTY_MS", str(timeout * 1000.0)))
    stall_ms = float(cfg.get("STALL_THRESHOLD_MS", "1000"))
    warmup = float(cfg.get("WARMUP_SECONDS", "5"))
    ready_timeout = float(cfg.get("CHANNEL_READY_TIMEOUT", "10"))
    live_progress = cfg.get("LIVE_PROGRESS", "1") == "1"
    source = ProbeSource(Path(cfg.get("PROBE_TEXT_FILE", DEFAULT_PROBE_TEXT_FILE)).read_text(encoding="utf-8"))
    trial_summary_path = Path(cfg.get("RESULT_DIR", "artifacts/results")) / "trials.csv"

    pr = ProtocolRunner(cfg, protocol, trial)
    background = []
    background_failures = 0
    child = None
    rows = []
    started = time.time()
    note = ""
    connection_valid = False
    channel_ready = False

    try:
        if protocol == "ssh":
            pr.start_master_if_needed(target, profile)
            background, background_failures = pr.start_background_channels(target, profile, bgs)
            pr.wait_background_ready(background, ready_timeout)
            child = pr.spawn_interactive(target, profile, bgs)
            pr.verify_ssh_multiplex(background, target, profile, child.pid)
            connection_valid = True
            channel_ready = True
        else:
            child = pr.spawn_interactive(target, profile, bgs)
            if protocol == "mosh":
                pr.wait_mosh_interactive_ready(child, ready_timeout)
                pr.start_mosh_background_inside_terminal(child, target, profile, bgs)
                pr.wait_mosh_background_ready(child, bgs, ready_timeout)
                channel_ready = True
            elif protocol == "ssh3":
                pr.wait_ssh3_background_ready(child, bgs, ready_timeout)
                channel_ready = True

        pr.prepare_target(child, target)

        if protocol == "ssh3":
            connection_valid = pr.write_connection_summary(target, profile, child.pid)
            if not connection_valid:
                raise RuntimeError("SSH3 connection audit did not observe exactly one UDP socket")
        elif protocol == "mosh":
            connection_valid = True

        time.sleep(warmup)
        drain_pending_output(child)

        for item in source.items():
            status = "unknown"
            latency_text = ""
            stall = 0
            item_note = ""
            try:
                latency = probe_once_ms(child, item.character, timeout, pr.tracker)
                latency_text = f"{latency:.3f}"
                stall = int(latency > stall_ms)
                status = "success"
            except pexpect.TIMEOUT as exc:
                status = "timeout"
                item_note = str(exc)
            except pexpect.EOF as exc:
                status = "eof"
                item_note = str(exc)
            except Exception as exc:
                status = "failure"
                item_note = repr(exc)

            row = sample_row(
                trial, protocol, target, profile, item, status, latency_text,
                stall, bgs, background_failures, item_note,
            )
            writer.writerow(row)
            rows.append(row)
            if live_progress:
                shown = latency_text or "-"
                print(
                    f"[LIVE] order={trial['trial_order']:03d} trial={trial['trial_id']} "
                    f"char={item.char_index:03d}/{item.char_total:03d} "
                    f"status={status:8s} latency_ms={shown}",
                    flush=True,
                )
            time.sleep(interval)

    except Exception as exc:
        note = repr(exc)
        if not rows:
            write_failed_payload(
                writer, trial, protocol, target, profile, bgs, source,
                "trial_unavailable", note,
            )
            rows = [
                sample_row(trial, protocol, target, profile, item, "trial_unavailable", "", 0, bgs, background_failures, note)
                for item in source.items()
            ]
        if live_progress:
            print(f"[LIVE] trial={trial['trial_id']} status=trial_unavailable note={note}", flush=True)
    finally:
        if child is not None:
            pr.cleanup_target(child, target)
        pr.stop_procs(background, target, profile)
        if child is not None:
            try:
                child.sendline(f"pkill -f {pr.trial_tag} || true")
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    try:
                        child.read_nonblocking(size=4096, timeout=0.05)
                    except pexpect.TIMEOUT:
                        continue
                    except pexpect.EOF:
                        break
                child.sendline("exit")
                time.sleep(0.4)
                child.close(force=True)
            except Exception:
                pass
        pr.write_stream_audit(target, profile)
        if pr.tracker is not None:
            pr.tracker.close()
        pr.stop_master_if_needed()
        write_trial_summary(
            trial_summary_path, trial, protocol, target, profile, rows,
            source.source_total, timeout_ms, started, note,
            connection_valid, channel_ready,
        )
