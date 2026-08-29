"""Thi hành một trial W3 trên đúng một connection."""

from __future__ import annotations

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

from harness.settings import cfg_bool
from interactive import InteractiveEndpoint
from probe import ProbeSource


LIVE_PRINT_LOCK = threading.Lock()
PANE_SELECT_KEYS = (
    ("F5", b"\x1b[15~"),
    ("F6", b"\x1b[17~"),
    ("F7", b"\x1b[18~"),
    ("F8", b"\x1b[19~"),
)


@dataclass(frozen=True)
class Pane:
    role: str
    index: int
    left: int
    top: int
    width: int
    height: int
    active: bool


def fmt(value) -> str:
    return "" if value == "" or value is None else f"{float(value):.3f}"


def percentile(values: list[float], probability: float):
    if not values:
        return ""
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def measurement_mode(protocol: str, stream_count: int = 1) -> str:
    if protocol == "mosh":
        return (
            "local_prediction_selected_pane"
            if stream_count > 1 else "local_prediction"
        )
    return "remote_terminal_render"


def transport_semantics(trial: dict) -> str:
    if trial["protocol"] == "mosh":
        return (
            "editor_process_in_terminal"
            if trial["stream_count"] == 1 else "tmux_pane_in_terminal"
        )
    return (
        "quic_bidirectional_stream"
        if trial["protocol"] == "ssh3" else "ssh_session_channel"
    )


def remote_file(trial_tag: str, role: str, editor: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in trial_tag)
    return f"/tmp/w3_{safe}_{role}_{editor}.txt"


def editor_command(cfg: dict, editor: str, path: str, ready_marker: str = "") -> str:
    prefix = f"printf '%s\\n' {shlex.quote(ready_marker)}; " if ready_marker else ""
    if editor == "vim":
        binary = shlex.quote(cfg.get("VIM_BIN", "vim"))
        command = f"{binary} -Nu NONE -n -i NONE -- {shlex.quote(path)}"
    elif editor == "nano":
        binary = shlex.quote(cfg.get("NANO_BIN", "nano"))
        command = f"{binary} -w -- {shlex.quote(path)}"
    else:
        raise ValueError(f"unsupported editor: {editor}")
    return f"rm -f {shlex.quote(path)}; {prefix}exec {command}"


def direct_specs(cfg: dict, trial: dict, roles: list[str]):
    rows = int(cfg.get("TERMINAL_ROWS", "48"))
    columns = int(cfg.get("TERMINAL_COLUMNS", "160"))
    terminal = cfg.get("TERMINAL_TYPE", "xterm-256color")
    specs, markers, files = [], {}, {}
    for role in roles:
        path = remote_file(trial["trial_tag"], role, trial["editor"])
        marker = f"__W3_READY_{trial['trial_tag']}_{role}__"
        files[role] = path
        markers[role] = marker.encode("ascii")
        specs.append(StreamSpec(
            role=role,
            remote_command=(
                "/bin/bash -lc "
                + shlex.quote(editor_command(cfg, trial["editor"], path, marker))
            ),
            allocate_pty=True,
            terminal_type=terminal,
            columns=columns,
            rows=rows,
        ))
    return specs, markers, files


def tmux_session_name(trial_tag: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in trial_tag)
    return f"w3_{safe}"[:80]


# Tạo tên socket tmux riêng để không thay đổi key binding của phiên người dùng.
def tmux_socket_name(session: str) -> str:
    return f"{session[:40]}_socket"


