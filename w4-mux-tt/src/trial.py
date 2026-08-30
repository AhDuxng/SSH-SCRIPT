"""Run one W4 trial on exactly one transport connection."""

from __future__ import annotations

import hashlib
import math
import re
import shlex
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass

from stream_mux import ConnectionAudit, StreamSpec, open_multiplex_connection
from stream_mux.connection.common import ssh_base

from background import BackgroundCoordinator, run_direct_background
from harness.settings import cfg_bool
from constants import COMMANDS, PAYLOAD_NAME
from terminal_io import InteractiveEndpoint


def fmt(value):
    return "" if value is None or value == "" else f"{float(value):.3f}"


def percentile(values, probability):
    if not values:
        return ""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def roles_for(scenario: str) -> list[str]:
    roles = ["interactive_0"]
    if scenario in {"W4-CMD", "W4-MIX"}:
        roles.append("command_0")
    if scenario in {"W4-OUTPUT", "W4-MIX"}:
        roles.append("output_0")
    return roles


def workload_type(role: str) -> str:
    return role.split("_", 1)[0]


def editor_command(cfg, editor: str, path: str, ready_marker="") -> str:
    prefix = f"printf '%s\\n' {shlex.quote(ready_marker)}; " if ready_marker else ""
    if editor == "vim":
        command = f"{shlex.quote(cfg.get('VIM_BIN', 'vim'))} -Nu NONE -n -i NONE -- {shlex.quote(path)}"
    else:
        command = f"{shlex.quote(cfg.get('NANO_BIN', 'nano'))} -w -- {shlex.quote(path)}"
    # Validation remains on the same measured transport stream. After the
    # editor exits, emit short hex chunks that fit even in the main Mosh pane.
    # Use POSIX/coreutils tools instead of xxd so a separate vim-common/xxd
    # package is not required on the server.
    final_hold = float(cfg.get("FINAL_OUTPUT_HOLD_SECONDS", "12"))
    final = (
        f"if [ -f {shlex.quote(path)} ]; then "
        # Put every final marker in a clean, stable viewport.  This matters for
        # Mosh, which may omit intermediate terminal states, and also prevents
        # an editor's alternate-screen cursor position from scrolling away the
        # first chunks before the client can reconstruct the file.
        "printf '\\033[2J\\033[H'; "
        f"od -An -v -tx1 {shlex.quote(path)} | tr -d '[:space:]' | fold -w 32 | "
        "awk '{printf \"__W4FINAL__:%06d:%s\\n\", NR, $0}'; "
        f"__w4_final_bytes=$(wc -c < {shlex.quote(path)} | tr -d '[:space:]'); "
        "printf '__W4FINAL_END__:%s\\n' \"$__w4_final_bytes\"; "
        f"else printf '__W4FINAL_END__:0\\n'; fi; sleep {final_hold:g}"
    )
    return f"rm -f {shlex.quote(path)}; {prefix}{command}; __w4_editor_rc=$?; {final}; exit \"$__w4_editor_rc\""


def remote_file(trial):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", trial["trial_tag"])
    return f"/tmp/w4_{safe}_{trial['editor']}.txt"


def direct_specs(cfg, trial, roles, path):
    rows = int(cfg.get("TERMINAL_ROWS", "48"))
    columns = int(cfg.get("TERMINAL_COLUMNS", "180"))
    terminal = cfg.get("TERMINAL_TYPE", "xterm-256color")
    marker = f"__W4_READY_{trial['trial_tag']}__"
    shell = cfg.get("DIRECT_SHELL_COMMAND", "/bin/bash --noprofile --norc")
    specs = []
    for role in roles:
        if role == "interactive_0":
            specs.append(StreamSpec(
                role, "/bin/bash -lc " + shlex.quote(editor_command(cfg, trial["editor"], path, marker)),
                allocate_pty=True, terminal_type=terminal, columns=columns, rows=rows,
            ))
        else:
            specs.append(StreamSpec(role, f"exec {shell}"))
    return specs, marker.encode()


