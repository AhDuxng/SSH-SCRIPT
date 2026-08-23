"""Measured W1/W2 background workloads used by W4."""

from __future__ import annotations

import hashlib
import re
import shlex
import threading
import time
from dataclasses import dataclass, field

from constants import COMMANDS, PAYLOAD_BYTES, PAYLOAD_NAME, PAYLOAD_SHA256


START_RE = re.compile(rb"__W4BG_START__:([0-9a-f]{24})")
DONE_RE = re.compile(
    rb"__W4BG_DONE__:([0-9a-f]{24}):(-?[0-9]+)(?::(\d+):([0-9a-f]{64}))?"
)
MOSH_MARKER_RE = re.compile(
    rb"__W4BG_(START|DONE|EXIT)__:(command_0|output_0):(\d+)(?::(\d+))?(?::(-?\d+))?"
)


@dataclass
class Pending:
    token: str
    event: threading.Event = field(default_factory=threading.Event)
    output: bytearray = field(default_factory=bytearray)
    first_wall_ns: int = 0
    first_mono_ns: int = 0
    last_wall_ns: int = 0
    last_mono_ns: int = 0
    done_wall_ns: int = 0
    done_mono_ns: int = 0
    exit_code: int | None = None
    expected_bytes: int | None = None
    expected_sha256: str = ""
    error: str = ""


class BackgroundCoordinator:
    """Execute one framed command at a time on one lossless transport stream."""

    def __init__(self, raw_stream):
        self.raw_stream = raw_stream
        self.pending: Pending | None = None
        self.buffer = b""
        self.lock = threading.RLock()
        self.closed = False
        self.thread = threading.Thread(target=self._pump, name=f"w4-bg-{raw_stream.role}", daemon=True)
        self.thread.start()

    @staticmethod
    def token(request_id: str) -> str:
        return hashlib.sha256(request_id.encode()).hexdigest()[:24]

    def _pump(self):
        while not self.closed:
            try:
                event = self.raw_stream.receive(timeout=0.2)
            except TimeoutError:
                continue
            except Exception as exc:
                self.fail(repr(exc))
                return
            if event.kind == "data":
                self._feed(event.data)
            elif event.kind in {"exit", "error"}:
                self.fail(event.message or f"stream exited with {event.exit_status}")
                return

    def _feed(self, data: bytes):
        now_wall, now_mono = time.time_ns(), time.perf_counter_ns()
        with self.lock:
            self.buffer += data
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                clean = line.rstrip(b"\r")
                start = START_RE.fullmatch(clean)
                done = DONE_RE.fullmatch(clean)
                if start:
                    continue
                if done:
                    current = self.pending
                    if current is not None and done.group(1).decode() == current.token:
                        current.exit_code = int(done.group(2))
                        if done.group(3) is not None:
                            current.expected_bytes = int(done.group(3))
                            current.expected_sha256 = done.group(4).decode()
                        current.done_wall_ns, current.done_mono_ns = now_wall, now_mono
                        current.event.set()
                    continue
                current = self.pending
                if current is not None:
                    chunk = line + b"\n"
                    if not current.first_mono_ns:
                        current.first_wall_ns, current.first_mono_ns = now_wall, now_mono
                    current.last_wall_ns, current.last_mono_ns = now_wall, now_mono
                    current.output.extend(chunk)

    def fail(self, message: str):
        with self.lock:
            if self.pending is not None:
                self.pending.error = message
                self.pending.event.set()

    def execute(
        self, request_id: str, command: str, timeout: float,
        capture_expected: bool = False,
    ) -> dict:
        token = self.token(request_id)
        pending = Pending(token)
        with self.lock:
            if self.pending is not None:
                raise RuntimeError(f"{self.raw_stream.role}: concurrent execute is not allowed")
            self.pending = pending
        if capture_expected:
            # W1 commands are dynamic. Spool exactly this invocation once on
            # the server, announce its byte count/hash, then send those same
            # bytes to the client so completeness is objectively verifiable.
            wrapped = (
                "__w4_tmp=$(mktemp); "
                f"printf '__W4BG_START__:{token}\\n'; "
                f"{{ {command}; }} >\"$__w4_tmp\" 2>&1; __w4_rc=$?; "
                "__w4_bytes=$(wc -c <\"$__w4_tmp\" | tr -d '[:space:]'); "
                "__w4_hash=$(sha256sum \"$__w4_tmp\" | awk '{print $1}'); "
                "cat \"$__w4_tmp\"; "
                f"printf '__W4BG_DONE__:{token}:%s:%s:%s\\n' "
                "\"$__w4_rc\" \"$__w4_bytes\" \"$__w4_hash\"; "
                "rm -f \"$__w4_tmp\"\n"
            ).encode()
        else:
            wrapped = (
                f"printf '__W4BG_START__:{token}\\n'; "
                f"{{ {command}; }} 2>&1; __w4_rc=$?; "
                f"printf '__W4BG_DONE__:{token}:%s\\n' \"$__w4_rc\"\n"
            ).encode()
        sent_wall, sent_mono = time.time_ns(), time.perf_counter_ns()
        self.raw_stream.send(wrapped)
        timed_out = not pending.event.wait(timeout)
        with self.lock:
            self.pending = None
        if pending.error:
            raise RuntimeError(pending.error)
        output = bytes(pending.output)
        return {
            "send_time_ns": sent_wall,
            "first_byte_time_ns": pending.first_wall_ns,
            "completion_time_ns": pending.last_wall_ns or pending.done_wall_ns,
            "first_byte_latency_ms": (
                (pending.first_mono_ns - sent_mono) / 1e6 if pending.first_mono_ns else None
            ),
            "completion_latency_ms": (
                ((pending.last_mono_ns or pending.done_mono_ns) - sent_mono) / 1e6
                if pending.last_mono_ns or pending.done_mono_ns else None
            ),
            "exit_code": pending.exit_code,
            "expected_bytes": pending.expected_bytes,
            "expected_sha256": pending.expected_sha256,
            "stdout": output,
            "completion_marker_received": bool(pending.done_mono_ns),
            "timed_out": timed_out,
        }

    def probe(self, timeout: float):
        result = self.execute(f"ready:{self.raw_stream.role}:{time.time_ns()}", ":", timeout)
        if result["exit_code"] != 0 or not result["completion_marker_received"]:
            raise RuntimeError(f"{self.raw_stream.role}: readiness probe failed")

    def close(self):
        self.closed = True


