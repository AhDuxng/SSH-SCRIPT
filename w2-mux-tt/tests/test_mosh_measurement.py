import importlib.util
import sys
import threading
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT.parent), str(PROJECT / "src")]

from framing import MarkerEvent, request_token
from stream_adapter import DirectCoordinator, DirectW2Connection
from stream_mux import RawStream
from terminal_screen import TerminalScreen
from trial import _sample_payload_spec, run_stream


def load_analyzer():
    path = PROJECT / "tools" / "analyze_w2.py"
    spec = importlib.util.spec_from_file_location("analyze_w2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_coordinator(screen=None):
    return DirectCoordinator(
        RawStream("terminal", lambda _data: None),
        background=False,
        max_capture_bytes=1 << 20,
        screen=screen,
    )


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

    def test_prefix_scan_only_returns_matching_rows(self):
        screen = TerminalScreen(rows=4, columns=20)
        screen.feed("\x1b[1;1HW2S0|abcPAYLOAD\x1b[2;1Hnoise\x1b[3;1HW2S1|xyzOTHER")
        rows = screen.rows_with_prefixes(("W2S0|abc",))
        self.assertEqual(rows, [(0, "W2S0|abc", b"W2S0|abcPAYLOAD\n")])

    def test_prefix_scan_finds_every_tracked_prefix(self):
        screen = TerminalScreen(rows=4, columns=20)
        screen.feed("\x1b[1;1HW2S0|aaaONE\x1b[2;1HW2S1|bbbTWO")
        found = {
            prefix for _row, prefix, _line
            in screen.rows_with_prefixes(("W2S0|aaa", "W2S1|bbb"))
        }
        self.assertEqual(found, {"W2S0|aaa", "W2S1|bbb"})


class ContentCompletionTests(unittest.TestCase):
    """Thời điểm nội dung đủ phải đo được trên cả byte stream lẫn screen state."""

    def test_byte_stream_records_time_of_last_missing_line(self):
        coordinator = make_coordinator()
        token = request_token("trial:output_0:1")
        prefix = b"W2S0|" + token.encode()
        expected = (prefix + b"AAA\n", prefix + b"BBB\n")
        transfer = coordinator._register("trial:output_0:1", prefix, expected)
        coordinator.feed(MarkerEvent("start", token), 1, 1)

        coordinator.feed_bytes(expected[0], 10, 10)
        self.assertEqual(transfer.unique_matched, 1)
        self.assertEqual(transfer.content_complete_mono_ns, 0)

        coordinator.feed_bytes(expected[1], 20, 20)
        self.assertEqual(transfer.unique_matched, 2)
        self.assertEqual(transfer.content_complete_mono_ns, 20)

    def test_repeated_line_does_not_advance_completion_time(self):
        coordinator = make_coordinator()
        token = request_token("trial:output_0:2")
        prefix = b"W2S0|" + token.encode()
        expected = (prefix + b"AAA\n",)
        transfer = coordinator._register("trial:output_0:2", prefix, expected)
        coordinator.feed(MarkerEvent("start", token), 1, 1)

        coordinator.feed_bytes(expected[0], 10, 10)
        coordinator.feed_bytes(expected[0], 30, 30)
        self.assertEqual(transfer.content_complete_mono_ns, 10)
        self.assertEqual(transfer.duplicate_count, 1)

    def test_screen_state_completion_without_lossless_byte_stream(self):
        screen = TerminalScreen(rows=8, columns=40)
        coordinator = make_coordinator(screen)
        token = request_token("trial:output_0:3")
        prefix = b"W2S0|" + token.encode()
        expected = (prefix + b"AAA\n", prefix + b"BBB\n")
        transfer = coordinator._register("trial:output_0:3", prefix, expected)

        # tmux vẽ từng pane bằng cursor addressing; các dòng không tới liền mạch
        # trên luồng byte nhưng vẫn nằm đúng hàng của viewport.
        head = prefix.decode()
        coordinator.feed_bytes(f"\x1b[1;1H{head}AAA".encode(), 10, 10)
        self.assertEqual(transfer.unique_matched, 1)
        self.assertEqual(transfer.content_complete_mono_ns, 0)

        coordinator.feed_bytes(f"\x1b[4;1H{head}BBB".encode(), 25, 25)
        self.assertEqual(transfer.unique_matched, 2)
        self.assertEqual(transfer.content_complete_mono_ns, 25)

    def test_last_line_arriving_with_done_marker_is_still_counted(self):
        """Dòng cuối và dấu hoàn thành thường nằm trong cùng một khối byte.

        Nếu dấu hoàn thành được xử lý trước, transfer bị gỡ khỏi hàng chờ và
        dòng cuối không bao giờ được đếm — đúng triệu chứng 24/25 dòng quan sát
        được trên Pi.
        """
        screen = TerminalScreen(rows=8, columns=60)
        coordinator = make_coordinator(screen)
        token = request_token("trial:output_0:5")
        prefix = b"W2S0|" + token.encode()
        expected = (prefix + b"AAA\n", prefix + b"BBB\n")
        transfer = coordinator._register("trial:output_0:5", prefix, expected)
        head = prefix.decode()

        # PTY chuyển \n thành \r\n, nên cột được đưa về 0 giữa các dòng.
        coordinator.feed_bytes(f"{head}AAA\r\n".encode(), 10, 10)
        self.assertEqual(transfer.unique_matched, 1)

        # Một khối duy nhất mang cả dòng cuối lẫn dấu hoàn thành, và dấu hoàn
        # thành ở đây tách được từ chính luồng byte.
        coordinator.feed_bytes(
            f"{head}BBB\r\n__W2TT_DONE__:{token}:0\r\n".encode(), 20, 20,
        )
        self.assertEqual(transfer.unique_matched, 2)
        self.assertEqual(transfer.content_complete_mono_ns, 20)
        self.assertTrue(transfer.event.is_set())

    def test_marker_is_recovered_from_reconstructed_viewport(self):
        screen = TerminalScreen(rows=8, columns=60)
        coordinator = make_coordinator(screen)
        token = request_token("trial:output_0:4")
        prefix = b"W2S0|" + token.encode()
        transfer = coordinator._register("trial:output_0:4", prefix, ())

        # Dấu hoàn thành bị cursor sequence cắt đôi trên luồng byte thô, nên
        # chỉ viewport mới ghép lại được thành một dòng hoàn chỉnh.
        marker = f"__W2TT_DONE__:{token}:0"
        coordinator.feed_bytes(f"\x1b[2;1H{marker[:10]}".encode(), 10, 10)
        coordinator.feed_bytes(f"\x1b[2;11H{marker[10:]}".encode(), 20, 20)
        self.assertTrue(transfer.event.is_set())
        self.assertEqual(transfer.exit_code, 0)


class MoshSessionTests(unittest.TestCase):
    """Mosh luôn là một terminal session duy nhất.

    Nó chỉ được đánh giá ở kịch bản một workload, nên không có cấu hình nào
    khiến workload phải dựng nhiều pane để giả lập stream song song.
    """

    def _connection(self, roles, rows="128"):
        return DirectW2Connection(
            {"W2_MOSH_COLUMNS": "4096", "W2_MOSH_ROWS": rows},
            "mosh", roles, "o001_trial",
        )

    def test_single_terminal_spec_without_pane_splitting(self):
        specs = self._connection(["output_0"])._stream_specs()
        command = specs[0].remote_command
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].role, "terminal")
        self.assertNotIn("tmux", command)
        self.assertNotIn("split-window", command)
        self.assertIn("stty -echo", command)

    def test_viewport_must_hold_the_payload_and_its_markers(self):
        with self.assertRaisesRegex(ValueError, "viewport Mosh"):
            self._connection(["output_0"], rows="10")._stream_specs()

    def test_narrow_viewport_is_rejected_so_lines_never_wrap(self):
        connection = DirectW2Connection(
            {"W2_MOSH_COLUMNS": "80", "W2_MOSH_ROWS": "128"},
            "mosh", ["output_0"], "o001_trial",
        )
        with self.assertRaisesRegex(ValueError, "viewport Mosh"):
            connection._stream_specs()