def key_row(trial, stream, item, sent_ns, render_ns, status, note, cursor):
    completed = status in {"completed", "stall"}
    semantics = (
        "quic_bidirectional_stream" if trial["protocol"] == "ssh3"
        else "ssh_session_channel"
    )
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "logical_workload_count",
        )},
        "stream_role": "interactive_0",
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": semantics,
        "measurement_mode": "remote_terminal_render",
        "char_index": item.index, "char_total": item.total,
        "source_line": item.line, "source_column": item.column, "token": item.token,
        "send_ns": sent_ns or "", "render_ns": render_ns or "",
        "latency_ms": fmt((render_ns - sent_ns) / 1e6 if completed else ""),
        "status": status, "completed": int(completed), "stall": int(status == "stall"),
        "timeout": int(status == "timeout"),
        "cursor_row": cursor.row if cursor else "",
        "cursor_column": cursor.column if cursor else "",
        "render_verification": "vt100_cursor_cell",
        "note": note,
    }


def measure_interactive(cfg, trial, endpoint, probe):
    timeout = float(cfg.get("KEY_TIMEOUT_SECONDS", "2"))
    stall = float(cfg.get("STALL_THRESHOLD_SECONDS", "1"))
    interval = float(cfg.get("KEY_INTERVAL_SECONDS", "0.2"))
    rows = []
    for item in probe.items():
        if trial["protocol"] != "mosh":
            endpoint.wait_quiet()
        endpoint.screen.clear_history()
        before = endpoint.snapshot()
        sent_ns, render_ns, note = time.perf_counter_ns(), 0, ""
        try:
            endpoint.send(b"\r" if item.character == "\n" else item.character.encode())
            render_ns = endpoint.wait_render(before, item.character, sent_ns, timeout)
            status = "stall" if (render_ns - sent_ns) / 1e9 > stall else "completed"
        except TimeoutError as exc:
            status, note = "timeout", str(exc)
        except Exception as exc:
            status, note = "error", repr(exc)
        row = key_row(trial, endpoint.raw_stream, item, sent_ns, render_ns, status, note, before)
        rows.append(row)
        every = int(cfg.get("LIVE_PROGRESS_EVERY", "1"))
        if cfg.get("LIVE_PROGRESS", "1") == "1" and (
            item.index in {1, item.total} or item.index % every == 0 or status != "completed"
        ):
            print(
                f"[LIVE] trial={trial['trial_id']} stream=interactive_0 "
                f"char={item.index:03d}/{item.total:03d} token={item.token!r} "
                f"status={status} latency_ms={row['latency_ms'] or '-'}", flush=True,
            )
        time.sleep(interval)
    return rows


def save_editor(editor, endpoint):
    # The final-output capture must not be polluted or evicted by editor
    # repaint bytes accumulated during the measured workload.
    if hasattr(endpoint, "clear_recent"):
        endpoint.clear_recent()
    if editor == "vim":
        endpoint.send(b"\x1b")
        time.sleep(0.05)
        endpoint.send(b":wq\r")
    else:
        endpoint.send(b"\x0f")
        time.sleep(0.10)
        endpoint.send(b"\r")
        time.sleep(0.20)
        endpoint.send(b"\x18")


FINAL_CHUNK_RE = re.compile(
    r"__W4FINAL__:(\d{6}):([0-9a-f]{2,32})(?![0-9a-f])"
)
FINAL_END_RE = re.compile(r"__W4FINAL_END__:(\d+)")


def _reconstruct_final_output(texts):
    """Reconstruct one contiguous indexed hex payload from terminal text."""
    chunks = {}
    expected_bytes = None
    for text in texts:
        for match in FINAL_CHUNK_RE.finditer(text):
            index, value = int(match.group(1)), match.group(2)
            # Prefer a complete raw-stream chunk over a shorter partial marker
            # that may remain in the reconstructed screen during a redraw.
            if len(value) > len(chunks.get(index, "")):
                chunks[index] = value
        end_matches = list(FINAL_END_RE.finditer(text))
        if end_matches:
            expected_bytes = int(end_matches[-1].group(1))
    if expected_bytes is None:
        return None
    expected_chunks = (expected_bytes + 15) // 16
    if set(chunks) != set(range(1, expected_chunks + 1)):
        return None
    try:
        output = bytes.fromhex("".join(chunks[index] for index in range(1, expected_chunks + 1)))
    except ValueError:
        return None
    return output if len(output) == expected_bytes else None


