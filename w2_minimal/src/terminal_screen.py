from collections import deque
from dataclasses import dataclass
import threading


@dataclass(frozen=True)
class ScreenSnapshot:
    row: int
    column: int
    write_seq: int
    event_seq: int
    linefeed_seq: int
    cell: str


class TerminalScreen:
    """Mô hình màn hình VT100/xterm tối giản để quan sát ô tại con trỏ."""

    # Khởi tạo màn hình ảo và các bộ đếm sự kiện.
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
        self.linefeed_seq = 0
        self.writes = deque(maxlen=8192)
        self.cursor_events = deque(maxlen=8192)
        self.lock = threading.RLock()

    # Chụp vị trí con trỏ và mốc sự kiện hiện tại.
    def snapshot(self) -> ScreenSnapshot:
        with self.lock:
            return ScreenSnapshot(
                self.row,
                self.column,
                self.write_seq,
                self.event_seq,
                self.linefeed_seq,
                self.screen[self.row][self.column],
            )

    # Kiểm tra ký tự mới có được vẽ đúng ở con trỏ đã chụp hay không.
    def observed_at_cursor(self, before: ScreenSnapshot, character: str) -> bool:
        with self.lock:
            for seq, row, column, written in reversed(self.writes):
                if seq <= before.write_seq:
                    break
                if row == before.row and column == before.column and written == character:
                    return True
            return False

    # Kiểm tra Enter có tạo chuyển động từ đúng con trỏ đã chụp hay không.
    def observed_newline(self, before: ScreenSnapshot) -> bool:
        with self.lock:
            for seq, old_row, old_column, new_row, new_column, _kind in reversed(self.cursor_events):
                if seq <= before.event_seq:
                    break
                if (old_row, old_column) == (before.row, before.column):
                    if (new_row, new_column) != (old_row, old_column):
                        return True
            return False

    # Lưu một sự kiện điều khiển làm con trỏ thay đổi.
    def _record_cursor_event(self, old_row: int, old_column: int, kind: str):
        self.event_seq += 1
        if (self.row, self.column) != (old_row, old_column):
            self.cursor_events.append(
                (self.event_seq, old_row, old_column, self.row, self.column, kind)
            )

    # Đưa chuỗi output terminal vào bộ phân tích.
    def feed(self, data: str):
        with self.lock:
            for character in data:
                self._feed_character(character)

    # Xử lý một ký tự theo state machine VT100 tối giản.
    def _feed_character(self, character: str):
        if self.state == "normal":
            if character == "\x1b":
                self.state = "escape"
                self.sequence = ""
            elif character == "\r":
                old_row, old_column = self.row, self.column
                self.column = 0
                self._record_cursor_event(old_row, old_column, "cr")
            elif character in ("\n", "\x0b", "\x0c"):
                old_row, old_column = self.row, self.column
                self._linefeed()
                self._record_cursor_event(old_row, old_column, "lf")
            elif character == "\b":
                old_row, old_column = self.row, self.column
                self.column = max(0, self.column - 1)
                self._record_cursor_event(old_row, old_column, "backspace")
            elif character == "\t":
                old_row, old_column = self.row, self.column
                self.column = min(self.columns - 1, ((self.column // 8) + 1) * 8)
                self._record_cursor_event(old_row, old_column, "tab")
            elif character >= " " and character != "\x7f":
                self._write(character)
            return

        if self.state == "escape":
            if character == "[":
                self.state = "csi"
                self.sequence = ""
            elif character == "]":
                self.state = "osc"
                self.sequence = ""
            elif character in ("P", "^", "_"):
                self.state = "string"
                self.sequence = ""
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

    # Ghi một ký tự hiển thị vào cell hiện tại.
    def _write(self, character: str):
        row, column = self.row, self.column
        self.screen[row][column] = character
        self.write_seq += 1
        self.writes.append((self.write_seq, row, column, character))
        if self.column == self.columns - 1:
            self.column = 0
            self._linefeed()
        else:
            self.column += 1

    # Đi xuống dòng và cuộn vùng hiển thị khi cần.
    def _linefeed(self):
        self.linefeed_seq += 1
        if self.row == self.scroll_bottom:
            del self.screen[self.scroll_top]
            self.screen.insert(self.scroll_bottom, [" " for _ in range(self.columns)])
        else:
            self.row = min(self.rows - 1, self.row + 1)

    # Xử lý escape sequence ngắn không thuộc CSI.
    def _escape_final(self, final: str):
        old_row, old_column = self.row, self.column
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
            if self.row == self.scroll_top:
                self.screen.insert(self.scroll_top, [" " for _ in range(self.columns)])
                del self.screen[self.scroll_bottom + 1]
            else:
                self.row = max(0, self.row - 1)
        elif final == "c":
            self.__init__(self.rows, self.columns)
            return
        self._record_cursor_event(old_row, old_column, f"escape_{final}")

    @staticmethod
    # Chuyển tham số CSI thành danh sách số nguyên.
    def _params(raw: str):
        clean = raw.lstrip("?<>=!")
        clean = clean.split(" ", 1)[0]
        values = []
        for item in clean.split(";") if clean else []:
            try:
                values.append(int(item) if item else 0)
            except ValueError:
                values.append(0)
        return values

    @staticmethod
    # Lấy tham số CSI và thay 0 bằng giá trị mặc định.
    def _value(values, index=0, default=1):
        if index >= len(values) or values[index] == 0:
            return default
        return values[index]

    # Áp dụng lệnh CSI lên màn hình ảo.
    def _csi_final(self, raw: str, final: str):
        values = self._params(raw)
        old_row, old_column = self.row, self.column
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
            self.row = min(self.scroll_bottom, self.row + amount)
            self.column = 0
        elif final == "F":
            self.row = max(self.scroll_top, self.row - amount)
            self.column = 0
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
            self._record_cursor_event(old_row, old_column, f"csi_{final}")

    # Xóa một phần hoặc toàn bộ dòng hiện tại.
    def _erase_line(self, mode: int):
        if mode == 1:
            start, end = 0, self.column + 1
        elif mode == 2:
            start, end = 0, self.columns
        else:
            start, end = self.column, self.columns
        for column in range(start, end):
            self.screen[self.row][column] = " "

    # Xóa một phần hoặc toàn bộ màn hình.
    def _erase_display(self, mode: int):
        if mode in (2, 3):
            for row in range(self.rows):
                self.screen[row] = [" "] * self.columns
            return
        if mode == 1:
            for row in range(0, self.row):
                self.screen[row] = [" "] * self.columns
            for column in range(0, self.column + 1):
                self.screen[self.row][column] = " "
            return
        for column in range(self.column, self.columns):
            self.screen[self.row][column] = " "
        for row in range(self.row + 1, self.rows):
            self.screen[row] = [" "] * self.columns


class TerminalTracker:
    """Reader dạng file cho pexpect, vừa mirror byte vừa cập nhật màn hình ảo."""

    # Tạo tracker kết hợp log tùy chọn và màn hình ảo.
    def __init__(self, mirror=None, rows: int = 24, columns: int = 80, close_mirror: bool = False):
        self.screen = TerminalScreen(rows, columns)
        self.mirror = mirror
        self.close_mirror = close_mirror
        self.recent = ""
        self.recent_lock = threading.RLock()

    # Nhận output từ pexpect, ghi log và cập nhật parser.
    def write(self, data):
        if self.mirror is not None:
            self.mirror.write(data)
            self.mirror.flush()
        self.screen.feed(data)
        with self.recent_lock:
            self.recent = (self.recent + data)[-200000:]

    # Đẩy dữ liệu log xuống tệp.
    def flush(self):
        if self.mirror is not None:
            self.mirror.flush()

    # Trả về cửa sổ raw output gần nhất cho marker audit.
    def recent_text(self):
        with self.recent_lock:
            return self.recent

    # Đóng tệp mirror nếu tracker là chủ sở hữu.
    def close(self):
        if self.mirror is not None and self.close_mirror:
            self.mirror.close()
        self.mirror = None
