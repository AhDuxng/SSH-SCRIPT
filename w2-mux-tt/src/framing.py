"""Tách payload và dấu mốc của lệnh W2 gửi trực tiếp."""

from __future__ import annotations

import re
from dataclasses import dataclass


ANSI_ESCAPE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
START_PATTERN = re.compile(rb"__W2TT_START__:([0-9a-f]{24})")
DONE_PATTERN = re.compile(rb"__W2TT_DONE__:([0-9a-f]{24}):(-?[0-9]+)")


# Tạo dòng Bash chạy cat trực tiếp giữa hai dấu mốc.
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

    # Nhận thêm byte và trả về các sự kiện W2 hoàn chỉnh.
    def feed(self, chunk: bytes) -> list[MarkerEvent]:
        cleaned = ANSI_ESCAPE.sub(b"", chunk).replace(b"\r\n", b"\n")
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