class AnalyzerTests(unittest.TestCase):
    def _row(self, **overrides):
        row = {
            "protocol": "mosh", "status": "partial",
            "first_byte_latency_ms": "10", "completion_latency_ms": "20",
            "marker_latency_ms": "25", "completion_marker_received": "1",
            "throughput_mib_s": "", "content_coverage_pct": "50",
            "raw_byte_ratio_pct": "60", "verified_byte_ratio_pct": "50",
            "expected_bytes": "100", "verified_bytes": "50",
            "bytes_complete": "0", "lines_complete": "0", "hash_complete": "0",
            "output_complete": "0", "raw_capture_exact": "0",
            "content_complete": "0", "content_complete_latency_ms": "",
        }
        row.update(overrides)
        return row

    def test_partial_mosh_rows_still_have_visible_latency(self):
        analyzer = load_analyzer()
        summary = analyzer.summarize_group([
            self._row(),
            self._row(status="timeout", completion_marker_received="0",
                      marker_latency_ms=""),
        ])
        self.assertEqual(summary["completed_transfers"], 0)
        self.assertEqual(summary["command_visible_n"], 1)
        self.assertEqual(summary["command_visible_mean_ms"], "25.000")

    def test_content_complete_is_reported_even_without_a_done_marker(self):
        """Nội dung đủ vẫn đo được khi dấu hoàn thành không bao giờ tới."""
        analyzer = load_analyzer()
        summary = analyzer.summarize_group([
            self._row(status="timeout", completion_marker_received="0",
                      marker_latency_ms="", content_complete="1",
                      content_complete_latency_ms="180"),
            self._row(content_complete="1", content_complete_latency_ms="220"),
            self._row(),
        ])
        self.assertEqual(summary["completed_transfers"], 0)
        self.assertEqual(summary["content_complete_transfers"], 2)
        self.assertEqual(summary["content_complete_rate_pct"], "66.667")
        self.assertEqual(summary["content_complete_median_ms"], "200.000")


