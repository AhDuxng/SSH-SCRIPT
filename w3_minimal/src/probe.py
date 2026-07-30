from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeItem:
    character: str
    char_index: int
    char_total: int
    source_offset: int
    source_char_total: int
    line: int
    column: int


class ProbeSource:
    """Tạo một lượt đánh số cho từng ký tự, gồm cả ký tự xuống dòng."""

    # Chuẩn hóa newline và đếm tổng ký tự nguồn.
    def __init__(self, text: str):
        self.text = text.replace("\r\n", "\n").replace("\r", "\n")
        if not self.text:
            raise ValueError("PROBE_TEXT_FILE must not be empty")
        self.source_total = len(self.text)

    # Phát lần lượt từng ký tự kèm số thứ tự và vị trí dòng/cột.
    def items(self):
        line = 1
        column = 0
        for index, character in enumerate(self.text, start=1):
            column += 1
            yield ProbeItem(
                character=character,
                char_index=index,
                char_total=self.source_total,
                source_offset=index,
                source_char_total=self.source_total,
                line=line,
                column=column,
            )
            if character == "\n":
                line += 1
                column = 0