def mosh_spec(cfg: dict, trial: dict, roles: list[str]):
    session = tmux_session_name(trial["trial_tag"])
    socket = tmux_socket_name(session)
    tmux = (
        f"{shlex.quote(cfg.get('TMUX_BIN', 'tmux'))} "
        f"-L {shlex.quote(socket)} -f /dev/null"
    )
    columns = int(cfg.get("TERMINAL_COLUMNS", "160"))
    rows = int(cfg.get("TERMINAL_ROWS", "48"))
    files = {
        role: remote_file(trial["trial_tag"], role, trial["editor"])
        for role in roles
    }
    commands = [
        f"{tmux} kill-server 2>/dev/null || true",
        (
            f"{tmux} new-session -d -x {columns} -y {rows} "
            f"-s {shlex.quote(session)} "
            f"{shlex.quote(editor_command(cfg, trial['editor'], files[roles[0]]))}"
        ),
        f"{tmux} set-option -t {shlex.quote(session)} status off",
        f"{tmux} set-option -t {shlex.quote(session)} base-index 0",
        f"{tmux} set-window-option -t {shlex.quote(session)} pane-base-index 0",
    ]
    for role in roles[1:]:
        commands.append(
            f"{tmux} split-window -d -t {shlex.quote(session)} "
            f"{shlex.quote(editor_command(cfg, trial['editor'], files[role]))}"
        )
    for index, (key_name, _) in enumerate(PANE_SELECT_KEYS[:len(roles)]):
        target = shlex.quote(f"{session}:0.{index}")
        commands.append(
            f"{tmux} bind-key -n {key_name} select-pane -t {target}"
        )
    layout = "even-horizontal" if len(roles) == 2 else "tiled"
    layout_marker = f"__W3_LAYOUT_{session}__"
    layout_template = "#{pane_index}|#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}|#{pane_active}|#{pane_dead}|#{pane_current_command}"
    commands += [
        f"{tmux} select-layout -t {shlex.quote(session)} {layout}",
        f"{tmux} select-pane -t {shlex.quote(session + ':0.0')}",
        f"{tmux} set-window-option -t {shlex.quote(session)} synchronize-panes off",
        f"printf '%s\\n' {shlex.quote(layout_marker)}",
        (
            f"{tmux} list-panes -t {shlex.quote(session)} "
            f"-F {shlex.quote(layout_template)}"
        ),
        f"printf '%s\\n' {shlex.quote(layout_marker + '_END')}",
        "sleep 1",
        f"exec {tmux} attach-session -t {shlex.quote(session)}",
    ]
    remote_command = "; ".join(commands)
    spec = StreamSpec(
        role="terminal",
        remote_command=remote_command,
        allocate_pty=True,
        terminal_type=cfg.get("TERMINAL_TYPE", "xterm-256color"),
        columns=columns,
        rows=rows,
    )
    return [spec], files, session


