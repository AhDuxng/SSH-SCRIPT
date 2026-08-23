import importlib.util
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from terminal_screen import TerminalScreen
from trial import _sample_payload_spec, run_stream


def load_analyzer():
    path = PROJECT / "tools" / "analyze_w2.py"
    spec = importlib.util.spec_from_file_location("analyze_w2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TerminalScreenTests(unittest.TestCase):
    def test_cursor_updates_remain_separate_rows(self):
        screen = TerminalScreen(rows=4, columns=12)
        screen.feed("\x1b[1;1HROW_ONE\x1b[2;1HROW_TWO")
        self.assertIn(b"ROW_ONE\n", screen.visible_lines())
        self.assertIn(b"ROW_TWO\n", screen.visible_lines())
        self.assertNotIn(b"ROW_ONEROW_TWO\n", screen.visible_lines())

    def test_clear_removes_previous_sample(self):
        screen = TerminalScreen(rows=3, columns=10)
        screen.feed("old\x1b[2J\x1b[Hnew")
        self.assertEqual(screen.visible_lines(), (b"new\n",))


class CommandVisibleSummaryTests(unittest.TestCase):
    def test_partial_mosh_rows_still_have_visible_latency(self):
        analyzer = load_analyzer()
        common = {
            "protocol": "mosh", "first_byte_latency_ms": "10",
            "throughput_mib_s": "", "content_coverage_pct": "50",
            "raw_byte_ratio_pct": "60", "verified_byte_ratio_pct": "50",
            "expected_bytes": "100", "verified_bytes": "50",
            "bytes_complete": "0", "lines_complete": "0",
            "hash_complete": "0", "output_complete": "0",
            "raw_capture_exact": "0",
        }
        rows = [
            {**common, "status": "partial", "completion_latency_ms": "20",
             "completion_marker_received": "1", "marker_latency_ms": "25"},
            {**common, "status": "timeout", "completion_latency_ms": "30",
             "completion_marker_received": "0", "marker_latency_ms": ""},
        ]
        summary = analyzer.summarize_group(rows)
        self.assertEqual(summary["completed_transfers"], 0)
        self.assertEqual(summary["command_visible_n"], 1)
        self.assertEqual(summary["command_visible_mean_ms"], "25.000")


class SharedMoshSnapshotTests(unittest.TestCase):
    def test_two_roles_verify_one_stable_screen_without_raw_byte_stream(self):
        trial = {
            "run_id": "test", "block_id": 1, "trial_order": 1,
            "trial_id": "mosh_w2-s2_r01", "trial_tag": "o001_test",
            "protocol": "mosh", "scenario": "W2-S2", "stream_count": 2,
        }
        payloads = []
        expected = []
        for index in range(2):
            prefix = f"W2S{index}|"
            line = (prefix.encode() + bytes([65 + index]) * 33 + b"\n")
            payload = {
                "name": f"p{index}.txt", "line_prefix": prefix,
                "bytes": len(line), "lines": 1, "_expected_lines": (line,),
            }
            payloads.append(payload)
            expected.extend(_sample_payload_spec(
                trial, f"output_{index}", 1, payload, f"/tmp/p{index}.txt"
            )["expected_lines"])

        class FakeStream:
            def __init__(self, role):
                self.role = role
                self.stream_id = ""
                self.conversation_id = ""

            def execute(self, request_id, command, line_prefix, timeout):
                return {
                    "stdout": b"", "exit_code": 0, "send_time_ns": 1,
                    "first_byte_time_ns": 2, "last_byte_time_ns": 3,
                    "marker_time_ns": 4, "first_byte_latency_ms": 1.0,
                    "completion_latency_ms": 2.0, "marker_latency_ms": 3.0,
                    "completion_marker_received": True, "timed_out": False,
                    "output_ambiguous": True, "output_truncated": False,
                }

        start = threading.Barrier(2)
        finish = threading.Barrier(2)
        verified = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    run_stream, trial, f"output_{index}", index,
                    FakeStream(f"output_{index}"), payloads[index], "/tmp",
                    start, 1.0, 1, False, 1, False, 2.0,
                    finish, verified, lambda: tuple(expected),
                )
                for index in range(2)
            ]
            results = [future.result()[0][0] for future in futures]
        self.assertTrue(all(row["status"] == "completed" for row in results))
        self.assertTrue(all(row["output_complete"] == 1 for row in results))
        self.assertTrue(all(row["received_bytes"] == 0 for row in results))


if __name__ == "__main__":
    unittest.main()