def wait_final_output(endpoint, timeout=10.0):
    """Reconstruct the saved probe from short marker lines on the same stream."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with endpoint.screen.lock:
            screen_text = "\n".join("".join(row) for row in endpoint.screen.screen)
        recent_text = endpoint.recent_text() if hasattr(endpoint, "recent_text") else ""
        # Direct streams normally preserve marker lines in recent_text. Mosh
        # may interleave ANSI screen diffs, so also inspect reconstructed cells.
        recent_text = re.sub(
            r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))",
            "", recent_text,
        ).replace("\r", "\n")
        output = _reconstruct_final_output((recent_text, screen_text))
        if output is not None:
            return output
        if endpoint.terminal_error:
            break
        time.sleep(0.02)
    return None


def remote_cleanup(cfg, paths):
    commands = []
    existing = [path for path in paths if path]
    if existing:
        commands.append("rm -f " + " ".join(shlex.quote(path) for path in existing))
    target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
    try:
        subprocess.run([*ssh_base(cfg), target, "; ".join(commands)], timeout=8, check=False)
    except Exception:
        pass


# Trình soạn thảo kết thúc dòng cuối bằng newline theo quy ước tệp văn bản
# POSIX. Probe đã kết thúc bằng "\n" nên buffer có thêm một dòng trống và tệp
# lưu ra dài hơn đúng một byte. Đó là ngữ nghĩa của editor, không phải sai
# lệch của transport, nên vẫn tính là khớp.
def probe_saved_ok(file_bytes, expected: bytes) -> bool:
    return file_bytes == expected or file_bytes == expected + b"\n"


def summarize_interactive(trial, stream, rows, file_bytes, probe, note=""):
    completed = [row for row in rows if row["completed"] == 1]
    values = [float(row["latency_ms"]) for row in completed]
    stalls = sum(row["stall"] == 1 for row in rows)
    timeouts = sum(row["timeout"] == 1 for row in rows)
    output_complete = probe_saved_ok(file_bytes, probe.data)
    received_bytes = len(file_bytes) if file_bytes is not None else 0
    complete = len(completed) == len(rows) and output_complete
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "logical_workload_count",
        )},
        "stream_role": "interactive_0", "workload_type": "interactive",
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": rows[0]["transport_semantics"] if rows else "",
        "measurement_mode": "remote_terminal_render",
        "expected_units": len(rows), "attempted_units": len(rows),
        "completed_units": len(completed),
        "completion_rate_pct": fmt(100 * len(completed) / len(rows) if rows else 0),
        "stall_count": stalls, "stall_rate_pct": fmt(100 * stalls / len(rows) if rows else 0),
        "timeout_count": timeouts, "timeout_rate_pct": fmt(100 * timeouts / len(rows) if rows else 0),
        "complete_outputs": int(output_complete), "output_completeness_pct": fmt(100 if output_complete else 0),
        "expected_bytes": len(probe.data), "received_bytes": received_bytes,
        "mean_ms": fmt(statistics.mean(values) if values else ""),
        "median_ms": fmt(statistics.median(values) if values else ""),
        "p95_ms": fmt(percentile(values, .95)), "p99_ms": fmt(percentile(values, .99)),
        "stream_complete": int(complete), "status": "completed" if complete else "partial", "note": note,
    }


def summarize_background(trial, stream, role, rows, note=""):
    completed = [row for row in rows if row["status"] == "completed"]
    values = [float(row["completion_latency_ms"]) for row in completed if row["completion_latency_ms"]]
    timeouts = sum(row["timed_out"] == 1 for row in rows)
    complete_outputs = sum(row["output_complete"] == 1 for row in rows)
    expected_bytes = sum(int(row["expected_bytes"] or 0) for row in rows)
    received_bytes = sum(int(row["received_bytes"] or 0) for row in rows)
    complete = bool(rows) and len(completed) == len(rows) and not timeouts
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "logical_workload_count",
        )},
        "stream_role": role, "workload_type": workload_type(role),
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": "transport_stream",
        "measurement_mode": rows[0]["measurement_origin"] if rows else "",
        "expected_units": len(rows), "attempted_units": len(rows), "completed_units": len(completed),
        "completion_rate_pct": fmt(100 * len(completed) / len(rows) if rows else 0),
        "stall_count": 0, "stall_rate_pct": "0.000", "timeout_count": timeouts,
        "timeout_rate_pct": fmt(100 * timeouts / len(rows) if rows else 0),
        "complete_outputs": complete_outputs,
        "output_completeness_pct": fmt(100 * complete_outputs / len(rows) if rows else 0),
        "expected_bytes": expected_bytes, "received_bytes": received_bytes,
        "mean_ms": fmt(statistics.mean(values) if values else ""),
        "median_ms": fmt(statistics.median(values) if values else ""),
        "p95_ms": fmt(percentile(values, .95)), "p99_ms": fmt(percentile(values, .99)),
        "stream_complete": int(complete), "status": "completed" if complete else "partial", "note": note,
    }


def run_trial(cfg, trial, probe):
    roles = roles_for(trial["scenario"])
    background_roles = roles[1:]
    path = remote_file(trial)
    connection = None
    endpoint = None
    streams, coordinators = {}, {}
    key_rows, background_rows, stream_rows = [], [], []
    setup_ms = workload_ms = 0.0
    ready = 0
    note = ""
    audit = ConnectionAudit(trial["protocol"], False, 0, 0, 0, {}, [], {}, "not opened")
    bg_threads, bg_results = [], {}
    stop_event = threading.Event()
    file_bytes = None
    try:
        specs, ready_marker = direct_specs(cfg, trial, roles, path)
        started = time.perf_counter_ns()
        connection = open_multiplex_connection(cfg, trial["protocol"], specs, trial["trial_tag"])
        streams = connection.open(float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")))
        audit = connection.audit
        endpoint = InteractiveEndpoint(
            streams["interactive_0"],
            int(cfg.get("TERMINAL_ROWS", "48")),
            int(cfg.get("TERMINAL_COLUMNS", "180")),
        )
        endpoint.wait_marker(
            ready_marker, float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")),
        )
        for role in background_roles:
            coordinators[role] = BackgroundCoordinator(streams[role])
            coordinators[role].probe(
                float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")),
            )
        ready = len(roles)
        time.sleep(float(cfg.get("EDITOR_START_SETTLE_SECONDS", "1")))
        if trial["editor"] == "vim":
            endpoint.send(b"i")
            time.sleep(0.3)
        endpoint.wait_quiet()
        setup_ms = (time.perf_counter_ns() - started) / 1e6

        time.sleep(float(cfg.get("WARMUP_SECONDS", "5")))
        endpoint.send(b"\x1b\x0ci" if trial["editor"] == "vim" else b"\x0c")
        endpoint.wait_quiet()
        endpoint.screen.clear_history()

        workload_start = time.perf_counter_ns()
        barrier = threading.Barrier(len(background_roles) + 1)

        def launch(role):
            bg_results[role] = run_direct_background(
                cfg, trial, role, streams[role], coordinators[role],
                stop_event, barrier,
            )

        for role in background_roles:
            thread = threading.Thread(
                target=launch, args=(role,), name=f"w4-{role}", daemon=True,
            )
            thread.start()
            bg_threads.append(thread)
        barrier.wait(timeout=float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")))

        key_rows = measure_interactive(cfg, trial, endpoint, probe)
        stop_event.set()
        for thread in bg_threads:
            thread.join(
                timeout=float(cfg.get("BACKGROUND_STOP_TIMEOUT_SECONDS", "15")),
            )
        background_rows = [
            row for role in background_roles for row in bg_results.get(role, [])
        ]
        workload_ms = (time.perf_counter_ns() - workload_start) / 1e6
        save_editor(trial["editor"], endpoint)
        file_bytes = wait_final_output(
            endpoint, float(cfg.get("FINAL_OUTPUT_TIMEOUT_SECONDS", "10"))
        )
    except Exception as exc:
        note = repr(exc)
    finally:
        stop_event.set()
        for coordinator in coordinators.values():
            coordinator.close()
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                note = "; ".join(filter(None, (note, f"close={exc!r}")))
        if endpoint is not None:
            endpoint.thread.join(timeout=1)

    if not cfg_bool(cfg, "VERIFY_FINAL_OUTPUT", "1"):
        file_bytes = probe.data
    elif file_bytes is None:
        note = "; ".join(filter(None, (
            note,
            "final output markers were not reconstructed before timeout",
        )))
    elif not probe_saved_ok(file_bytes, probe.data):
        note = "; ".join(filter(None, (
            note,
            "final output mismatch: "
            f"bytes={len(file_bytes)}/{len(probe.data)} "
            f"sha256={hashlib.sha256(file_bytes).hexdigest()}",
        )))
    final_output_complete = probe_saved_ok(file_bytes, probe.data)
    logical_streams = {role: streams.get(role) for role in roles}
    if not key_rows:
        for item in probe.items():
            key_rows.append(key_row(trial, logical_streams.get("interactive_0"), item, 0, 0, "trial_unavailable", note, None))
    stream_rows.append(summarize_interactive(
        trial, logical_streams.get("interactive_0"), key_rows, file_bytes, probe, note
    ))
    for role in background_roles:
        role_rows = [row for row in background_rows if row["stream_role"] == role]
        stream_rows.append(summarize_background(trial, logical_streams.get(role), role, role_rows, note))

    audit_rows = []
    for role in roles:
        stream = logical_streams.get(role)
        semantics = (
            "quic_bidirectional_stream" if trial["protocol"] == "ssh3"
            else "ssh_session_channel"
        )
        audit_rows.append({
            **{key: trial[key] for key in (
                "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
                "protocol", "editor", "scenario", "logical_workload_count",
            )},
            "stream_role": role, "workload_type": workload_type(role),
            "connection_valid": int(audit.valid), "connection_pid": audit.connection_pid,
            "socket_count": audit.socket_count,
            "transport_stream_id": getattr(stream, "stream_id", ""),
            "conversation_stream_id": getattr(stream, "conversation_id", ""),
            "transport_semantics": semantics, "note": audit.note,
        })

    completed_keys = sum(row["completed"] == 1 for row in key_rows)
    stalls = sum(row["stall"] == 1 for row in key_rows)
    timeouts = sum(row["timeout"] == 1 for row in key_rows)
    bg_completed = sum(row["status"] == "completed" for row in background_rows)
    completed_streams = sum(row["stream_complete"] == 1 for row in stream_rows)
    unique_streams = len({value for value in audit.stream_ids.values() if value})
    trial_ok = (
        audit.valid and ready == len(roles) and completed_keys == len(key_rows)
        and completed_streams == len(stream_rows)
        and final_output_complete
    )
    trial_row = {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "logical_workload_count",
        )},
        "connection_valid": int(audit.valid), "connection_pid": audit.connection_pid,
        "socket_count": audit.socket_count, "opened_transport_streams": audit.stream_count,
        "unique_transport_streams": unique_streams,
        "conversation_count": len(audit.conversation_ids), "ready_workloads": ready,
        "expected_keystrokes": len(key_rows), "completed_keystrokes": completed_keys,
        "keystroke_completion_rate_pct": fmt(100 * completed_keys / len(key_rows)),
        "stall_count": stalls, "stall_rate_pct": fmt(100 * stalls / len(key_rows)),
        "timeout_count": timeouts, "timeout_rate_pct": fmt(100 * timeouts / len(key_rows)),
        "background_samples": len(background_rows), "background_completed_samples": bg_completed,
        "background_completion_rate_pct": fmt(100 * bg_completed / len(background_rows) if background_rows else 0),
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": fmt(100 * completed_streams / len(stream_rows)),
        "setup_ms": fmt(setup_ms), "workload_elapsed_ms": fmt(workload_ms),
        "status": "completed" if trial_ok else "partial",
        "note": "; ".join(filter(None, (audit.note, note))),
    }
    if cfg_bool(cfg, "CLEANUP_REMOTE_FILES", "1"):
        remote_cleanup(cfg, [path])
    return key_rows, background_rows, stream_rows, trial_row, audit_rows