def remote_check(cfg: dict, command: str, timeout: float = 10.0):
    target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
    return subprocess.run(
        [*ssh_base(cfg), target, command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def wait_tmux_layout(endpoint, cfg: dict, session: str, roles: list[str]) -> list[Pane]:
    timeout = float(cfg.get("MOSH_LAYOUT_QUERY_TIMEOUT_SECONDS", "10"))
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = endpoint.recent_text()
        panes = []
        clean = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", last)
        for line in clean.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 8 or not all(part.isdigit() for part in parts[:7]):
                continue
            index, left, top, width, height, active, dead = map(int, parts[:7])
            if not dead:
                panes.append((index, left, top, width, height, bool(active)))
        panes.sort()
        if len(panes) == len(roles):
            return [
                Pane(role, index, left, top, width, height, active)
                for role, (index, left, top, width, height, active)
                in zip(roles, panes)
            ]
        time.sleep(0.1)
    raise TimeoutError(f"tmux panes not ready in Mosh terminal: session={session} output={last[-1000:]!r}")


# Kiểm tra cursor đang thuộc vùng nội dung của pane được chọn.
def pane_contains_cursor(pane: Pane, snapshot) -> bool:
    return (
        pane.left <= snapshot.column < pane.left + pane.width
        and pane.top <= snapshot.row < pane.top + pane.height
    )


# Chọn một pane qua chính terminal Mosh trước khi bắt đầu đồng hồ đo.
def select_mosh_pane(endpoint, cfg: dict, session: str, pane: Pane):
    timeout = float(cfg.get("MOSH_PANE_SELECT_TIMEOUT_SECONDS", "2.0"))
    retries = int(cfg.get("MOSH_PANE_SELECT_RETRIES", "3"))
    retry_delay = float(cfg.get("MOSH_PANE_SELECT_RETRY_DELAY_SECONDS", "0.05"))
    if timeout <= 0:
        raise ValueError("MOSH_PANE_SELECT_TIMEOUT_SECONDS must be > 0")
    if retries < 0:
        raise ValueError("MOSH_PANE_SELECT_RETRIES must be >= 0")
    if retry_delay < 0:
        raise ValueError("MOSH_PANE_SELECT_RETRY_DELAY_SECONDS must be >= 0")

    quiet_timeout = min(timeout, 0.5)
    endpoint.wait_quiet(quiet_seconds=0.02, timeout=quiet_timeout)
    target = f"{session}:0.{pane.index}"
    try:
        _, select_key = PANE_SELECT_KEYS[pane.index]
    except IndexError as exc:
        raise ValueError(f"unsupported tmux pane index: {pane.index}") from exc

    snapshot = endpoint.snapshot()
    attempts = retries + 1
    last_snapshot = snapshot
    for attempt in range(1, attempts + 1):
        endpoint.send(select_key)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = endpoint.snapshot()
            last_snapshot = snapshot
            if pane_contains_cursor(pane, snapshot):
                endpoint.wait_quiet(quiet_seconds=0.02, timeout=quiet_timeout)
                confirmed = endpoint.snapshot()
                last_snapshot = confirmed
                if pane_contains_cursor(pane, confirmed):
                    return confirmed
            if endpoint.terminal_error:
                raise RuntimeError(endpoint.terminal_error)
            if endpoint.exited.is_set():
                raise EOFError("Mosh terminal exited while selecting tmux pane")
            time.sleep(0.002)

        # Phím F5-F8 đi qua chính Mosh input path nên có thể hiếm khi không được
        # tmux nhận dưới impairment. Gửi lại cùng phím là thao tác idempotent và
        # vẫn diễn ra hoàn toàn trước send_ns của ký tự workload.
        if attempt < attempts:
            with LIVE_PRINT_LOCK:
                print(
                    f"[PANE-RETRY] target={target} retry={attempt}/{retries} "
                    f"cursor=({last_snapshot.row},{last_snapshot.column})",
                    flush=True,
                )
            if retry_delay:
                time.sleep(retry_delay)

            # Có thể repaint đã tới trong khoảng nghỉ; xác nhận lại trước khi
            # phát một phím chọn pane nữa.
            snapshot = endpoint.snapshot()
            last_snapshot = snapshot
            if pane_contains_cursor(pane, snapshot):
                endpoint.wait_quiet(quiet_seconds=0.02, timeout=quiet_timeout)
                confirmed = endpoint.snapshot()
                last_snapshot = confirmed
                if pane_contains_cursor(pane, confirmed):
                    return confirmed

    raise TimeoutError(
        f"tmux pane selection not visible after {attempts} attempts: "
        f"target={target} cursor=({last_snapshot.row},{last_snapshot.column})"
    )


# Đưa riêng từng Vim pane vào insert mode qua terminal Mosh.
def initialize_mosh_panes(cfg, trial, endpoint, panes, session):
    for pane in panes:
        select_mosh_pane(endpoint, cfg, session, pane)
        if trial["editor"] == "vim":
            endpoint.send(b"i")
            endpoint.wait_quiet(quiet_seconds=0.02, timeout=0.5)
    select_mosh_pane(endpoint, cfg, session, panes[0])


# Repaint độc lập từng editor pane ngoài khoảng thời gian đo.
def refresh_mosh_panes(cfg, trial, endpoint, panes, session):
    for pane in panes:
        select_mosh_pane(endpoint, cfg, session, pane)
        refresh_editors(trial["editor"], [endpoint])
        endpoint.wait_quiet()
    select_mosh_pane(endpoint, cfg, session, panes[0])


def cleanup_remote(cfg: dict, files: dict[str, str], session: str = ""):
    commands = []
    if session:
        socket = tmux_socket_name(session)
        commands.append(
            f"{shlex.quote(cfg.get('TMUX_BIN', 'tmux'))} "
            f"-L {shlex.quote(socket)} kill-server 2>/dev/null || true"
        )
    if files:
        commands.append("rm -f " + " ".join(shlex.quote(path) for path in files.values()))
    if commands:
        try:
            remote_check(cfg, "; ".join(commands), timeout=5.0)
        except Exception:
            pass


def key_row(
    trial: dict, role: str, stream, item, sent_ns, render_ns,
    status: str, note: str, cursor, verification: str,
):
    completed = status in {"completed", "stall"}
    latency = (render_ns - sent_ns) / 1_000_000 if completed else ""
    semantics = transport_semantics(trial)
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "stream_count",
        )},
        "stream_role": role,
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": semantics,
        "measurement_mode": measurement_mode(
            trial["protocol"], int(trial["stream_count"]),
        ),
        "sample_index": item.index - 1,
        "char_index": item.index,
        "char_total": item.total,
        "source_offset": item.index,
        "source_char_total": item.total,
        "source_line": item.line,
        "source_column": item.column,
        "token": item.token,
        "send_ns": sent_ns or "",
        "render_ns": render_ns or "",
        "latency_ms": fmt(latency),
        "status": status,
        "completed": int(completed),
        "stall": int(status == "stall"),
        "timeout": int(status == "timeout"),
        "cursor_row": cursor.row if cursor else "",
        "cursor_column": cursor.column if cursor else "",
        "render_verification": verification,
        "note": note,
    }


