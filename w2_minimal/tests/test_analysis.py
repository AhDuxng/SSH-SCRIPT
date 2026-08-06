import re
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from analyze_w2 import summarize_samples
import pexpect

from clock_sync import estimate_clock_offset
from continuous_workloads import (
    SEQUENCE, TIMESTAMP_19, build_output_load_command, build_preflight_command,
)
from run_w2 import build_schedule
from terminal_io import ECHO_GAP, clean_digits, gapped_literal


# Tạo một sample event tối giản cho analyzer.
def make_sample(protocol, workload, index, status, latency=""):
    return {
        "trial_id": f"{protocol}_{workload}_r01",
        "protocol": protocol,
        "workload": workload,
        "sample_index": str(index),
        "status": status,
        "latency_ms": str(latency),
    }


class AnalysisTests(unittest.TestCase):
    # Mỗi block chứa đầy đủ mọi tổ hợp protocol × workload.
    def test_schedule_uses_complete_blocks(self):
        rows = build_schedule(
            ["ssh", "ssh3"],
            ["find_usr", "docker_logs", "journalctl", "large_file"],
            2, 42, "test", "low",
        )
        self.assertEqual(len(rows), 16)
        first = {(row["protocol"], row["workload"]) for row in rows[:8]}
        self.assertEqual(len(first), 8)

    # Summary chỉ tính latency thành công nhưng tỷ lệ dùng toàn bộ mẫu kỳ vọng.
    def test_summary_reports_event_latency_and_completion(self):
        rows = [
            make_sample("ssh", "large_file", 1, "success", 100),
            make_sample("ssh", "large_file", 2, "success", 200),
            make_sample("ssh", "large_file", 3, "timeout"),
        ]
        summary = summarize_samples(rows)[0]
        self.assertEqual(summary["mean_ms"], "150.000000")
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["success_rate_pct"], "66.667")

    # Marker gapped phải nhận ANSI và newline xen giữa ký tự như Mosh redraw.
    def test_gapped_event_marker(self):
        pattern = re.compile(
            gapped_literal("W2_OUTPUT_EVENT_")
            + SEQUENCE + ECHO_GAP + ":" + ECHO_GAP + TIMESTAMP_19
        )
        text = "W2_OUTPUT_EVENT_12:\x1b[31m1785988222614578891"
        match = pattern.search(text)
        self.assertIsNotNone(match)
        self.assertEqual(clean_digits(match.group(1)), "12")
        self.assertEqual(clean_digits(match.group(2)), "1785988222614578891")

    # Các sequence có ANSI xen giữa vẫn được khôi phục chính xác.
    def test_clean_digits(self):
        self.assertEqual(clean_digits("1\x1b[31m2\r\n3"), "123")

    # Các shell command sinh ra cho workload và clock probe phải hợp lệ cú pháp Bash.
    def test_generated_shell_commands_are_valid(self):
        class FakeChild:
            def __init__(self):
                self.commands = []

            def sendline(self, command):
                self.commands.append(command)

            def sendcontrol(self, _char):
                pass

            def expect(self, _pattern, timeout=None):
                del timeout
                raise pexpect.TIMEOUT("stop after command capture")

        class FakeRunner:
            def expect_prompt(self, _child, _timeout=None):
                pass

        cfg = {
            "SAMPLES_PER_TRIAL": "1", "WARMUP_SAMPLES": "0",
            "EVENT_TIMEOUT": "1", "_CLOCK_OFFSET_NS": "0",
        }
        captured = []
        captured.extend([
            build_preflight_command("find /usr", "W2_PREFLIGHT_TEST_RC:"),
            build_output_load_command("find /usr", "W2_OUTPUT_EVENT_", 0.1),
        ])

        clock_child = FakeChild()
        with self.assertRaises(RuntimeError):
            estimate_clock_offset(clock_child, FakeRunner(), 1, 1, 1)
        captured.extend(clock_child.commands)

        for command in captured:
            result = subprocess.run(["bash", "-n", "-c", command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, msg=f"{command}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main()