def background_row(trial, stream, role, kind, sample, operation_index, operation, result, note=""):
    output = result.get("stdout", b"")
    digest = hashlib.sha256(output).hexdigest()
    is_output = kind == "output"
    expected_bytes = PAYLOAD_BYTES if is_output else result.get("expected_bytes", "")
    expected_hash = PAYLOAD_SHA256 if is_output else result.get("expected_sha256", "")
    marker = bool(result.get("completion_marker_received"))
    exit_ok = result.get("exit_code") == 0
    output_complete = bool(
        marker and exit_ok and expected_bytes != "" and expected_hash
        and len(output) == int(expected_bytes) and digest == expected_hash
    )
    timed_out = bool(result.get("timed_out"))
    status = "timeout" if timed_out else ("completed" if output_complete else "partial")
    return {
        **{key: trial[key] for key in (
            "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
            "protocol", "editor", "scenario", "logical_workload_count",
        )},
        "stream_role": role,
        "workload_type": kind,
        "transport_stream_id": getattr(stream, "stream_id", ""),
        "conversation_stream_id": getattr(stream, "conversation_id", ""),
        "transport_semantics": "transport_stream",
        "measurement_origin": "client_send_to_client_completion",
        "sample_index": sample,
        "operation_index": operation_index,
        "operation": operation,
        "send_time_ns": result.get("send_time_ns", ""),
        "first_byte_time_ns": result.get("first_byte_time_ns", ""),
        "completion_time_ns": result.get("completion_time_ns", ""),
        "first_byte_latency_ms": fmt(result.get("first_byte_latency_ms")),
        "completion_latency_ms": fmt(result.get("completion_latency_ms")),
        "exit_code": "" if result.get("exit_code") is None else result["exit_code"],
        "expected_bytes": expected_bytes,
        "received_bytes": len(output),
        "expected_sha256": expected_hash,
        "received_sha256": digest,
        "completion_marker_received": int(marker),
        "output_complete": int(output_complete),
        "timed_out": int(timed_out),
        "status": status,
        "note": note,
    }