class RunStreamTests(unittest.TestCase):
    def test_row_carries_content_completion_from_the_adapter(self):
        trial = {
            "run_id": "test", "block_id": 1, "trial_order": 1,
            "trial_id": "mosh_w2-s2_r01", "trial_tag": "o001_test",
            "protocol": "mosh", "scenario": "W2-S2", "stream_count": 2,
        }
        line = b"W2S0|" + b"A" * 33 + b"\n"
        payload = {
            "name": "p0.txt", "line_prefix": "W2S0|",
            "bytes": len(line), "lines": 1, "_expected_lines": (line,),
        }
        sample = _sample_payload_spec(trial, "output_0", 1, payload, "/tmp/p0.txt")
        expected = sample["expected_lines"]

        class FakeStream:
            stream_id = ""
            conversation_id = ""

            def execute(self, request_id, command, line_prefix, timeout,
                        expected_lines=()):
                return {
                    "stdout": b"".join(expected_lines),
                    "exit_code": 0, "send_time_ns": 1,
                    "first_byte_time_ns": 2, "last_byte_time_ns": 3,
                    "marker_time_ns": 4, "content_complete_time_ns": 5,
                    "first_byte_latency_ms": 1.0, "completion_latency_ms": 2.0,
                    "marker_latency_ms": 3.0,
                    "content_complete_latency_ms": 4.5,
                    "matched_lines": {line: 1 for line in expected_lines},
                    "completion_marker_received": True, "timed_out": False,
                    "output_ambiguous": False, "output_truncated": False,
                }

        rows, summary = run_stream(
            trial, "output_0", 0, FakeStream(), payload, "/tmp",
            threading.Barrier(1), 1.0, 1, False, 1,
        )
        self.assertEqual(rows[0]["content_complete"], 1)
        self.assertEqual(rows[0]["content_complete_latency_ms"], "4.500")
        self.assertEqual(rows[0]["valid_unique_lines"], 1)
        self.assertEqual(rows[0]["status"], "completed")
        self.assertEqual(summary["content_complete_transfers"], 1)
        self.assertEqual(summary["content_complete_rate_pct"], "100.000")

    def test_missing_lines_leave_the_sample_incomplete(self):
        trial = {
            "run_id": "test", "block_id": 1, "trial_order": 1,
            "trial_id": "mosh_w2-s4_r01", "trial_tag": "o002_test",
            "protocol": "mosh", "scenario": "W2-S4", "stream_count": 4,
        }
        lines = tuple(
            b"W2S0|" + bytes([65 + index]) * 33 + b"\n" for index in range(2)
        )
        payload = {
            "name": "p0.txt", "line_prefix": "W2S0|",
            "bytes": sum(len(item) for item in lines), "lines": 2,
            "_expected_lines": lines,
        }

        class HalfStream:
            stream_id = ""
            conversation_id = ""

            def execute(self, request_id, command, line_prefix, timeout,
                        expected_lines=()):
                return {
                    "stdout": expected_lines[0], "exit_code": 0,
                    "send_time_ns": 1, "first_byte_time_ns": 2,
                    "last_byte_time_ns": 3, "marker_time_ns": 4,
                    "content_complete_time_ns": None,
                    "first_byte_latency_ms": 1.0, "completion_latency_ms": 2.0,
                    "marker_latency_ms": 3.0,
                    "content_complete_latency_ms": None,
                    "matched_lines": {expected_lines[0]: 1},
                    "completion_marker_received": True, "timed_out": False,
                    "output_ambiguous": False, "output_truncated": False,
                }

        rows, summary = run_stream(
            trial, "output_0", 0, HalfStream(), payload, "/tmp",
            threading.Barrier(1), 1.0, 1, False, 1,
        )
        self.assertEqual(rows[0]["content_complete"], 0)
        self.assertEqual(rows[0]["content_complete_latency_ms"], "")
        self.assertEqual(rows[0]["missing_lines"], 1)
        self.assertEqual(rows[0]["status"], "partial")
        self.assertEqual(summary["content_complete_transfers"], 0)


if __name__ == "__main__":
    unittest.main()
