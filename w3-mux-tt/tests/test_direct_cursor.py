import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path[:0] = [str(ROOT), str(SRC)]

from trial import editor_origin, synchronize_direct_editors


class FakeEndpoint:
    def __init__(self, editor, recover_after_refresh=1):
        self.editor = editor
        self.recover_after_refresh = recover_after_refresh
        self.refreshes = 0
        self.terminal_error = ""
        self.exited = SimpleNamespace(is_set=lambda: False)

    def send(self, data):
        expected = b"\x1bgg0\x0ci" if self.editor == "vim" else b"\x0c\x01"
        if data == expected:
            self.refreshes += 1

    def snapshot(self):
        if self.refreshes >= self.recover_after_refresh:
            row, column = editor_origin(self.editor)
        else:
            # Reproduce the stale Nano status-bar cursor observed in pi_runs.
            row, column = 46, 112
        return SimpleNamespace(
            row=row, column=column, write_seq=self.refreshes,
            event_seq=self.refreshes,
        )


class DirectCursorTests(unittest.TestCase):
    def test_editor_origins_match_empty_vim_and_nano_buffers(self):
        self.assertEqual(editor_origin("vim"), (0, 0))
        self.assertEqual(editor_origin("nano"), (1, 0))

    def test_all_streams_must_hold_a_stable_origin(self):
        endpoints = {
            "interactive_0": FakeEndpoint("vim"),
            "interactive_1": FakeEndpoint("vim"),
        }
        snapshots = synchronize_direct_editors(
            {
                "EDITOR_CURSOR_READY_TIMEOUT_SECONDS": "0.05",
                "EDITOR_CURSOR_STABLE_SECONDS": "0.01",
                "EDITOR_CURSOR_REFRESH_RETRIES": "0",
                "LIVE_PROGRESS": "0",
            },
            "vim", list(endpoints), endpoints,
        )
        self.assertEqual(
            {(snapshot.row, snapshot.column) for snapshot in snapshots.values()},
            {(0, 0)},
        )

    def test_status_bar_cursor_triggers_an_unmeasured_repaint_retry(self):
        endpoint = FakeEndpoint("nano", recover_after_refresh=2)
        snapshots = synchronize_direct_editors(
            {
                "EDITOR_CURSOR_READY_TIMEOUT_SECONDS": "0.025",
                "EDITOR_CURSOR_STABLE_SECONDS": "0.005",
                "EDITOR_CURSOR_REFRESH_RETRIES": "1",
                "LIVE_PROGRESS": "0",
            },
            "nano", ["interactive_0"], {"interactive_0": endpoint},
        )
        self.assertEqual(endpoint.refreshes, 2)
        self.assertEqual(
            (snapshots["interactive_0"].row, snapshots["interactive_0"].column),
            (1, 0),
        )


if __name__ == "__main__":
    unittest.main()
