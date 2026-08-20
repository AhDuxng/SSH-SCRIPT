"""Interactive terminal reader with optional observers for a shared Mosh screen."""

from __future__ import annotations

import codecs
import threading
import time

from terminal_screen import ScreenSnapshot, TerminalScreen


class InteractiveEndpoint:
    def __init__(
        self, raw_stream, rows: int, columns: int, observers=(),
    ):
        self.raw_stream = raw_stream
        self.screen = TerminalScreen(rows, columns)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.observers = list(observers)
        self.recent = bytearray()
        self.recent_lock = threading.RLock()
        self.terminal_error = ""
        self.exit_status = None
        self.exited = threading.Event()
        self.thread = threading.Thread(target=self._reader, name=f"w4-{raw_stream.role}-reader", daemon=True)
        self.thread.start()

    def _reader(self):
        try:
            while True:
                try:
                    event = self.raw_stream.receive(timeout=0.2)
                except TimeoutError:
                    continue
                if event.kind == "data":
                    observed_ns = time.perf_counter_ns()
                    wall_ns = time.time_ns()
                    with self.recent_lock:
                        self.recent.extend(event.data)
                        if len(self.recent) > 524288:
                            del self.recent[:-524288]
                    if event.data_type == 0:
                        text = self.decoder.decode(event.data)
                        if text:
                            self.screen.feed(text, observed_ns)
                        for observer in self.observers:
                            observer(event.data, wall_ns, observed_ns, self.screen)
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

    def recent_text(self) -> str:
        with self.recent_lock:
            return bytes(self.recent).decode("utf-8", errors="replace")

    def recent_contains(self, marker: bytes) -> bool:
        with self.recent_lock:
            return marker in self.recent

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

    def wait_quiet(self, quiet_seconds: float = 0.02, timeout: float = 0.20) -> None:
        deadline = time.monotonic() + timeout
        previous = self.screen.snapshot()
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(min(0.01, quiet_seconds))
            current = self.screen.snapshot()
            if (current.write_seq, current.event_seq) != (previous.write_seq, previous.event_seq):
                previous, stable_since = current, time.monotonic()
            elif time.monotonic() - stable_since >= quiet_seconds:
                return

    def send(self, data: bytes) -> None:
        if self.terminal_error:
            raise RuntimeError(self.terminal_error)
        self.raw_stream.send(data)

    def snapshot(self) -> ScreenSnapshot:
        return self.screen.snapshot()

    def wait_render(self, before: ScreenSnapshot, character: str, sent_ns: int, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observed = (
                self.screen.observed_newline(before)
                if character == "\n"
                else self.screen.observed_at_cursor(before, character)
            )
            if observed is not None and observed >= sent_ns:
                return observed
            if self.terminal_error:
                raise RuntimeError(self.terminal_error)
            if self.exited.is_set():
                raise EOFError("terminal exited while waiting for render")
            time.sleep(0.002)
        raise TimeoutError(
            f"terminal did not render {character!r} at ({before.row},{before.column})"
        )