def print_live_key(cfg: dict, row: dict):
    """In một dòng đo ngay khi mẫu hoàn tất, kể cả khi các thread chạy đồng thời."""
    if not cfg_bool(cfg, "LIVE_PROGRESS", "1"):
        return
    every = int(cfg.get("LIVE_PROGRESS_EVERY", "1"))
    index = int(row["char_index"])
    total = int(row["char_total"])

    if (
        every > 1
        and index not in {1, total}
        and index % every != 0
        and row["status"] == "completed"
    ):
        return
    latency = row["latency_ms"] or "-"
    cursor = (
        f"({row['cursor_row']},{row['cursor_column']})"
        if row["cursor_row"] != "" else "-"
    )
    with LIVE_PRINT_LOCK:
        print(
            f"[LIVE] order={int(row['trial_order']):04d} "
            f"trial={row['trial_id']} stream={row['stream_role']} "
            f"char={index:03d}/{total:03d} "
            f"source={row['source_line']}:{row['source_column']} "
            f"token={row['token']!r} status={row['status']} "
            f"latency_ms={latency} stall={row['stall']} cursor={cursor}",
            flush=True,
        )


def measure_direct(cfg, trial, roles, endpoints, probe):
    timeout = float(cfg.get("KEY_TIMEOUT_SECONDS", "2.0"))
    stall_s = float(cfg.get("STALL_THRESHOLD_SECONDS", "1.0"))
    interval = float(cfg.get("KEY_INTERVAL_SECONDS", "0.20"))
    barrier = threading.Barrier(len(roles))
    by_role = {role: [] for role in roles}

    def worker(role):
        endpoint = endpoints[role]
        stream = endpoint.raw_stream
        for item in probe.items():
            # Tương đương drain_pending_output() của w3_minimal: reader nền
            # phải xử lý xong repaint cũ trước khi chụp cursor cho phím mới.
            endpoint.wait_quiet(quiet_seconds=0.02, timeout=0.20)
            try:
                barrier.wait(timeout=timeout + 2.0)
            except threading.BrokenBarrierError as exc:
                before = endpoint.snapshot()
                row = key_row(
                    trial, role, stream, item, 0, 0, "barrier_failure",
                    str(exc), before, "vt100_cursor_cell",
                )
                by_role[role].append(row)
                print_live_key(cfg, row)
                continue
            endpoint.screen.clear_history()
            before = endpoint.snapshot()
            try:
                # Barrier pha hai giữ khoảng snapshot → send ngắn và cân bằng
                # cho mọi stream, không để stream nhanh chờ stream đang drain.
                barrier.wait(timeout=timeout + 2.0)
            except threading.BrokenBarrierError as exc:
                row = key_row(
                    trial, role, stream, item, 0, 0, "barrier_failure",
                    str(exc), before, "vt100_cursor_cell",
                )
                by_role[role].append(row)
                print_live_key(cfg, row)
                continue
            sent_ns = time.perf_counter_ns()
            render_ns = 0
            status, note = "error", ""
            try:
                endpoint.send(b"\r" if item.character == "\n" else item.character.encode("utf-8"))
                render_ns = endpoint.wait_render(before, item.character, sent_ns, timeout)
                latency_s = (render_ns - sent_ns) / 1_000_000_000
                status = "stall" if latency_s > stall_s else "completed"
            except TimeoutError as exc:
                status, note = "timeout", str(exc)
            except EOFError as exc:
                status, note = "eof", str(exc)
            except Exception as exc:
                status, note = "error", repr(exc)
            row = key_row(
                trial, role, stream, item, sent_ns, render_ns, status, note,
                before, "vt100_cursor_cell",
            )
            by_role[role].append(row)
            print_live_key(cfg, row)
            time.sleep(interval)

    threads = [threading.Thread(target=worker, args=(role,), daemon=True) for role in roles]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [row for role in roles for row in by_role[role]]


