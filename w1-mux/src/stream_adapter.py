"""Bọc transport byte thô bằng giao thức lệnh riêng của W1."""

from __future__ import annotations

import queue
import shlex
import threading
import time

from stream_mux import ConnectionAudit, RawStream, StreamSpec
from stream_mux import open_multiplex_connection

from framing import FrameDecoder, decode_bytes, encode_frame


# Tạo lệnh khởi chạy agent W1 trên máy đích.
def remote_agent_command(remote_agent: str, roles: list[str]) -> str:
    return shlex.join([
        "python3", "-u", remote_agent, "--roles", ",".join(roles),
    ])


class W1CommandStream:
    """Thực thi request/result W1 trên một stream byte thô."""

    # Khởi tạo stream lệnh theo role W1.
    def __init__(self, role: str, raw_stream: RawStream):
        self.role = role
        self.raw_stream = raw_stream
        self.stream_id = raw_stream.stream_id
        self.conversation_id = raw_stream.conversation_id
        self._events: queue.Queue[dict] = queue.Queue()
        self._backlog: dict[str, dict] = {}
        self._request_lock = threading.Lock()

    # Đưa một frame W1 vào hàng đợi.
    def put_event(self, frame: dict) -> None:
        self._events.put(frame)

    # Chờ đúng loại sự kiện W1 được yêu cầu.
    def wait_event(self, event_type: str, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.role}: timed out waiting for {event_type}")
            try:
                frame = self._events.get(timeout=remaining)
            except queue.Empty as exc:
                raise TimeoutError(
                    f"{self.role}: timed out waiting for {event_type}"
                ) from exc
            if frame.get("type") == "error":
                raise RuntimeError(f"{self.role}: {frame.get('message', 'remote error')}")
            if frame.get("type") == event_type:
                return frame

    # Gửi một lệnh W1 và chờ kết quả tương ứng.
    def execute(self, request_id: str, command: str, timeout: float) -> dict:
        with self._request_lock:
            sent_wall_ns = time.time_ns()
            sent_mono_ns = time.perf_counter_ns()
            self.raw_stream.send(encode_frame({
                "type": "exec",
                "role": self.role,
                "request_id": request_id,
                "command": command,
                "timeout_seconds": timeout,
            }))
            deadline = time.monotonic() + timeout + 5.0
            frame = self._backlog.pop(request_id, None)
            while frame is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.role}: no result for {request_id}")
                try:
                    event = self._events.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(
                        f"{self.role}: no result for {request_id}"
                    ) from exc
                if event.get("type") == "error":
                    raise RuntimeError(
                        f"{self.role}: {event.get('message', 'remote error')}"
                    )
                if event.get("type") != "result":
                    continue
                event_id = str(event.get("request_id", ""))
                if event_id == request_id:
                    frame = event
                elif event_id:
                    self._backlog[event_id] = event
            received_mono_ns = time.perf_counter_ns()
            received_wall_ns = time.time_ns()
            return {
                **frame,
                "stdout": decode_bytes(frame.get("stdout_b64", "")),
                "stderr": decode_bytes(frame.get("stderr_b64", "")),
                "send_time_ns": sent_wall_ns,
                "completion_time_ns": received_wall_ns,
                "latency_ms": (received_mono_ns - sent_mono_ns) / 1_000_000.0,
            }


