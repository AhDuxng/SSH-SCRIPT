"""Kiểm tra ngữ nghĩa so khớp tệp do trình soạn thảo lưu ra."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = PROJECT_DIR.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(REPO_DIR))
sys.path.append(str(REPO_DIR / "w3-mux-tt" / "src"))

from trial import probe_saved_ok  # noqa: E402


class ProbeSavedOkTests(unittest.TestCase):
    PROBE = b"#include <stdio.h>\nint main(void)\n{\n    return 0;\n}\n"

    # Tệp giống hệt probe thì khớp.
    def test_exact_match(self):
        self.assertTrue(probe_saved_ok(self.PROBE, self.PROBE))

    # Probe kết thúc bằng newline nên buffer có dòng trống cuối; editor ghi
    # thêm một newline cho dòng đó. Đây là ngữ nghĩa editor, vẫn tính khớp.
    def test_single_trailing_newline_accepted(self):
        self.assertTrue(probe_saved_ok(self.PROBE + b"\n", self.PROBE))

    # Hai newline thừa không còn giải thích được bằng ngữ nghĩa editor.
    def test_two_trailing_newlines_rejected(self):
        self.assertFalse(probe_saved_ok(self.PROBE + b"\n\n", self.PROBE))

    # Mất byte hoặc sai nội dung vẫn phải bị bắt.
    def test_truncated_or_corrupt_rejected(self):
        self.assertFalse(probe_saved_ok(self.PROBE[:-1], self.PROBE))
        self.assertFalse(probe_saved_ok(b"x" + self.PROBE, self.PROBE))

    # Không đọc được tệp thì không phải là khớp.
    def test_none_rejected(self):
        self.assertFalse(probe_saved_ok(None, self.PROBE))


if __name__ == "__main__":
    unittest.main()
