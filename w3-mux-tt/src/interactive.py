"""Adapter terminal tương tác đặt trên RawStream dùng chung của SSH/SSH3/Mosh."""

from __future__ import annotations

import codecs
import threading
import time
from pathlib import Path

from terminal_screen import ScreenSnapshot, TerminalScreen


class InteractiveEndpoint:
    """Đọc RawStream liên tục, cập nhật màn hình ảo và hỗ trợ chờ render."""

    def __init__(self, raw_stream, rows: int, columns: int, log_path: Path | None = None):
        self.raw_stream = raw_stream
        self.screen = TerminalScreen(rows, columns)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.log_path = log_path
        self.log_handle = (
            log_path.open("wb") if log_path is not None else None
        )
        self.recent = bytearray()
        self.recent_lock = threading.RLock()
        self.terminal_error = ""
        self.exit_status = None
        self.exited = threading.Event()
        self.thread = threading.Thread(
            target=self._reader, name=f"w3-{raw_stream.role}-reader", daemon=True
        )
        self.thread.start()

    def _reader(self):
        try:
            while True:
                try:
                    event = self.raw_stream.receive(timeout=0.2)
                except TimeoutError:
                    continue
                if event.kind == "data":
                    if self.log_handle is not None:
                        self.log_handle.write(event.data)
                        self.log_handle.flush()
                    with self.recent_lock:
                        self.recent.extend(event.data)
                        if len(self.recent) > 262144:
                            del self.recent[:-262144]
                    if event.data_type == 0:
                        observed_ns = time.perf_counter_ns()
                        text = self.decoder.decode(event.data)
                        if text:
                            self.screen.feed(text, observed_ns)
                elif event.kind == "exit":
                    self.exit_status = event.exit_status
                    self.exited.set()
                    return
                elif event.kind == "error":
                    self.terminal_error = event.message
                    self.exited.set()
                    return
        except Exception as exc:
            self.terminal_error = repr(exc)
            self.exited.set()

    def close(self):
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def recent_contains(self, marker: bytes) -> bool:
        with self.recent_lock:
            return marker in self.recent

    def recent_text(self) -> str:
        with self.recent_lock:
            return bytes(self.recent).decode("utf-8", errors="replace")

    def wait_marker(self, marker: bytes, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.recent_contains(marker):
                return
            if self.terminal_error:
                raise RuntimeError(self.terminal_error)
            if self.exited.is_set():
                raise EOFError(f"{self.raw_stream.role} exited before READY")
            time.sleep(0.01)
        raise TimeoutError(f"{self.raw_stream.role}: READY marker not received")

    def wait_quiet(self, quiet_seconds: float = 0.10, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        previous = self.screen.snapshot()
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(min(0.02, quiet_seconds))
            current = self.screen.snapshot()
            if (current.write_seq, current.event_seq) != (
                previous.write_seq, previous.event_seq
            ):
                previous = current
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= quiet_seconds:
                return

    def send(self, data: bytes) -> None:
        if self.terminal_error:
            raise RuntimeError(self.terminal_error)
        self.raw_stream.send(data)

    def snapshot(self) -> ScreenSnapshot:
        return self.screen.snapshot()

    def wait_render(
        self, before: ScreenSnapshot, character: str, sent_ns: int, timeout: float
    ) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observed_ns = (
                self.screen.observed_newline(before)
                if character == "\n"
                else self.screen.observed_at_cursor(before, character)
            )
            if observed_ns is not None and observed_ns >= sent_ns:
                return observed_ns
            if self.terminal_error:
                raise RuntimeError(self.terminal_error)
            if self.exited.is_set():
                raise EOFError(f"{self.raw_stream.role} exited while waiting for render")
            time.sleep(0.002)
        raise TimeoutError(
            f"terminal did not render {character!r} at "
            f"({before.row},{before.column})"
        )

    def wait_position_render(
        self, after_seq: int, row: int, column: int, character: str,
        sent_ns: int, timeout: float,
    ) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observed_ns = self.screen.first_matching_write(
                after_seq, row, column, character
            )
            if observed_ns is not None and observed_ns >= sent_ns:
                return observed_ns
            if self.terminal_error:
                raise RuntimeError(self.terminal_error)
            if self.exited.is_set():
                raise EOFError("Mosh terminal exited while waiting for pane render")
            time.sleep(0.002)
        raise TimeoutError(
            f"terminal did not render {character!r} at pane cell ({row},{column})"
        )