# Đo round-robin từng pane đã chọn, mỗi pane có send/render timestamp riêng.
def measure_mosh(cfg, trial, endpoint, panes, session, probe):
    timeout = float(cfg.get("KEY_TIMEOUT_SECONDS", "2.0"))
    stall_s = float(cfg.get("STALL_THRESHOLD_SECONDS", "1.0"))
    interval = float(cfg.get("KEY_INTERVAL_SECONDS", "0.20"))
    output = []
    for item in probe.items():
        # Xoay pane bắt đầu theo từng ký tự để pane 0 không luôn được hưởng
        # khoảng nghỉ còn pane cuối luôn phải theo sau các repaint khác.
        offset = (item.index - 1) % len(panes)
        pane_order = panes[offset:] + panes[:offset]
        for pane in pane_order:
            before = None
            sent_ns = 0
            render_ns = 0
            status, note = "error", ""
            try:
                # Chuyển pane và chờ repaint hoàn tất trước t_send, nên chi phí
                # điều hướng tmux không bị tính vào keystroke latency.
                select_mosh_pane(endpoint, cfg, session, pane)
                endpoint.screen.clear_history()
                before = endpoint.snapshot()
                sent_ns = time.perf_counter_ns()
                endpoint.send(
                    b"\r" if item.character == "\n"
                    else item.character.encode("utf-8")
                )
                render_ns = endpoint.wait_render(
                    before, item.character, sent_ns, timeout
                )
                latency_s = (render_ns - sent_ns) / 1_000_000_000
                status = "stall" if latency_s > stall_s else "completed"
            except TimeoutError as exc:
                status, note = "timeout", str(exc)
            except EOFError as exc:
                status, note = "eof", str(exc)
            except Exception as exc:
                status, note = "error", repr(exc)
            result = key_row(
                trial, pane.role, endpoint.raw_stream, item, sent_ns, render_ns,
                status, note, before, "tmux_selected_pane_vt100_cursor_cell",
            )
            output.append(result)
            print_live_key(cfg, result)
        time.sleep(interval)
    return output


def refresh_editors(editor: str, endpoints: list[InteractiveEndpoint], shared=False):
    """Đưa editor về đầu buffer và repaint ngoài khoảng thời gian đo."""
    targets = endpoints[:1] if shared else endpoints
    for endpoint in targets:
        if editor == "vim":
            # Normal mode -> first line/column -> redraw -> insert mode.
            endpoint.send(b"\x1bgg0\x0ci")
        else:
            # Nano uses row 0 for its title; Ctrl-A puts the edit cursor at
            # column zero after Ctrl-L has requested a complete repaint.
            endpoint.send(b"\x0c\x01")


