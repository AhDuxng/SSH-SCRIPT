"""Tách dấu mốc khỏi output của lệnh gửi trực tiếp."""

from __future__ import annotations

import re
from dataclasses import dataclass


START_PREFIX = b"__W1TT_START__:"
DONE_PREFIX = b"__W1TT_DONE__:"
ANSI_ESCAPE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
START_PATTERN = re.compile(rb"__W1TT_START__:([0-9a-f]{24})")
DONE_PATTERN = re.compile(rb"__W1TT_DONE__:([0-9a-f]{24}):(-?[0-9]+)")


# Tạo dòng Bash chứa lệnh thật và hai dấu mốc đo.
def build_direct_line(command: str, token: str, background: bool) -> bytes:
    body = (
        "printf '\\n__W1TT_%s__:%s\\n' START '" + token + "'; "
        "{ " + command + "; } 2>&1; "
        "__w1tt_rc=$?; "
        "printf '\\n__W1TT_%s__:%s:%s\\n' DONE '" + token
        + "' \"$__w1tt_rc\""
    )
    if background:
        body = "( " + body + " ) &"
    return (body + "\n").encode("utf-8")


@dataclass(frozen=True)
class MarkerEvent:
    """Biểu diễn dấu bắt đầu, output hoặc dấu hoàn thành."""

    kind: str
    token: str = ""
    exit_code: int | None = None
    data: bytes = b""


@dataclass
class MarkerDecoder:
    """Tách từng dòng terminal và nhận diện dấu mốc W1 trực tiếp."""

    pending: bytes = b""

    # Nhận thêm byte và trả về các sự kiện hoàn chỉnh.
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
