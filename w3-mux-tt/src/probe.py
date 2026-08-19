"""Nạp probe text xác định và đánh số từng ký tự."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProbeItem:
    character: str
    index: int
    total: int
    line: int
    column: int

    @property
    def token(self) -> str:
        if self.character == "\n":
            return "\\n"
        if self.character == "\t":
            return "\\t"
        return self.character


class ProbeSource:
    """Probe UTF-8, giữ và đánh số cả ký tự newline như W3 minimal."""

    def __init__(self, path: str | Path):
        text = Path(path).read_text(encoding="utf-8")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            raise ValueError("probe text must not be empty")
        if any(
            (ord(character) < 32 and character != "\n") or ord(character) == 127
            for character in text
        ):
            raise ValueError("W3 probe only supports printable characters and newline")
        self.text = text
        self.data = text.encode("utf-8")
        self.sha256 = hashlib.sha256(self.data).hexdigest()

    def items(self):
        total = len(self.text)
        line = 1
        column = 0
        for index, character in enumerate(self.text, start=1):
            column += 1
            yield ProbeItem(character, index, total, line, column)
            if character == "\n":
                line += 1
                column = 0