def editor_origin(editor: str) -> tuple[int, int]:
    """Return the expected empty-buffer cursor cell in a direct terminal."""
    return (0, 0) if editor == "vim" else (1, 0)


def _wait_direct_editor_origins(cfg, editor, roles, endpoints):
    """Require every direct editor cursor and screen state to be stable."""
    timeout = float(cfg.get("EDITOR_CURSOR_READY_TIMEOUT_SECONDS", "3.0"))
    stable_seconds = float(cfg.get("EDITOR_CURSOR_STABLE_SECONDS", "0.20"))
    expected = editor_origin(editor)
    deadline = time.monotonic() + timeout
    signatures = {}
    stable_since = {}
    snapshots = {}

    while time.monotonic() < deadline:
        now = time.monotonic()
        for role in roles:
            endpoint = endpoints[role]
            if endpoint.terminal_error:
                raise RuntimeError(f"{role}: {endpoint.terminal_error}")
            if endpoint.exited.is_set():
                raise EOFError(f"{role}: editor exited while stabilizing cursor")
            snapshot = endpoint.snapshot()
            snapshots[role] = snapshot
            signature = (
                snapshot.row, snapshot.column,
                snapshot.write_seq, snapshot.event_seq,
            )
            if (snapshot.row, snapshot.column) != expected:
                signatures.pop(role, None)
                stable_since.pop(role, None)
            elif signatures.get(role) != signature:
                signatures[role] = signature
                stable_since[role] = now

        if len(stable_since) == len(roles) and all(
            now - stable_since[role] >= stable_seconds for role in roles
        ):
            return snapshots
        time.sleep(0.01)

    positions = {
        role: (snapshot.row, snapshot.column)
        for role, snapshot in snapshots.items()
    }
    raise TimeoutError(
        f"direct editor cursor did not stabilize at {expected}: {positions}"
    )


def synchronize_direct_editors(cfg, editor, roles, endpoints):
    """Repaint and verify direct editors before any measured key is sent."""
    retries = int(cfg.get("EDITOR_CURSOR_REFRESH_RETRIES", "1"))
    last_error = None
    for attempt in range(retries + 1):
        refresh_editors(editor, [endpoints[role] for role in roles])
        try:
            return _wait_direct_editor_origins(
                cfg, editor, roles, endpoints,
            )
        except TimeoutError as exc:
            last_error = exc
            if attempt < retries and cfg_bool(cfg, "LIVE_PROGRESS", "1"):
                with LIVE_PRINT_LOCK:
                    print(
                        f"[CURSOR-RETRY] editor={editor} "
                        f"retry={attempt + 1}/{retries} reason={exc}",
                        flush=True,
                    )
    raise last_error


def discard_editors(editor: str, endpoints: list[InteractiveEndpoint], shared=False):
    """Thoát không lưu ngoài khoảng đo, tương đương cleanup của w3_minimal."""
    targets = endpoints[:1] if shared else endpoints
    for endpoint in targets:
        try:
            endpoint.send(b"\x03:qa!\r" if editor == "vim" else b"\x18")
        except Exception:
            pass
    if editor == "nano":
        time.sleep(0.2)
        for endpoint in targets:
            try:
                endpoint.send(b"n")
            except Exception:
                pass
    time.sleep(0.4)


def summarize_stream(trial, role, stream, rows, note=""):
    completed = [row for row in rows if row["completed"] == 1]
    latencies = [float(row["latency_ms"]) for row in completed]
    expected = len(rows)
    stalls = sum(row["stall"] == 1 for row in rows)
    timeouts = sum(row["timeout"] == 1 for row in rows)
    stream_complete = len(completed) == expected
    semantics = rows[0]["transport_semantics"] if rows else ""
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "stream_count",
        )},
        "stream_role": role,
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": semantics,
        "measurement_mode": measurement_mode(
            trial["protocol"], int(trial["stream_count"]),
        ),
        "expected_keystrokes": expected,
        "completed_keystrokes": len(completed),
        "keystroke_completion_rate_pct": fmt(100 * len(completed) / expected if expected else 0),
        "stall_count": stalls,
        "stall_rate_pct": fmt(100 * stalls / expected if expected else 0),
        "timeout_count": timeouts,
        "timeout_rate_pct": fmt(100 * timeouts / expected if expected else 0),
        "mean_ms": fmt(statistics.mean(latencies) if latencies else ""),
        "median_ms": fmt(statistics.median(latencies) if latencies else ""),
        "p95_ms": fmt(percentile(latencies, 0.95)),
        "p99_ms": fmt(percentile(latencies, 0.99)),
        "stream_complete": int(stream_complete),
        "status": "completed" if stream_complete else "partial",
        "note": note,
    }


