"""Parser VT100/xterm tối giản dùng để xác nhận ký tự tại vị trí con trỏ."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ScreenSnapshot:
    row: int
    column: int
    write_seq: int
    event_seq: int


class TerminalScreen:
    def __init__(self, rows: int = 24, columns: int = 80):
        self.rows = rows
        self.columns = columns
        self.row = 0
        self.column = 0
        self.saved_cursor = (0, 0)
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.screen = [[" " for _ in range(columns)] for _ in range(rows)]
        self.state = "normal"
        self.sequence = ""
        self.write_seq = 0
        self.event_seq = 0
        self.writes = deque(maxlen=65536)
        self.cursor_events = deque(maxlen=65536)
        self.lock = threading.RLock()

    def snapshot(self) -> ScreenSnapshot:
        with self.lock:
            return ScreenSnapshot(self.row, self.column, self.write_seq, self.event_seq)

    def clear_history(self) -> None:
        with self.lock:
            self.writes.clear()
            self.cursor_events.clear()

    def first_matching_write(self, after_seq: int, row: int, column: int, character: str):
        with self.lock:
            for seq, item_row, item_column, written, observed_ns in self.writes:
                if seq > after_seq and item_row == row and item_column == column and written == character:
                    return observed_ns
        return None

    def observed_at_cursor(self, before: ScreenSnapshot, character: str):
        return self.first_matching_write(
            before.write_seq, before.row, before.column, character
        )

    def observed_newline(self, before: ScreenSnapshot):
        with self.lock:
            for seq, old_row, old_column, new_row, new_column, observed_ns in self.cursor_events:
                if seq > before.event_seq and (old_row, old_column) == (before.row, before.column):
                    if (new_row, new_column) != (old_row, old_column):
                        return observed_ns
        return None

    def feed(self, data: str, observed_ns: int) -> None:
        with self.lock:
            for character in data:
                self._feed_character(character, observed_ns)

    def _record_cursor(self, old_row: int, old_column: int, observed_ns: int):
        self.event_seq += 1
        if (self.row, self.column) != (old_row, old_column):
            self.cursor_events.append(
                (self.event_seq, old_row, old_column, self.row, self.column, observed_ns)
            )

    def _write(self, character: str, observed_ns: int):
        row, column = self.row, self.column
        self.screen[row][column] = character
        self.write_seq += 1
        self.writes.append((self.write_seq, row, column, character, observed_ns))
        if self.column == self.columns - 1:
            self.column = 0
            self._linefeed()
        else:
            self.column += 1

    def _linefeed(self):
        if self.row == self.scroll_bottom:
            del self.screen[self.scroll_top]
            self.screen.insert(self.scroll_bottom, [" "] * self.columns)
        else:
            self.row = min(self.rows - 1, self.row + 1)

    def _feed_character(self, character: str, observed_ns: int):
        if self.state == "normal":
            if character == "\x1b":
                self.state = "escape"
                self.sequence = ""
            elif character == "\r":
                old = (self.row, self.column)
                self.column = 0
                self._record_cursor(*old, observed_ns)
            elif character in ("\n", "\x0b", "\x0c"):
                old = (self.row, self.column)
                self._linefeed()
                self._record_cursor(*old, observed_ns)
            elif character == "\b":
                old = (self.row, self.column)
                self.column = max(0, self.column - 1)
                self._record_cursor(*old, observed_ns)
            elif character == "\t":
                old = (self.row, self.column)
                self.column = min(self.columns - 1, ((self.column // 8) + 1) * 8)
                self._record_cursor(*old, observed_ns)
            elif character >= " " and character != "\x7f":
                self._write(character, observed_ns)
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
                self._escape_final(character, observed_ns)
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
                self._csi_final(self.sequence, character, observed_ns)
                self.state = "normal"
                self.sequence = ""
            else:
                self.sequence += character

    def _escape_final(self, final: str, observed_ns: int):
        old = (self.row, self.column)
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
            rows, columns = self.rows, self.columns
            self.__init__(rows, columns)
            return
        self._record_cursor(*old, observed_ns)

    @staticmethod
    def _params(raw: str):
        clean = raw.lstrip("?<>=!").split(" ", 1)[0]
        values = []
        for item in clean.split(";") if clean else []:
            try:
                values.append(int(item) if item else 0)
            except ValueError:
                values.append(0)
        return values

    @staticmethod
    def _value(values, index=0, default=1):
        return default if index >= len(values) or values[index] == 0 else values[index]

    def _csi_final(self, raw: str, final: str, observed_ns: int):
        values = self._params(raw)
        old = (self.row, self.column)
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
        if final not in ("m", "h", "l", "n", "c"):
            self._record_cursor(*old, observed_ns)

    def _erase_line(self, mode: int):
        start, end = (0, self.columns) if mode == 2 else (
            (0, self.column + 1) if mode == 1 else (self.column, self.columns)
        )
        for column in range(start, end):
            self.screen[self.row][column] = " "

    def _erase_display(self, mode: int):
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
