import sys
import unittest
from pathlib import Path

import pexpect


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from terminal_io import drain_pending_output, probe_once_ms
from terminal_screen import TerminalScreen, TerminalTracker


class TerminalIOTests(unittest.TestCase):
    def test_drain_removes_pexpect_internal_buffer(self):
        child = pexpect.spawn(
            "/bin/bash",
            ["-c", "printf Axxxxx; sleep 1"],
            encoding="utf-8",
            timeout=2,
        )
        self.addCleanup(child.close, True)
        child.expect_exact("A")
        self.assertEqual(child.buffer, "xxxxx")

        drain_pending_output(child)

        self.assertEqual(child.buffer, "")

    def test_probe_cannot_match_stale_fifth_copy(self):
        child = pexpect.spawn(
            "/bin/bash",
            [
                "-c",
                "stty -echo; printf Axxxxx; read -r -n 1 ch; "
                "sleep 0.15; printf x",
            ],
            encoding="utf-8",
            timeout=2,
        )
        self.addCleanup(child.close, True)
        child.delaybeforesend = 0
        tracker = TerminalTracker()
        child.logfile_read = tracker
        child.expect_exact("A")
        self.assertEqual(child.buffer, "xxxxx")

        measured_ms = probe_once_ms(child, "x", 2, tracker)

        self.assertGreaterEqual(measured_ms, 100)

    def test_screen_tracks_cursor_position_through_ansi(self):
        screen = TerminalScreen(rows=4, columns=10)
        screen.feed("abc\x1b[2;3H")
        before = screen.snapshot()
        screen.feed("Z")

        self.assertEqual((before.row, before.column), (1, 2))
        self.assertTrue(screen.observed_at_cursor(before, "Z"))

    def test_screen_does_not_accept_same_character_elsewhere(self):
        screen = TerminalScreen(rows=4, columns=10)
        screen.feed("\x1b[2;3H")
        before = screen.snapshot()
        screen.feed("\x1b[1;1Hx")

        self.assertFalse(screen.observed_at_cursor(before, "x"))

    def test_screen_observes_newline_cursor_change(self):
        screen = TerminalScreen(rows=4, columns=10)
        screen.feed("abc")
        before = screen.snapshot()
        screen.feed("\r\n")

        self.assertTrue(screen.observed_newline(before))


if __name__ == "__main__":
    unittest.main()
