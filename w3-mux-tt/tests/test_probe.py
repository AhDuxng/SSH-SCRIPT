import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from probe import ProbeSource


class ProbeTests(unittest.TestCase):
    def test_newline_is_typed_and_numbered(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.txt"
            path.write_text("abc\n", encoding="utf-8")
            probe = ProbeSource(path)
            items = list(probe.items())
        self.assertEqual(probe.text, "abc\n")
        self.assertEqual(probe.data, b"abc\n")
        self.assertEqual(probe.sha256, hashlib.sha256(b"abc\n").hexdigest())
        self.assertEqual(items[-1].token, "\\n")
        self.assertEqual((items[-1].line, items[-1].column), (1, 4))


if __name__ == "__main__":
    unittest.main()