def fmt(value):
    return "" if value is None or value == "" else f"{float(value):.3f}"


def run_direct_background(cfg, trial, role, stream, coordinator, stop_event, start_barrier):
    kind = "command" if role == "command_0" else "output"
    timeout = float(cfg.get(
        "BACKGROUND_OUTPUT_TIMEOUT_SECONDS" if kind == "output"
        else "BACKGROUND_COMMAND_TIMEOUT_SECONDS",
        "120" if kind == "output" else "30",
    ))
    payload_path = f"{cfg.get('W4_REMOTE_PAYLOAD_DIR', '/tmp/w4_mux_tt_payloads').rstrip('/')}/{PAYLOAD_NAME}"
    rows, sample = [], 0
    start_barrier.wait(timeout=float(cfg.get("STREAM_READY_TIMEOUT_SECONDS", "15")))
    while not stop_event.is_set():
        sample += 1
        operation_index = ((sample - 1) % len(COMMANDS)) + 1 if kind == "command" else 1
        operation = COMMANDS[operation_index - 1] if kind == "command" else f"cat {shlex.quote(payload_path)}"
        request_id = f"{trial['trial_id']}:{role}:{sample}"
        try:
            result = coordinator.execute(
                request_id, operation, timeout, capture_expected=(kind == "command")
            )
            row = background_row(trial, stream, role, kind, sample, operation_index, operation, result)
        except Exception as exc:
            row = background_row(
                trial, stream, role, kind, sample, operation_index, operation,
                {"stdout": b"", "timed_out": isinstance(exc, TimeoutError)}, repr(exc),
            )
        rows.append(row)
        if cfg.get("LIVE_PROGRESS", "1") == "1":
            print(
                f"[BG] trial={trial['trial_id']} role={role} sample={sample:04d} "
                f"status={row['status']} latency_ms={row['completion_latency_ms'] or '-'} "
                f"bytes={row['received_bytes']}", flush=True,
            )
        if row["status"] == "timeout" or (
            row["status"] == "partial" and cfg.get("BACKGROUND_CONTINUE_ON_ERROR", "1") != "1"
        ):
            break
    return rows


class TerminalControlFilter:
    def __init__(self):
        self.state = "text"

    def feed(self, chunk: bytes) -> bytes:
        output = bytearray()
        for value in chunk:
            if self.state == "text":
                if value == 0x1B:
                    self.state = "escape"
                elif value in (10, 13) or value >= 32:
                    output.append(value)
            elif self.state == "escape":
                if value == ord("["):
                    self.state = "csi"
                elif value in (ord("]"), ord("P"), ord("X"), ord("^"), ord("_")):
                    self.state = "string"
                else:
                    self.state = "text"
            elif self.state == "csi":
                if 0x40 <= value <= 0x7E:
                    self.state = "text"
            elif self.state == "string":
                if value == 7:
                    self.state = "text"
                elif value == 0x1B:
                    self.state = "string_escape"
            elif self.state == "string_escape":
                self.state = "text" if value == ord("\\") else "string"
        return bytes(output)


