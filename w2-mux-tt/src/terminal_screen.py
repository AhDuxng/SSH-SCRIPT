"""Mô phỏng phần trạng thái VT100/xterm cần để kiểm tra màn hình Mosh W2."""

from __future__ import annotations

import threading


class TerminalScreen:
    """Áp dụng text và cursor sequence vào một viewport cố định."""

    def __init__(self, rows: int, columns: int):
        self.rows = rows
        self.columns = columns
        self.row = 0
        self.column = 0
        self.saved_cursor = (0, 0)
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.screen = [[" "] * columns for _ in range(rows)]
        self.state = "normal"
        self.sequence = ""
        self.revision = 0
        self.lock = threading.RLock()

    # Áp dụng một chuỗi terminal đã giải mã UTF-8.
    def feed(self, text: str) -> None:
        with self.lock:
            for character in text:
                self._feed_character(character)

    # Trả các hàng đang nhìn thấy, giữ newline để khớp payload chuẩn.
    def visible_lines(self) -> tuple[bytes, ...]:
        with self.lock:
            return tuple(
                ("".join(line).rstrip() + "\n").encode("utf-8")
                for line in self.screen
                if any(character != " " for character in line)
            )

    # Trả phiên bản màn hình để phát hiện trạng thái đã ổn định.
    def current_revision(self) -> int:
        with self.lock:
            return self.revision

    def _changed(self) -> None:
        self.revision += 1

    def _linefeed(self) -> None:
        if self.row == self.scroll_bottom:
            del self.screen[self.scroll_top]
            self.screen.insert(self.scroll_bottom, [" "] * self.columns)
        else:
            self.row = min(self.rows - 1, self.row + 1)
        self._changed()

    def _write(self, character: str) -> None:
        self.screen[self.row][self.column] = character
        self._changed()
        if self.column == self.columns - 1:
            self.column = 0
            self._linefeed()
        else:
            self.column += 1

    def _feed_character(self, character: str) -> None:
        if self.state == "normal":
            if character == "\x1b":
                self.state = "escape"
                self.sequence = ""
            elif character == "\r":
                self.column = 0
                self._changed()
            elif character in ("\n", "\x0b", "\x0c"):
                self._linefeed()
            elif character == "\b":
                self.column = max(0, self.column - 1)
                self._changed()
            elif character == "\t":
                self.column = min(
                    self.columns - 1, ((self.column // 8) + 1) * 8
                )
                self._changed()
            elif character >= " " and character != "\x7f":
                self._write(character)
            return
        if self.state == "escape":
            if character == "[":
                self.state = "csi"
                self.sequence = ""
            elif character == "]":
                self.state = "osc"
            elif character in ("P", "^", "_"):
                self.state = "string"
            elif character in ("(", ")", "*", "+", "-", ".", "/"):
                self.state = "charset"
            else:
                self._escape_final(character)
                self.state = "normal"
            return
        if self.state == "charset":
            self.state = "normal"
            return
        if self.state in ("osc", "string"):
            if character == "\x07":
                self.state = "normal"
            elif character == "\x1b":
                self.state = "string_escape"
            return
        if self.state == "string_escape":
            self.state = "normal" if character == "\\" else "string"
            return
        if self.state == "csi":
            if "@" <= character <= "~":
                self._csi_final(self.sequence, character)
                self.state = "normal"
                self.sequence = ""
            else:
                self.sequence += character

    def _escape_final(self, final: str) -> None:
        if final == "7":
            self.saved_cursor = (self.row, self.column)
        elif final == "8":
            self.row, self.column = self.saved_cursor
        elif final == "D":
            self._linefeed()
        elif final == "E":
            self.column = 0
            self._linefeed()
        elif final == "M":
            self.row = max(self.scroll_top, self.row - 1)
        elif final == "c":
            self.row = self.column = 0
            self.scroll_top, self.scroll_bottom = 0, self.rows - 1
            self.screen = [[" "] * self.columns for _ in range(self.rows)]
        self._changed()

    @staticmethod
    def _params(raw: str) -> list[int]:
        clean = raw.lstrip("?<>=!").split(" ", 1)[0]
        values = []
        for item in clean.split(";") if clean else []:
            try:
                values.append(int(item) if item else 0)
            except ValueError:
                values.append(0)
        return values

    @staticmethod
    def _value(values: list[int], index: int = 0, default: int = 1) -> int:
        return default if index >= len(values) or values[index] == 0 else values[index]

    def _csi_final(self, raw: str, final: str) -> None:
        values = self._params(raw)
        amount = self._value(values)
        if final == "A":
            self.row = max(self.scroll_top, self.row - amount)
        elif final in ("B", "e"):
            self.row = min(self.scroll_bottom, self.row + amount)
        elif final in ("C", "a"):
            self.column = min(self.columns - 1, self.column + amount)
        elif final == "D":
            self.column = max(0, self.column - amount)
        elif final == "E":
            self.row, self.column = min(self.scroll_bottom, self.row + amount), 0
        elif final == "F":
            self.row, self.column = max(self.scroll_top, self.row - amount), 0
        elif final in ("G", "`"):
            self.column = min(self.columns - 1, amount - 1)
        elif final in ("H", "f"):
            self.row = min(self.rows - 1, self._value(values, 0) - 1)
            self.column = min(self.columns - 1, self._value(values, 1) - 1)
        elif final == "d":
            self.row = min(self.rows - 1, amount - 1)
        elif final == "J":
            self._erase_display(values[0] if values else 0)
        elif final == "K":
            self._erase_line(values[0] if values else 0)
        elif final == "X":
            for column in range(self.column, min(self.columns, self.column + amount)):
                self.screen[self.row][column] = " "
        elif final == "@":
            line = self.screen[self.row]
            line[self.column:self.column] = [" "] * amount
            del line[self.columns:]
        elif final == "P":
            line = self.screen[self.row]
            del line[self.column:min(self.columns, self.column + amount)]
            line.extend([" "] * (self.columns - len(line)))
        elif final == "L":
            for _ in range(amount):
                self.screen.insert(self.row, [" "] * self.columns)
                del self.screen[self.scroll_bottom + 1]
        elif final == "M":
            for _ in range(amount):
                del self.screen[self.row]
                self.screen.insert(self.scroll_bottom, [" "] * self.columns)
        elif final == "S":
            for _ in range(amount):
                del self.screen[self.scroll_top]
                self.screen.insert(self.scroll_bottom, [" "] * self.columns)
        elif final == "T":
            for _ in range(amount):
                self.screen.insert(self.scroll_top, [" "] * self.columns)
                del self.screen[self.scroll_bottom + 1]
        elif final == "r":
            top = self._value(values, 0) - 1
            bottom = self._value(values, 1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top, self.scroll_bottom = top, bottom
                self.row, self.column = top, 0
        elif final == "s":
            self.saved_cursor = (self.row, self.column)
        elif final == "u":
            self.row, self.column = self.saved_cursor
        self._changed()

    def _erase_line(self, mode: int) -> None:
        start, end = (0, self.columns) if mode == 2 else (
            (0, self.column + 1) if mode == 1 else (self.column, self.columns)
        )
        for column in range(start, end):
            self.screen[self.row][column] = " "

    def _erase_display(self, mode: int) -> None:
        if mode in (2, 3):
            for row in range(self.rows):
                self.screen[row] = [" "] * self.columns
        elif mode == 1:
            for row in range(self.row):
                self.screen[row] = [" "] * self.columns
            for column in range(self.column + 1):
                self.screen[self.row][column] = " "
        else:
            for column in range(self.column, self.columns):
                self.screen[self.row][column] = " "
            for row in range(self.row + 1, self.rows):
                self.screen[row] = [" "] * self.columns