def unavailable_rows(cfg, trial, roles, streams, probe, note):
    rows = []
    for role in roles:
        stream = streams.get(role) if streams else None
        for item in probe.items():
            row = key_row(
                trial, role, stream, item, 0, 0, "trial_unavailable", note,
                None, "unavailable",
            )
            rows.append(row)
            print_live_key(cfg, row)
    return rows


def run_trial(cfg: dict, trial: dict, probe: ProbeSource):
    roles = [f"interactive_{index}" for index in range(trial["stream_count"])]
    connection = None
    endpoints = {}
    streams = {}
    files = {}
    session = ""
    panes = []
    key_rows = []
    setup_ms = 0.0
    workload_ms = 0.0
    note = ""
    audit = ConnectionAudit(trial["protocol"], False, 0, 0, 0, {}, [], {}, "not opened")
    ready_streams = 0
    mosh_panes = trial["protocol"] == "mosh" and len(roles) > 1

    try:
        if mosh_panes:
            specs, files, session = mosh_spec(cfg, trial, roles)
            markers = {}
        else:
            specs, markers, files = direct_specs(cfg, trial, roles)
        setup_start = time.perf_counter_ns()
        connection = open_multiplex_connection(
            cfg, trial["protocol"], specs, trial["trial_tag"]
        )
        streams = connection.open(float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")))
        audit = connection.audit
        for physical_role, stream in streams.items():
            endpoints[physical_role] = InteractiveEndpoint(
                stream,
                int(cfg.get("TERMINAL_ROWS", "48")),
                int(cfg.get("TERMINAL_COLUMNS", "160")),
            )

        if mosh_panes:
            endpoint = endpoints["terminal"]
            panes = wait_tmux_layout(endpoint, cfg, session, roles)
            time.sleep(float(cfg.get("EDITOR_START_SETTLE_SECONDS", "1.0")))
            ready_streams = len(panes)
            initialize_mosh_panes(cfg, trial, endpoint, panes, session)
            if trial["editor"] == "vim":
                time.sleep(0.3)
        else:
            if trial["protocol"] != "mosh":
                for role in roles:
                    endpoints[role].wait_marker(
                        markers[role], float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15"))
                    )
            time.sleep(float(cfg.get("EDITOR_START_SETTLE_SECONDS", "1.0")))
            ready_streams = len(roles)
            for role in roles:
                endpoint = endpoints[role]
                if endpoint.terminal_error:
                    raise RuntimeError(f"{role}: {endpoint.terminal_error}")
                if endpoint.exited.is_set():
                    raise EOFError(f"{role}: editor exited before READY")
            if trial["editor"] == "vim":
                for role in roles:
                    endpoints[role].send(b"i")
                time.sleep(0.3)
        for endpoint in endpoints.values():
            endpoint.wait_quiet()
        setup_ms = (time.perf_counter_ns() - setup_start) / 1_000_000

        time.sleep(float(cfg.get("WARMUP_SECONDS", "5.0")))
        refresh_targets = [endpoints["terminal"]] if mosh_panes else [endpoints[role] for role in roles]
        if mosh_panes:
            refresh_mosh_panes(
                cfg, trial, endpoints["terminal"], panes, session,
            )
            # Preserve Mosh's existing post-selection quiet period. Direct
            # streams use the stricter origin-and-screen stability check below.
            endpoints["terminal"].wait_quiet()
        else:
            synchronize_direct_editors(
                cfg, trial["editor"], roles, endpoints,
            )
        for endpoint in refresh_targets:
            endpoint.screen.clear_history()

        workload_start = time.perf_counter_ns()
        if mosh_panes:
            key_rows = measure_mosh(
                cfg, trial, endpoints["terminal"], panes, session, probe,
            )
        else:
            key_rows = measure_direct(cfg, trial, roles, endpoints, probe)
        workload_ms = (time.perf_counter_ns() - workload_start) / 1_000_000
        if mosh_panes:
            discard_editors(trial["editor"], [endpoints["terminal"]], shared=True)
        else:
            discard_editors(
                trial["editor"], [endpoints[role] for role in roles]
            )
    except Exception as exc:
        note = repr(exc)
        if not key_rows:
            logical_streams = (
                {role: streams.get("terminal") for role in roles}
                if mosh_panes else streams
            )
            key_rows = unavailable_rows(cfg, trial, roles, logical_streams, probe, note)
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as exc:
                note = "; ".join(item for item in (note, f"close={exc!r}") if item)
        for endpoint in endpoints.values():
            endpoint.thread.join(timeout=1.0)

    logical_streams = (
        {role: streams.get("terminal") for role in roles}
        if mosh_panes else streams
    )
    stream_rows = []
    for role in roles:
        role_rows = [row for row in key_rows if row["stream_role"] == role]
        stream_rows.append(summarize_stream(
            trial, role, logical_streams.get(role), role_rows, note,
        ))

    audit_rows = []
    for role in roles:
        stream = logical_streams.get(role)
        semantics = transport_semantics(trial)
        audit_rows.append({
            **{key: trial[key] for key in (
                "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
                "protocol", "editor", "scenario", "stream_count",
            )},
            "stream_role": role,
            "connection_valid": int(audit.valid),
            "connection_pid": audit.connection_pid,
            "socket_count": audit.socket_count,
            "transport_stream_id": getattr(stream, "stream_id", ""),
            "conversation_stream_id": getattr(stream, "conversation_id", ""),
            "transport_semantics": semantics,
            "note": audit.note,
        })

    expected_keys = len(roles) * len(probe.text)
    completed_keys = sum(row["completed"] == 1 for row in key_rows)
    stalls = sum(row["stall"] == 1 for row in key_rows)
    timeouts = sum(row["timeout"] == 1 for row in key_rows)
    completed_streams = sum(row["stream_complete"] == 1 for row in stream_rows)
    unique_streams = len({
        value for value in audit.stream_ids.values() if value
    })
    trial_complete = (
        audit.valid and ready_streams == len(roles) and completed_streams == len(roles)
    )
    trial_row = {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "stream_count",
        )},
        "connection_valid": int(audit.valid),
        "connection_pid": audit.connection_pid,
        "socket_count": audit.socket_count,
        "opened_transport_streams": audit.stream_count,
        "unique_transport_streams": unique_streams,
        "conversation_count": len(audit.conversation_ids),
        "ready_streams": ready_streams,
        "expected_keystrokes": expected_keys,
        "completed_keystrokes": completed_keys,
        "keystroke_completion_rate_pct": fmt(100 * completed_keys / expected_keys),
        "stall_count": stalls,
        "stall_rate_pct": fmt(100 * stalls / expected_keys),
        "timeout_count": timeouts,
        "timeout_rate_pct": fmt(100 * timeouts / expected_keys),
        "completed_streams": completed_streams,
        "stream_completion_rate_pct": fmt(100 * completed_streams / len(roles)),
        "setup_ms": fmt(setup_ms),
        "workload_elapsed_ms": fmt(workload_ms),
        "status": "completed" if trial_complete else "partial",
        "note": "; ".join(item for item in (audit.note, note) if item),
    }
    if cfg_bool(cfg, "CLEANUP_REMOTE_FILES", "1"):
        cleanup_remote(cfg, files, session)
    return key_rows, stream_rows, trial_row, audit_rows