class W1Connection:
    """Ghép adapter lệnh W1 với một transport dùng chung."""

    # Khởi tạo cấu hình và các role logic W1.
    def __init__(self, cfg: dict, protocol: str, roles: list[str], trial_tag: str):
        self.cfg = cfg
        self.protocol = protocol
        self.roles = list(roles)
        self.trial_tag = trial_tag
        self.transport = None
        self.streams: dict[str, W1CommandStream] = {}
        self._raw_streams: dict[str, RawStream] = {}
        self._pumps: list[threading.Thread] = []
        self._closing = False
        self.audit = ConnectionAudit(protocol, False, 0, 0, 0, {}, [], {}, "not opened")

    # Tạo đặc tả transport phù hợp với giới hạn từng giao thức.
    def _stream_specs(self) -> list[StreamSpec]:
        remote_agent = self.cfg.get("W1_REMOTE_AGENT", "/tmp/w1_mux_agent.py")
        if self.protocol == "mosh":
            command = remote_agent_command(remote_agent, self.roles)
            return [StreamSpec(
                "terminal",
                f"stty -echo; exec {command}",
                allocate_pty=True,
            )]
        return [
            StreamSpec(role, remote_agent_command(remote_agent, [role]))
            for role in self.roles
        ]

    # Chuyển frame nhận được tới đúng role W1.
    def _dispatch_frame(self, fallback_role: str, frame: dict) -> None:
        role = str(frame.get("role", fallback_role))
        stream = self.streams.get(role)
        if stream is not None:
            stream.put_event(frame)

    # Đọc một stream transport và giải mã frame W1.
    def _pump(self, raw_stream: RawStream, fallback_role: str) -> None:
        decoder = FrameDecoder()
        while True:
            try:
                event = raw_stream.receive()
            except Exception as exc:
                if not self._closing:
                    self._dispatch_frame(fallback_role, {
                        "type": "error", "role": fallback_role,
                        "message": repr(exc),
                    })
                return
            if event.kind == "data":
                if event.data_type != 0:
                    continue
                for frame in decoder.feed(event.data):
                    self._dispatch_frame(fallback_role, frame)
                continue
            if event.kind in {"error", "exit"}:
                if not self._closing:
                    message = event.message or (
                        f"transport stream exited with status {event.exit_status}"
                    )
                    targets = self.roles if self.protocol == "mosh" else [fallback_role]
                    for role in targets:
                        self._dispatch_frame(role, {
                            "type": "error", "role": role, "message": message,
                        })
                return

    # Mở transport, khởi chạy bộ giải mã và chờ READY của W1.
    def open(self, timeout: float) -> dict[str, W1CommandStream]:
        specs = self._stream_specs()
        self.transport = open_multiplex_connection(
            self.cfg, self.protocol, specs, self.trial_tag
        )
        try:
            self._raw_streams = self.transport.open(timeout)
        finally:
            self.audit = self.transport.audit

        if self.protocol == "mosh":
            raw_stream = self._raw_streams["terminal"]
            for role in self.roles:
                self.streams[role] = W1CommandStream(role, raw_stream)
            pump_specs = [(raw_stream, self.roles[0])]
        else:
            for role in self.roles:
                self.streams[role] = W1CommandStream(role, self._raw_streams[role])
            pump_specs = [
                (self._raw_streams[role], role) for role in self.roles
            ]

        for raw_stream, fallback_role in pump_specs:
            pump = threading.Thread(
                target=self._pump,
                args=(raw_stream, fallback_role),
                name=f"w1-{self.protocol}-{fallback_role}",
                daemon=True,
            )
            pump.start()
            self._pumps.append(pump)

        deadline = time.monotonic() + timeout
        for role in self.roles:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"W1 streams not ready: {role}")
            self.streams[role].wait_event("ready", remaining)
        return self.streams

    # Dừng agent W1 rồi đóng transport dùng chung.
    def close(self) -> None:
        self._closing = True
        sent: set[int] = set()
        for stream in self.streams.values():
            raw_identity = id(stream.raw_stream)
            if raw_identity in sent:
                continue
            sent.add(raw_identity)
            try:
                stream.raw_stream.send(encode_frame({"type": "shutdown"}))
            except Exception:
                pass
        if self.transport is not None:
            self.transport.close()


# Tạo connection đã gắn giao thức W1.
def open_w1_connection(
    cfg: dict, protocol: str, roles: list[str], trial_tag: str
) -> W1Connection:
    return W1Connection(cfg, protocol, roles, trial_tag)
