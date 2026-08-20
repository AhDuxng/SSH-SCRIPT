"""Tách payload và dấu mốc của lệnh W2 gửi trực tiếp."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


START_PATTERN = re.compile(rb"__W2TT_START__:([0-9a-f]{24})")
DONE_PATTERN = re.compile(rb"__W2TT_DONE__:([0-9a-f]{24}):(-?[0-9]+)")


@dataclass
class TerminalControlFilter:
    """Loại control sequence kể cả khi bị chia giữa hai read chunk."""

    state: str = "text"

    def feed(self, chunk: bytes) -> bytes:
        output = bytearray()
        for value in chunk:
            if self.state == "text":
                if value == 0x1B:
                    self.state = "escape"
                elif value in (0x0A, 0x0D) or value >= 0x20:
                    output.append(value)
                continue
            if self.state == "escape":
                if value == ord("["):
                    self.state = "csi"
                elif value in (ord("]"), ord("P"), ord("X"), ord("^"), ord("_")):
                    self.state = "string"
                else:
                    self.state = "text"
                continue
            if self.state == "csi":
                if 0x40 <= value <= 0x7E:
                    self.state = "text"
                continue
            if self.state == "string":
                if value == 0x07:
                    self.state = "text"
                elif value == 0x1B:
                    self.state = "string_escape"
                continue
            if self.state == "string_escape":
                self.state = "text" if value == ord("\\") else "string"
        return bytes(output)


# Tạo token duy nhất và ổn định cho một yêu cầu W2.
def request_token(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]


# Tạo dòng Bash chạy lệnh output trực tiếp giữa hai dấu mốc.
def build_direct_line(command: str, token: str, background: bool) -> bytes:
    body = (
        "printf '__W2TT_%s__:%s\\n' START '" + token + "'; "
        "{ " + command + "; } 2>&1; "
        "__w2tt_rc=$?; "
        "printf '__W2TT_%s__:%s:%s\\n' DONE '" + token
        + "' \"$__w2tt_rc\""
    )
    if background:
        body = "( " + body + " ) &"
    return (body + "\n").encode("utf-8")


@dataclass(frozen=True)
class MarkerEvent:
    """Biểu diễn dấu bắt đầu, một dòng payload hoặc dấu hoàn thành."""

    kind: str
    token: str = ""
    exit_code: int | None = None
    data: bytes = b""


@dataclass
class MarkerDecoder:
    """Tách từng dòng và loại mã điều khiển terminal của Mosh."""

    pending: bytes = b""
    controls: TerminalControlFilter = field(default_factory=TerminalControlFilter)

    # Nhận thêm byte và trả về các sự kiện W2 hoàn chỉnh.
    def feed(self, chunk: bytes) -> list[MarkerEvent]:
        cleaned = self.controls.feed(chunk).replace(b"\r\n", b"\n")
        cleaned = cleaned.replace(b"\r", b"\n")
        self.pending += cleaned
        events = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            start = START_PATTERN.search(line)
            if start:
                events.append(MarkerEvent("start", start.group(1).decode("ascii")))
                continue
            done = DONE_PATTERN.search(line)
            if done:
                events.append(MarkerEvent(
                    "done",
                    done.group(1).decode("ascii"),
                    int(done.group(2)),
                ))
                continue
            events.append(MarkerEvent("output", data=line + b"\n"))
        return events
