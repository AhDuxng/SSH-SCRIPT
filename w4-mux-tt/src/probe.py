"""Load and index the deterministic 100-character W4 editing probe."""

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
        return "\\n" if self.character == "\n" else self.character


class ProbeSource:
    def __init__(self, path: str | Path):
        text = Path(path).read_text(encoding="utf-8")
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not self.text:
            raise ValueError("probe text must not be empty")
        self.data = self.text.encode("utf-8")
        self.sha256 = hashlib.sha256(self.data).hexdigest()

    def items(self):
        line, column = 1, 0
        for index, character in enumerate(self.text, start=1):
            column += 1
            yield ProbeItem(character, index, len(self.text), line, column)
            if character == "\n":
                line, column = line + 1, 0
