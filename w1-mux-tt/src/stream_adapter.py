"""Gửi lệnh W1 trực tiếp qua các stream byte dùng chung."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stream_mux import ConnectionAudit, RawStream, StreamSpec
from stream_mux import open_multiplex_connection

from framing import MarkerDecoder, MarkerEvent, build_direct_line


@dataclass
class PendingCommand:
    """Giữ trạng thái một lệnh đang chờ dấu hoàn thành."""

    token: str
    event: threading.Event = field(default_factory=threading.Event)
    output: bytearray = field(default_factory=bytearray)
    started: bool = False
    ambiguous: bool = False
    exit_code: int | None = None
    error: str = ""
    completion_wall_ns: int = 0
    completion_mono_ns: int = 0


class DirectCoordinator:
    """Điều phối dấu mốc trên một stream vật lý."""

    # Khởi tạo bộ điều phối cho một stream vật lý.
    def __init__(self, raw_stream: RawStream, protocol: str, background: bool):
        self.raw_stream = raw_stream
        self.protocol = protocol
        self.background = background
        self.decoder = MarkerDecoder()
        self.pending: dict[str, PendingCommand] = {}
        self.active: dict[str, PendingCommand] = {}
        self.lock = threading.Lock()

    # Tạo mã dấu mốc ổn định từ mã yêu cầu.
    def _token(self, request_id: str) -> str:
        return hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]

    # Đăng ký một yêu cầu trước khi gửi xuống stream.
    def _register(self, request_id: str) -> PendingCommand:
        token = self._token(request_id)
        command = PendingCommand(token)
        with self.lock:
            if token in self.pending:
                raise RuntimeError(f"trùng mã yêu cầu trực tiếp: {request_id}")
            self.pending[token] = command
        return command

    # Gỡ yêu cầu khỏi mọi bảng trạng thái.
    def _discard(self, command: PendingCommand) -> None:
        with self.lock:
            self.pending.pop(command.token, None)
            self.active.pop(command.token, None)

    # Phân phối một sự kiện dấu mốc tới yêu cầu tương ứng.
    def feed(self, event: MarkerEvent) -> None:
        now_wall_ns = time.time_ns()
        now_mono_ns = time.perf_counter_ns()
        with self.lock:
            if event.kind == "start":
                command = self.pending.get(event.token)
                if command is not None:
                    command.started = True
                    self.active[event.token] = command
                return
            if event.kind == "done":
                command = self.pending.pop(event.token, None)
                self.active.pop(event.token, None)
                if command is not None:
                    command.exit_code = event.exit_code
                    command.completion_wall_ns = now_wall_ns
                    command.completion_mono_ns = now_mono_ns
                    command.event.set()
                return
            if event.kind == "output" and self.active:
                active = list(self.active.values())
                if len(active) > 1:
                    for command in active:
                        command.ambiguous = True
                for command in active:
                    command.output.extend(event.data)

    # Giải mã một khối dữ liệu vừa nhận.
    def feed_bytes(self, data: bytes) -> None:
        for event in self.decoder.feed(data):
            self.feed(event)

    # Báo lỗi cho mọi lệnh đang chờ.
    def fail_all(self, message: str) -> None:
        with self.lock:
            commands = list(self.pending.values())
            self.pending.clear()
            self.active.clear()
        for command in commands:
            command.error = message
            command.event.set()

    # Gửi lệnh thật và chờ đúng dấu hoàn thành.
    def execute(self, request_id: str, command_text: str, timeout: float) -> dict:
        command = self._register(request_id)
        line = build_direct_line(command_text, command.token, self.background)
        sent_wall_ns = time.time_ns()
        sent_mono_ns = time.perf_counter_ns()
        try:
            self.raw_stream.send(line)
        except Exception:
            self._discard(command)
            raise
        if not command.event.wait(timeout):
            self._discard(command)
            raise TimeoutError(f"không nhận dấu hoàn thành cho {request_id}")
        if command.error:
            raise RuntimeError(command.error)
        verifiable = self.protocol in {"ssh", "ssh3"} and not command.ambiguous
        return {
            "stdout": bytes(command.output),
            "stderr": b"",
            "exit_code": command.exit_code,
            "timed_out": False,
            "error": "",
            "send_time_ns": sent_wall_ns,
            "completion_time_ns": command.completion_wall_ns,
            "latency_ms": (
                command.completion_mono_ns - sent_mono_ns
            ) / 1_000_000.0,
            "completion_marker_received": True,
            "output_verifiable": verifiable,
            "output_complete": verifiable and command.started,
            "output_ambiguous": command.ambiguous,
        }

    # Kiểm tra Bash từ xa đã nhận được lệnh trực tiếp.
    def probe(self, request_id: str, timeout: float) -> None:
        self.execute(request_id, ":", timeout)


class DirectCommandStream:
    """Cung cấp giao diện chạy lệnh cho một vai trò W1."""

    # Gắn vai trò logic vào bộ điều phối vật lý.
    def __init__(self, role: str, coordinator: DirectCoordinator):
        self.role = role
        self.coordinator = coordinator
        self.raw_stream = coordinator.raw_stream
        self.stream_id = self.raw_stream.stream_id
        self.conversation_id = self.raw_stream.conversation_id
        self.request_lock = threading.Lock()

    # Gửi tuần tự một lệnh thật trên vai trò này.
    def execute(self, request_id: str, command: str, timeout: float) -> dict:
        with self.request_lock:
            return self.coordinator.execute(request_id, command, timeout)


class DirectW1Connection:
    """Ghép Bash trực tiếp với transport dùng chung."""

    # Khởi tạo connection và các vai trò của trial.
    def __init__(self, cfg: dict, protocol: str, roles: list[str], trial_tag: str):
        self.cfg = cfg
        self.protocol = protocol
        self.roles = list(roles)
        self.trial_tag = trial_tag
        self.transport = None
        self.streams: dict[str, DirectCommandStream] = {}
        self.coordinators: list[DirectCoordinator] = []
        self.pumps: list[threading.Thread] = []
        self.closing = False
        self.audit = ConnectionAudit(protocol, False, 0, 0, 0, {}, [], {}, "chưa mở")

    # Tạo Bash thường trực cho từng mô hình transport.
    def _stream_specs(self) -> list[StreamSpec]:
        shell = self.cfg.get(
            "DIRECT_SHELL_COMMAND", "/bin/bash --noprofile --norc"
        )
        if self.protocol == "mosh":
            return [StreamSpec(
                "terminal",
                f"stty -echo; exec {shell}",
                allocate_pty=True,
                columns=int(self.cfg.get("W1_MOSH_COLUMNS", "4096")),
                rows=int(self.cfg.get("W1_MOSH_ROWS", "128")),
            )]
        return [StreamSpec(role, f"exec {shell}") for role in self.roles]

    # Đọc liên tục dữ liệu của một stream vật lý.
    def _pump(self, coordinator: DirectCoordinator) -> None:
        raw_stream = coordinator.raw_stream
        while True:
            try:
                event = raw_stream.receive()
            except Exception as exc:
                if not self.closing:
                    coordinator.fail_all(repr(exc))
                return
            if event.kind == "data":
                coordinator.feed_bytes(event.data)
                continue
            if event.kind in {"error", "exit"}:
                if not self.closing:
                    message = event.message or (
                        f"stream thoát với mã {event.exit_status}"
                    )
                    coordinator.fail_all(message)
                return

    # Mở transport, Bash thường trực và kiểm tra sẵn sàng.
    def open(self, timeout: float) -> dict[str, DirectCommandStream]:
        self.transport = open_multiplex_connection(
            self.cfg, self.protocol, self._stream_specs(), self.trial_tag
        )
        try:
            raw_streams = self.transport.open(timeout)
        finally:
            self.audit = self.transport.audit

        if self.protocol == "mosh":
            coordinator = DirectCoordinator(raw_streams["terminal"], "mosh", True)
            self.coordinators.append(coordinator)
            for role in self.roles:
                self.streams[role] = DirectCommandStream(role, coordinator)
        else:
            for role in self.roles:
                coordinator = DirectCoordinator(raw_streams[role], self.protocol, False)
                self.coordinators.append(coordinator)
                self.streams[role] = DirectCommandStream(role, coordinator)

        for index, coordinator in enumerate(self.coordinators):
            pump = threading.Thread(
                target=self._pump,
                args=(coordinator,),
                name=f"w1-tt-{self.protocol}-{index}",
                daemon=True,
            )
            pump.start()
            self.pumps.append(pump)

        # Kiểm tra song song để setup không cộng tuần tự một RTT cho mỗi stream.
        with ThreadPoolExecutor(max_workers=len(self.coordinators)) as pool:
            futures = [
                pool.submit(
                    coordinator.probe,
                    f"{self.trial_tag}:ready:{index}",
                    timeout,
                )
                for index, coordinator in enumerate(self.coordinators)
            ]
            for future in futures:
                future.result()
        return self.streams

    # Đóng Bash và transport dùng chung.
    def close(self) -> None:
        self.closing = True
        sent: set[int] = set()
        for stream in self.streams.values():
            identity = id(stream.raw_stream)
            if identity in sent:
                continue
            sent.add(identity)
            try:
                stream.raw_stream.send(b"exit\n")
            except Exception:
                pass
        if self.transport is not None:
            self.transport.close()


# Tạo connection W1 gửi lệnh trực tiếp.
def open_direct_w1_connection(
    cfg: dict, protocol: str, roles: list[str], trial_tag: str
) -> DirectW1Connection:
    return DirectW1Connection(cfg, protocol, roles, trial_tag)
