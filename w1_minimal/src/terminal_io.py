import re

import pexpect


ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
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

