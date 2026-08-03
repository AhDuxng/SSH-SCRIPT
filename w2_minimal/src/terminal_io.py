import re
import time

import pexpect


ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z]|\x1b[()][0-9A-Za-z])"
ANSI_RE = re.compile(ANSI_SEQ)
INITIAL_PROMPT_RE = re.compile(r"[#$>](?:" + ANSI_SEQ + r"|\s)*\s*$", re.MULTILINE)


# Xóa output còn lại trong buffer nội bộ của pexpect.
def clear_expect_buffer(child):
    child.buffer = child.string_type()
    child._before = child.buffer_type()


# Loại cả buffer pexpect và dữ liệu PTY đang chờ trước một phép đo mới.
def drain_pending_output(child):
    clear_expect_buffer(child)
    try:
        for _ in range(32):
            try:
                child.read_nonblocking(size=65536, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
    finally:
        clear_expect_buffer(child)


# Tạo regex prompt chịu được ANSI redraw và byte điều khiển xen giữa ký tự.
def prompt_pattern(marker):
    gap = rf"(?:{ANSI_SEQ}|[\r\n\b])*"
    return re.compile("".join(re.escape(ch) + gap for ch in marker) + rf"(?:{ANSI_SEQ}|\s)*")


# Loại ANSI, carriage return và backspace khỏi output hiển thị.
def clean_terminal_text(text):
    return ANSI_RE.sub("", text).replace("\r", "").replace("\b", "")


# Đọc stream tăng dần đến marker, không giữ toàn bộ output lớn trong bộ nhớ.
def wait_for_marker(child, marker, sample_timeout, idle_timeout, max_read):
    deadline = time.monotonic() + sample_timeout
    marker_re = re.compile(re.escape(marker) + r" exit_code=(\d+)")
    buffer = ""
    pending_line = ""
    debug_tail = ""
    output_bytes = 0
    output_lines = 0
    saw_activity = False
    last_data_at = time.monotonic()

    # Tạo lỗi timeout kèm số byte và phần đuôi output đã quan sát.
    def timeout_error(reason):
        return pexpect.TIMEOUT(
            f"{reason}; marker={marker!r}; output_bytes={output_bytes}; "
            f"clean_tail={debug_tail[-500:]!r}"
        )

    while True:
        now = time.monotonic()
        if now >= deadline:
            raise timeout_error(f"sample timeout after {sample_timeout:.1f}s")
        if idle_timeout > 0 and saw_activity and now - last_data_at >= idle_timeout:
            raise timeout_error(f"no output for {idle_timeout:.1f}s")

        read_timeout = min(0.5, max(0.05, deadline - now))
        if idle_timeout > 0 and saw_activity:
            read_timeout = min(read_timeout, max(0.05, idle_timeout - (now - last_data_at)))
        try:
            chunk = child.read_nonblocking(size=max_read, timeout=read_timeout)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF as exc:
            raise pexpect.EOF(
                f"EOF before marker {marker!r}; output_bytes={output_bytes}; "
                f"clean_tail={debug_tail[-500:]!r}"
            ) from exc

        if not chunk:
            continue
        saw_activity = True
        last_data_at = time.monotonic()
        debug_tail = (debug_tail + clean_terminal_text(chunk))[-500:]
        # Giữ raw text đến hết dòng để ANSI sequence bị chia chunk vẫn được nối lại.
        buffer += chunk
        clean_buffer = clean_terminal_text(buffer)

        match = marker_re.search(clean_buffer)
        if match is not None:
            prefix = pending_line + clean_buffer[:match.start()]
            # Bỏ đúng newline phân cách do wrapper thêm trước marker.
            if prefix.endswith("\n"):
                prefix = prefix[:-1]
            output_bytes += len(prefix.encode("utf-8", errors="replace"))
            output_lines += prefix.count("\n")
            return output_bytes, output_lines, int(match.group(1))

        # Chỉ giữ phần dòng cuối để marker bị chia chunk vẫn có thể match.
        lines = buffer.split("\n")
        buffer = lines.pop() if lines else ""
        for line in lines:
            cleaned_line = clean_terminal_text(line + "\n")
            # Hoãn một dòng để phân biệt output cuối với newline ngăn marker.
            if pending_line:
                output_bytes += len(pending_line.encode("utf-8", errors="replace"))
                output_lines += pending_line.count("\n")
            pending_line = cleaned_line

        # Giới hạn dòng cực dài để phép cat không làm tăng bộ nhớ vô hạn.
        if len(buffer) > 262144:
            dropped = buffer[:-262144]
            cleaned_dropped = clean_terminal_text(dropped)
            output_bytes += len(cleaned_dropped.encode("utf-8", errors="replace"))
            output_lines += cleaned_dropped.count("\n")
            buffer = buffer[-262144:]
