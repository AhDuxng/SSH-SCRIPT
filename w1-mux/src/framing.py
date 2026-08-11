"""Đóng khung dữ liệu riêng cho W1."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass


FRAME_PREFIX = b"MUX1 "
PROTOCOL_VERSION = 1
ANSI_ESCAPE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")


# Mã hóa một frame theo giao thức W1.
def encode_frame(payload: dict) -> bytes:
    document = {"version": PROTOCOL_VERSION, **payload}
    return FRAME_PREFIX + json.dumps(
        document, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"


# Giải mã dữ liệu Base64 về dạng nhị phân.
def decode_bytes(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


@dataclass
class FrameDecoder:
    """Tách dần frame W1 khỏi output có nhiễu terminal."""

    pending: bytes = b""

    # Nhận thêm dữ liệu và trả về các frame hoàn chỉnh.
    def feed(self, chunk: bytes) -> list[dict]:
        self.pending += chunk.replace(b"\r\n", b"\n")
        frames = []
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            marker = line.find(FRAME_PREFIX)
            if marker < 0:
                continue
            raw = ANSI_ESCAPE.sub(b"", line[marker + len(FRAME_PREFIX):])
            try:
                frame = json.loads(raw.decode("ascii"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if frame.get("version") == PROTOCOL_VERSION:
                frames.append(frame)
        return frames