class MoshBackgroundCollector:
    """Observe background markers in one Mosh terminal without claiming byte losslessness."""

    def __init__(self):
        self.filter = TerminalControlFilter()
        self.buffer = b""
        self.scan_position = 0
        self.active = {}
        self.completed = []
        self.exited = set()
        self.seen_markers = set()
        self.lock = threading.RLock()

    def _accept_marker(self, match, wall_ns, mono_ns):
        kind = match.group(1).decode()
        role = match.group(2).decode()
        sample = int(match.group(3))
        operation_index = int(match.group(4) or b"0")
        exit_code = int(match.group(5)) if match.group(5) is not None else None
        identity = (kind, role, sample, operation_index, exit_code)
        if identity in self.seen_markers:
            return
        self.seen_markers.add(identity)
        if kind == "START":
            self.active[role] = {
                "sample": sample, "operation_index": operation_index,
                "start_wall_ns": wall_ns, "start_mono_ns": mono_ns,
                "observed_bytes": 0,
            }
        elif kind == "DONE":
            item = self.active.pop(role, None)
            if item is not None:
                item.update({
                    "role": role, "done_wall_ns": wall_ns,
                    "done_mono_ns": mono_ns, "exit_code": exit_code,
                })
                self.completed.append(item)
        elif kind == "EXIT":
            self.exited.add(role)

    def feed(self, chunk: bytes, wall_ns: int, mono_ns: int, screen=None):
        clean = self.filter.feed(chunk).replace(b"\r", b"\n")
        with self.lock:
            for item in self.active.values():
                item["observed_bytes"] += len(clean)
            self.buffer += clean
            for match in MOSH_MARKER_RE.finditer(self.buffer, self.scan_position):
                self.scan_position = match.end()
                self._accept_marker(match, wall_ns, mono_ns)
            # Mosh may send only the changed cells of a marker. Scanning the
            # reconstructed terminal catches a complete marker even when its
            # raw bytes were split across unrelated screen-diff updates.
            if screen is not None:
                with screen.lock:
                    rendered = "\n".join("".join(row) for row in screen.screen).encode(
                        "ascii", errors="ignore"
                    )
                for match in MOSH_MARKER_RE.finditer(rendered):
                    self._accept_marker(match, wall_ns, mono_ns)
            if self.scan_position > 262144:
                self.buffer = self.buffer[self.scan_position - 1024:]
                self.scan_position = 1024

    def wait_exit(self, roles, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if set(roles).issubset(self.exited):
                    return
            time.sleep(0.05)

    def rows(self, trial, stream):
        output = []
        with self.lock:
            completed = list(self.completed)
        for item in completed:
            role = item["role"]
            kind = "command" if role == "command_0" else "output"
            op_index = item["operation_index"]
            valid_operation = kind != "command" or 1 <= op_index <= len(COMMANDS)
            if kind == "command" and valid_operation:
                operation = COMMANDS[op_index - 1]
            elif kind == "command":
                operation = f"invalid_command_index_{op_index}"
            else:
                operation = f"cat {PAYLOAD_NAME}"
            observed = item["observed_bytes"]
            exit_ok = item["exit_code"] == 0
            completed_ok = valid_operation and exit_ok
            output.append({
                **{key: trial[key] for key in (
                    "run_id", "block_id", "trial_order", "trial_id", "trial_tag",
                    "protocol", "editor", "scenario", "logical_workload_count",
                )},
                "stream_role": role, "workload_type": kind,
                "transport_stream_id": getattr(stream, "stream_id", ""),
                "conversation_stream_id": getattr(stream, "conversation_id", ""),
                "transport_semantics": "tmux_pane_in_terminal",
                "measurement_origin": "client_observed_start_to_client_observed_completion",
                "sample_index": item["sample"], "operation_index": op_index,
                "operation": operation, "send_time_ns": item["start_wall_ns"],
                "first_byte_time_ns": "", "completion_time_ns": item["done_wall_ns"],
                "first_byte_latency_ms": "",
                "completion_latency_ms": fmt((item["done_mono_ns"] - item["start_mono_ns"]) / 1e6),
                "exit_code": item["exit_code"],
                "expected_bytes": PAYLOAD_BYTES if kind == "output" else "",
                "received_bytes": observed,
                "expected_sha256": PAYLOAD_SHA256 if kind == "output" else "",
                "received_sha256": "", "completion_marker_received": 1,
                "output_complete": 0, "timed_out": 0,
                "status": "completed" if completed_ok else "partial",
                "note": (
                    "Mosh synchronizes screen state, not a lossless byte stream; "
                    "observed_bytes counts shared terminal update characters and is diagnostic only"
                    + (
                        f"; invalid command marker operation_index={op_index}"
                        if not valid_operation else ""
                    )
                    + (
                        f"; exit_code={item['exit_code']}"
                        if not exit_ok else ""
                    )
                ),
            })
        return output
