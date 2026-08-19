import sys
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from terminal_screen import TerminalScreen


class TerminalScreenTests(unittest.TestCase):
    def test_exact_cursor_cell_is_required(self):
        screen = TerminalScreen(rows=4, columns=10)
        screen.feed("abc\x1b[2;3H", time.perf_counter_ns())
        before = screen.snapshot()
        observed = time.perf_counter_ns()
        screen.feed("Z", observed)
        self.assertEqual(screen.observed_at_cursor(before, "Z"), observed)

    def test_same_character_elsewhere_does_not_match(self):
        screen = TerminalScreen(rows=4, columns=10)
        screen.feed("\x1b[2;3H", time.perf_counter_ns())
        before = screen.snapshot()
        screen.feed("\x1b[1;1Hx", time.perf_counter_ns())
        self.assertIsNone(screen.observed_at_cursor(before, "x"))

    def test_tmux_panes_are_verified_at_distinct_cells(self):
        screen = TerminalScreen(rows=6, columns=20)
        before = screen.snapshot()
        first_ns = time.perf_counter_ns()
        screen.feed("A\x1b[1;11HA", first_ns)
        self.assertEqual(screen.first_matching_write(before.write_seq, 0, 0, "A"), first_ns)
        self.assertEqual(screen.first_matching_write(before.write_seq, 0, 10, "A"), first_ns)


if __name__ == "__main__":
    unittest.main()

