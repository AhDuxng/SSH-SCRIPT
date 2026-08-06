import re

import pexpect


ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z]|\x1b[()][0-9A-Za-z])"
ECHO_GAP = rf"(?:{ANSI_SEQ}|[\r\n\b])*"
INITIAL_PROMPT_RE = re.compile(r"[#$>](?:" + ANSI_SEQ + r"|\s)*\s*$", re.MULTILINE)


# Tạo regex cho literal có thể bị ANSI/redraw xen giữa từng ký tự.
def gapped_literal(value):
    return "".join(re.escape(char) + ECHO_GAP for char in value)


# Tạo regex prompt chịu được ANSI/redraw của terminal.
def prompt_pattern(marker):
    return re.compile(gapped_literal(marker) + rf"(?:{ANSI_SEQ}|\s)*")


# Loại dữ liệu đang chờ trong cả buffer pexpect và PTY.
def drain_pending_output(child):
    child.buffer = child.string_type()
    child._before = child.buffer_type()
    try:
        for _ in range(32):
            try:
                child.read_nonblocking(size=65536, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
    finally:
        child.buffer = child.string_type()
        child._before = child.buffer_type()


# Khôi phục chuỗi chữ số từ match có ANSI/redraw xen giữa.
def clean_digits(value):
    return re.sub(r"\D", "", re.sub(ANSI_SEQ, "", value))
