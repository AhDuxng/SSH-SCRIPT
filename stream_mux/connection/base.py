"""Khai báo hợp đồng transport dùng chung cho mọi workload."""

from __future__ import annotations

import queue
import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass(frozen=True)
class StreamSpec:
    """Mô tả một stream do workload yêu cầu transport mở."""

    role: str
    remote_command: str
    allocate_pty: bool = False
    terminal_type: str = "xterm-256color"
    columns: int = 80
    rows: int = 24

    # Kiểm tra tham số trước khi mở stream.
    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.role):
            raise ValueError("role must contain only letters, digits, dot, dash or underscore")
        if not self.remote_command:
            raise ValueError("role and remote_command must be non-empty")
        if self.allocate_pty and (
            not self.terminal_type or self.columns <= 0 or self.rows <= 0
        ):
            raise ValueError("PTY requires terminal_type, columns and rows")


@dataclass(frozen=True)
class StreamEvent:
    """Biểu diễn dữ liệu hoặc trạng thái nhận từ một stream."""

    kind: str
    data: bytes = b""
    data_type: int = 0
    exit_status: int | None = None
    message: str = ""
    observed_wall_ns: int = 0
    observed_mono_ns: int = 0


@dataclass(frozen=True)
class ConnectionAudit:
    """Lưu bằng chứng connection và stream của một trial."""

    protocol: str
    valid: bool
    connection_pid: int
    socket_count: int
    stream_count: int
    stream_ids: dict[str, str]
    conversation_ids: list[str]
    stream_semantics: dict[str, str] = field(default_factory=dict)
    note: str = ""

    # Chuyển kết quả audit thành dictionary.
    def to_dict(self) -> dict:
        return asdict(self)


class RawStream:
    """Cung cấp luồng byte hai chiều không phụ thuộc workload."""

    # Khởi tạo luồng byte và hàng đợi sự kiện.
    def __init__(
        self,
        role: str,
        sender: Callable[[bytes], None],
        input_closer: Callable[[], None] | None = None,
    ):
        self.role = role
        self.stream_id = ""
        self.conversation_id = ""
        self._sender = sender
        self._input_closer = input_closer
        self._events: queue.Queue[StreamEvent] = queue.Queue()
        self._write_lock = threading.Lock()
        self._input_closed = False

    # Gửi nguyên vẹn một khối byte lên stream.
    def send(self, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("stream data must be bytes")
        if self._input_closed:
            raise RuntimeError(f"{self.role}: stream input is closed")
        with self._write_lock:
            self._sender(data)

    # Nhận sự kiện kế tiếp trong thời gian giới hạn.
    def receive(self, timeout: float | None = None) -> StreamEvent:
        try:
            return self._events.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"{self.role}: no stream event before timeout") from exc

    # Đóng chiều gửi nhưng vẫn cho phép nhận dữ liệu.
    def close_input(self) -> None:
        with self._write_lock:
            if self._input_closed:
                return
            self._input_closed = True
            if self._input_closer is not None:
                self._input_closer()

    # Đưa dữ liệu nhận được vào hàng đợi.
    def put_data(
        self, data: bytes, data_type: int = 0,
        observed_wall_ns: int = 0, observed_mono_ns: int = 0,
    ) -> None:
        if data:
            self._events.put(StreamEvent(
                "data", data=data, data_type=data_type,
                observed_wall_ns=observed_wall_ns,
                observed_mono_ns=observed_mono_ns,
            ))

    # Báo mã thoát của tiến trình từ xa.
    def put_exit(self, exit_status: int | None, message: str = "") -> None:
        self._events.put(StreamEvent(
            "exit", exit_status=exit_status, message=message
        ))

    # Báo lỗi transport cho workload.
    def put_error(self, message: str) -> None:
        self._events.put(StreamEvent("error", message=message))


class MultiplexConnection:
    """Quản lý một connection và các stream transport của nó."""

    # Khởi tạo connection bằng danh sách đặc tả stream.
    def __init__(self, protocol: str, specs: list[StreamSpec]):
        if not specs:
            raise ValueError("at least one stream spec is required")
        for spec in specs:
            spec.validate()
        roles = [spec.role for spec in specs]
        if len(set(roles)) != len(roles):
            raise ValueError("stream roles must be unique")
        self.protocol = protocol
        self.specs = list(specs)
        self.roles = roles
        self.streams: dict[str, RawStream] = {}
        self.audit = ConnectionAudit(
            protocol, False, 0, 0, 0, {}, [], {}, "not opened"
        )

    # Mở connection và toàn bộ stream transport.
    def open(self, timeout: float) -> dict[str, RawStream]:
        raise NotImplementedError

    # Đóng toàn bộ stream rồi đóng connection.
    def close(self) -> None:
        raise NotImplementedError

    # Mở connection khi vào context manager.
    def __enter__(self):
        self.open(20.0)
        return self

    # Đóng connection khi rời context manager.
    def __exit__(self, exc_type, exc, tb):
        self.close()
