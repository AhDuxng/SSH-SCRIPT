"""Gửi lệnh cat trực tiếp qua các stream byte dùng chung."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stream_mux import ConnectionAudit, RawStream, StreamSpec
from stream_mux import open_multiplex_connection

from constants import PAYLOAD_BYTES
from framing import MarkerDecoder, MarkerEvent, build_direct_line


@dataclass
class PendingTransfer:
    """Giữ trạng thái một lần truyền payload đang diễn ra."""

    token: str
    line_prefix: bytes
    event: threading.Event = field(default_factory=threading.Event)
    output: bytearray = field(default_factory=bytearray)
    started: bool = False
    ambiguous: bool = False
    truncated: bool = False
    exit_code: int | None = None
    error: str = ""
    first_byte_wall_ns: int = 0
    first_byte_mono_ns: int = 0
    last_byte_wall_ns: int = 0
    last_byte_mono_ns: int = 0
    marker_wall_ns: int = 0
    marker_mono_ns: int = 0


class DirectCoordinator:
    """Điều phối nhiều lần truyền trên một stream vật lý."""

    # Khởi tạo bộ điều phối và giới hạn vùng giữ output.
    def __init__(
        self, raw_stream: RawStream, background: bool, max_capture_bytes: int
    ):
        self.raw_stream = raw_stream
        self.background = background
        self.max_capture_bytes = max_capture_bytes
        self.decoder = MarkerDecoder()
        self.pending: dict[str, PendingTransfer] = {}
        self.active: dict[str, PendingTransfer] = {}
        self.lock = threading.Lock()

    # Tạo mã dấu mốc ổn định từ mã yêu cầu.
    def _token(self, request_id: str) -> str:
        return hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]

    # Đăng ký lần truyền trước khi gửi lệnh cat.
    def _register(self, request_id: str, line_prefix: bytes) -> PendingTransfer:
        token = self._token(request_id)
        transfer = PendingTransfer(token, line_prefix)
        with self.lock:
            if token in self.pending:
                raise RuntimeError(f"trùng mã yêu cầu W2: {request_id}")
            self.pending[token] = transfer
        return transfer

    # Gỡ lần truyền khỏi các bảng trạng thái.
    def _discard(self, transfer: PendingTransfer) -> None:
        with self.lock:
            self.pending.pop(transfer.token, None)
            self.active.pop(transfer.token, None)

    # Ghi một dòng payload và thời điểm byte đầu/cuối.
    def _append_output(
        self, transfer: PendingTransfer, data: bytes,
        wall_ns: int, mono_ns: int,
    ) -> None:
        if not transfer.first_byte_mono_ns:
            transfer.first_byte_wall_ns = wall_ns
            transfer.first_byte_mono_ns = mono_ns
        transfer.last_byte_wall_ns = wall_ns
        transfer.last_byte_mono_ns = mono_ns
        remaining = self.max_capture_bytes - len(transfer.output)
        if remaining <= 0:
            transfer.truncated = True
            return
        transfer.output.extend(data[:remaining])
        if len(data) > remaining:
            transfer.truncated = True

    # Định tuyến một dòng theo tiền tố riêng của payload.
    def _route_output(self, data: bytes, wall_ns: int, mono_ns: int) -> None:
        active = list(self.active.values())
        if not active:
            return
        matches = [item for item in active if item.line_prefix in data]
        if len(matches) == 1:
            transfer = matches[0]
            marker = data.find(transfer.line_prefix)
            self._append_output(transfer, data[marker:], wall_ns, mono_ns)
            return
        if len(active) == 1:
            self._append_output(active[0], data, wall_ns, mono_ns)
            return
        for transfer in active:
            transfer.ambiguous = True

    # Phân phối một sự kiện parser tới lần truyền tương ứng.
    def feed(
        self, event: MarkerEvent, wall_ns: int, mono_ns: int
    ) -> None:
        with self.lock:
            if event.kind == "start":
                transfer = self.pending.get(event.token)
                if transfer is not None:
                    transfer.started = True
                    self.active[event.token] = transfer
                return
            if event.kind == "done":
                transfer = self.pending.pop(event.token, None)
                self.active.pop(event.token, None)
                if transfer is not None:
                    transfer.exit_code = event.exit_code
                    transfer.marker_wall_ns = wall_ns
                    transfer.marker_mono_ns = mono_ns
                    transfer.event.set()
                return
            if event.kind == "output":
                self._route_output(event.data, wall_ns, mono_ns)

    # Giải mã một khối byte vừa nhận.
    def feed_bytes(self, data: bytes) -> None:
        wall_ns = time.time_ns()
        mono_ns = time.perf_counter_ns()
        for event in self.decoder.feed(data):
            self.feed(event, wall_ns, mono_ns)

    # Báo lỗi cho toàn bộ lần truyền đang chờ.
    def fail_all(self, message: str) -> None:
        with self.lock:
            transfers = list(self.pending.values())
            self.pending.clear()
            self.active.clear()
        for transfer in transfers:
            transfer.error = message
            transfer.event.set()

    # Gửi lệnh cat thật và chờ dấu hoàn thành.
    def execute(
        self, request_id: str, command_text: str,
        line_prefix: bytes, timeout: float,
    ) -> dict:
        transfer = self._register(request_id, line_prefix)
        line = build_direct_line(command_text, transfer.token, self.background)
        sent_wall_ns = time.time_ns()
        sent_mono_ns = time.perf_counter_ns()
        try:
            self.raw_stream.send(line)
        except Exception:
            self._discard(transfer)
            raise
        timed_out = not transfer.event.wait(timeout)
        if timed_out:
            self._discard(transfer)
        if transfer.error:
            raise RuntimeError(transfer.error)
        first_latency = (
            (transfer.first_byte_mono_ns - sent_mono_ns) / 1_000_000.0
            if transfer.first_byte_mono_ns else None
        )
        completion_latency = (
            (transfer.last_byte_mono_ns - sent_mono_ns) / 1_000_000.0
            if transfer.last_byte_mono_ns else None
        )
        return {
            "stdout": bytes(transfer.output),
            "exit_code": transfer.exit_code,
            "send_time_ns": sent_wall_ns,
            "first_byte_time_ns": transfer.first_byte_wall_ns or None,
            "last_byte_time_ns": transfer.last_byte_wall_ns or None,
            "marker_time_ns": transfer.marker_wall_ns or None,
            "first_byte_latency_ms": first_latency,
            "completion_latency_ms": completion_latency,
            "marker_latency_ms": (
                transfer.marker_mono_ns - sent_mono_ns
            ) / 1_000_000.0 if transfer.marker_mono_ns else None,
            "completion_marker_received": not timed_out,
            "timed_out": timed_out,
            "output_ambiguous": transfer.ambiguous,
            "output_truncated": transfer.truncated,
        }

    # Kiểm tra Bash từ xa đã sẵn sàng nhận lệnh.
    def probe(self, request_id: str, timeout: float) -> None:
        self.execute(request_id, ":", b"__W2TT_NO_OUTPUT__", timeout)


class DirectOutputStream:
    """Cung cấp một vai trò truyền output của W2."""

    # Gắn vai trò logic vào bộ điều phối vật lý.
    def __init__(self, role: str, coordinator: DirectCoordinator):
        self.role = role
        self.coordinator = coordinator
        self.raw_stream = coordinator.raw_stream
        self.stream_id = self.raw_stream.stream_id
        self.conversation_id = self.raw_stream.conversation_id
        self.request_lock = threading.Lock()

    # Gửi tuần tự một lệnh cat trên vai trò này.
    def execute(
        self, request_id: str, command: str,
        line_prefix: bytes, timeout: float,
    ) -> dict:
        with self.request_lock:
            return self.coordinator.execute(
                request_id, command, line_prefix, timeout
            )


class DirectW2Connection:
    """Ghép workload W2 trực tiếp với transport dùng chung."""

    # Khởi tạo connection và các vai trò output.
    def __init__(self, cfg: dict, protocol: str, roles: list[str], trial_tag: str):
        self.cfg = cfg
        self.protocol = protocol
        self.roles = list(roles)
        self.trial_tag = trial_tag
        self.transport = None
        self.streams: dict[str, DirectOutputStream] = {}
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
                columns=int(self.cfg.get("W2_MOSH_COLUMNS", "4096")),
                rows=int(self.cfg.get("W2_MOSH_ROWS", "128")),
            )]
        return [StreamSpec(role, f"exec {shell}") for role in self.roles]

    # Đọc dữ liệu liên tục từ một stream vật lý.
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
                    message = event.message or f"stream thoát với mã {event.exit_status}"
                    coordinator.fail_all(message)
                return

    # Mở transport, Bash và kiểm tra sẵn sàng song song.
    def open(self, timeout: float) -> dict[str, DirectOutputStream]:
        self.transport = open_multiplex_connection(
            self.cfg, self.protocol, self._stream_specs(), self.trial_tag
        )
        try:
            raw_streams = self.transport.open(timeout)
        finally:
            self.audit = self.transport.audit

        max_capture = int(self.cfg.get("MAX_CAPTURE_BYTES", "2097152"))
        if max_capture < PAYLOAD_BYTES:
            raise ValueError(
                f"MAX_CAPTURE_BYTES phải ít nhất {PAYLOAD_BYTES}"
            )
        if self.protocol == "mosh":
            coordinator = DirectCoordinator(raw_streams["terminal"], True, max_capture)
            self.coordinators.append(coordinator)
            for role in self.roles:
                self.streams[role] = DirectOutputStream(role, coordinator)
        else:
            for role in self.roles:
                coordinator = DirectCoordinator(raw_streams[role], False, max_capture)
                self.coordinators.append(coordinator)
                self.streams[role] = DirectOutputStream(role, coordinator)

        for index, coordinator in enumerate(self.coordinators):
            pump = threading.Thread(
                target=self._pump,
                args=(coordinator,),
                name=f"w2-tt-{self.protocol}-{index}",
                daemon=True,
            )
            pump.start()
            self.pumps.append(pump)

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

    # Xóa trạng thái terminal và xác nhận buffer đã sạch sau warm-up.
    def prepare_workload(self, timeout: float) -> None:
        with ThreadPoolExecutor(max_workers=len(self.coordinators)) as pool:
            futures = [
                pool.submit(
                    coordinator.execute,
                    f"{self.trial_tag}:clear:{index}",
                    "printf '\\033[2J\\033[H'",
                    b"__W2TT_NO_OUTPUT__",
                    timeout,
                )
                for index, coordinator in enumerate(self.coordinators)
            ]
            for future in futures:
                future.result()

    # Đóng Bash và connection sau trial.
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


# Tạo connection W2 gửi lệnh trực tiếp.
def open_direct_w2_connection(
    cfg: dict, protocol: str, roles: list[str], trial_tag: str
) -> DirectW2Connection:
    return DirectW2Connection(cfg, protocol, roles, trial_tag)
