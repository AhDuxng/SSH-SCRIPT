import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path[:0] = [str(ROOT), str(SRC)]

from probe import ProbeItem
from trial import (
    Pane, measure_mosh, mosh_spec, pane_contains_cursor, wait_tmux_layout,
)


class FakeEndpoint:
    def recent_text(self):
        return (
            "\x1b[2J__W3_LAYOUT__\r\n"
            "0|0|0|79|23|1|0|vim\r\n"
            "1|80|0|80|23|0|0|vim\r\n"
            "2|0|24|79|24|0|0|vim\r\n"
            "3|80|24|80|24|0|0|vim\r\n"
            "__W3_LAYOUT_END__"
        )


class FakeMeasuredEndpoint:
    def __init__(self):
        self.raw_stream = SimpleNamespace(stream_id="", conversation_id="")
        self.screen = SimpleNamespace(clear_history=lambda: None)
        self.current_pane = 0

    def snapshot(self):
        return SimpleNamespace(row=0, column=0, write_seq=0, event_seq=0)

    def send(self, _data):
        return None

    def wait_render(self, _before, _character, sent_ns, _timeout):
        return sent_ns + (self.current_pane + 1) * 1_000_000


class MoshLayoutTests(unittest.TestCase):
    def test_layout_is_parsed_from_same_terminal_output(self):
        panes = wait_tmux_layout(
            FakeEndpoint(), {"MOSH_LAYOUT_QUERY_TIMEOUT_SECONDS": "0.1"},
            "session", [f"interactive_{index}" for index in range(4)],
        )
        self.assertEqual(
            [(pane.role, pane.left, pane.top, pane.active) for pane in panes],
            [
                ("interactive_0", 0, 0, True),
                ("interactive_1", 80, 0, False),
                ("interactive_2", 0, 24, False),
                ("interactive_3", 80, 24, False),
            ],
        )

    def test_cursor_membership_is_checked_per_pane(self):
        pane = Pane("interactive_1", 1, 80, 0, 80, 23, False)
        self.assertTrue(pane_contains_cursor(
            pane, SimpleNamespace(row=4, column=90),
        ))
        self.assertFalse(pane_contains_cursor(
            pane, SimpleNamespace(row=4, column=20),
        ))

    def test_mosh_tmux_disables_broadcast_for_independent_measurement(self):
        trial = {
            "trial_tag": "test", "editor": "vim",
        }
        specs, _, _ = mosh_spec(
            {"TERMINAL_COLUMNS": "160", "TERMINAL_ROWS": "48"},
            trial, ["interactive_0", "interactive_1"],
        )
        command = specs[0].remote_command
        self.assertIn("synchronize-panes off", command)
        self.assertNotIn("synchronize-panes on", command)
        self.assertIn("-L w3_test_socket -f /dev/null", command)
        self.assertIn("bind-key -n F5 select-pane", command)
        self.assertIn("bind-key -n F6 select-pane", command)

    def test_each_mosh_pane_gets_an_independent_timing_sample(self):
        endpoint = FakeMeasuredEndpoint()
        panes = [
            Pane("interactive_0", 0, 0, 0, 80, 23, True),
            Pane("interactive_1", 1, 80, 0, 80, 23, False),
        ]
        probe = SimpleNamespace(items=lambda: iter([
            ProbeItem("A", 1, 1, 1, 1),
        ]))
        trial = {
            "run_id": "run", "block_id": 1, "trial_order": 1,
            "trial_id": "trial", "trial_tag": "tag", "protocol": "mosh",
            "editor": "vim", "scenario": "W3-I2", "stream_count": 2,
        }

        def select(_endpoint, _cfg, _session, pane):
            endpoint.current_pane = pane.index

        with patch("trial.select_mosh_pane", side_effect=select), patch(
            "trial.time.perf_counter_ns", side_effect=[1_000_000_000, 2_000_000_000]
        ), patch("trial.time.sleep"):
            rows = measure_mosh(
                {"KEY_INTERVAL_SECONDS": "0", "LIVE_PROGRESS": "0"},
                trial, endpoint, panes,
                "session", probe,
            )

        self.assertEqual([row["stream_role"] for row in rows], [
            "interactive_0", "interactive_1",
        ])
        self.assertEqual([row["send_ns"] for row in rows], [
            1_000_000_000, 2_000_000_000,
        ])
        self.assertEqual([row["latency_ms"] for row in rows], ["1.000", "2.000"])
        self.assertTrue(all(
            row["measurement_mode"] == "local_prediction_selected_pane"
            for row in rows
        ))
        self.assertTrue(all(
            row["render_verification"] == "tmux_selected_pane_vt100_cursor_cell"
            for row in rows
        ))


if __name__ == "__main__":
    unittest.main()
