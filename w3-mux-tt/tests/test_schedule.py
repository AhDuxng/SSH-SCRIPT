import sys
import unittest
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from run_w3 import build_schedule


class ScheduleTests(unittest.TestCase):
    def test_complete_randomized_blocks(self):
        schedule = build_schedule(
            ["ssh", "ssh3", "mosh"], ["vim", "nano"],
            ["W3-I1", "W3-I2", "W3-I4"], 2, 123, "run",
        )
        self.assertEqual(len(schedule), 36)
        counts = Counter((row["protocol"], row["editor"], row["scenario"]) for row in schedule)
        self.assertEqual(set(counts.values()), {2})
        self.assertEqual({row["stream_count"] for row in schedule if row["scenario"] == "W3-I4"}, {4})


if __name__ == "__main__":
    unittest.main()

