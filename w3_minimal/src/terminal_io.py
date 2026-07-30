import time

import pexpect

from terminal_screen import TerminalTracker


# Xóa phần output mà một lệnh expect trước đó đã đọc vào buffer nội bộ.
def clear_expect_buffer(child):
    # read_nonblocking() chỉ đọc PTY; nó không xóa phần dư mà expect() đã
    # giữ lại sau một lần match. Pexpect 4.9 dùng cả _buffer để tìm kiếm và
    # _before làm bản đệm đầy đủ khi khởi tạo lần expect tiếp theo.
    child.buffer = child.string_type()
    child._before = child.buffer_type()


# Loại bỏ cả buffer nội bộ và output đang chờ trên PTY trước khi bắt đầu đo.
def drain_pending_output(child, max_reads: int = 64):
    clear_expect_buffer(child)
    try:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
    finally:
        # EOF hoặc một lần read có thể thay đổi trạng thái của pexpect, nhưng
        # không output nào có trước mốc start được phép tham gia lần match mới.
        clear_expect_buffer(child)


# Gửi một ký tự và đo đến khi ký tự được vẽ tại vị trí con trỏ hiện tại.
def probe_once_ms(child, probe: str, timeout: float, tracker: TerminalTracker):
    drain_pending_output(child)
    before = tracker.screen.snapshot()
    start_ns = time.perf_counter_ns()
    child.send("\r" if probe == "\n" else probe)
    deadline = time.monotonic() + timeout
    while True:
        observed = (
            tracker.screen.observed_newline(before)
            if probe == "\n"
            else tracker.screen.observed_at_cursor(before, probe)
        )
        if observed:
            end_ns = time.perf_counter_ns()
            return (end_ns - start_ns) / 1_000_000.0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise pexpect.TIMEOUT(
                f"terminal did not draw {probe!r} at cursor ({before.row},{before.column})"
            )
        try:
            child.read_nonblocking(size=4096, timeout=min(0.05, remaining))
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            raise pexpect.EOF("connection closed while waiting for terminal update")
