import subprocess
import sys
import unittest
from pathlib import Path

import pexpect

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from analyze_w2 import summarize_load, summarize_samples
from command_measurement import build_measured_command, wait_for_completion
from run_w2 import build_schedule


# Tạo một sample tối thiểu cho test phần thống kê.
def make_sample(protocol, workload, index, status, latency="", output="", rate=""):
    return {
        "trial_id": f"{protocol}_{workload}_r01",
        "protocol": protocol,
        "workload": workload,
        "sample_index": str(index),
        "status": status,
        "latency_ms": str(latency),
        "output_bytes": str(output),
        "throughput_bytes_per_sec": str(rate),
    }


# Giả lập PTY trả dữ liệu theo nhiều chunk.
class FakeChild:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.read_sizes = []

    def read_nonblocking(self, size, timeout):
        del timeout
        self.read_sizes.append(size)
        if not self.chunks:
            raise pexpect.EOF("end")
        return self.chunks.pop(0)


class AnalysisTests(unittest.TestCase):
    def test_schedule_uses_complete_blocks(self):
        rows = build_schedule(
            ["ssh", "ssh3"], ["find_usr", "docker_logs", "large_file"],
            2, 42, "test", "low",
        )
        self.assertEqual(len(rows), 12)
        self.assertEqual(
            len({(row["protocol"], row["workload"]) for row in rows[:6]}), 6,
        )

    def test_summary_reports_completion_time_and_success_rate(self):
        rows = [
            make_sample("ssh", "large_file", 1, "success", 100),
            make_sample("ssh", "large_file", 2, "success", 200),
            make_sample("ssh", "large_file", 3, "timeout"),
        ]
        summary = summarize_samples(rows)[0]
        self.assertEqual(summary["mean_ms"], "150.000000")
        self.assertEqual(summary["success_rate_pct"], "66.667")

    def test_load_summary_uses_successful_sample_bytes_and_rates(self):
        rows = [
            make_sample("ssh3", "docker_logs", 1, "success", 100, 1000, 10000),
            make_sample("ssh3", "docker_logs", 2, "success", 200, 2000, 10000),
            make_sample("ssh3", "docker_logs", 3, "timeout"),
        ]
        summary = summarize_load(rows)[0]
        self.assertEqual(summary["measured_samples"], 2)
        self.assertEqual(summary["output_mean_bytes"], "1500.000000")
        self.assertEqual(summary["observed_rate_mean_bytes_per_sec"], "10000.000000")

    def test_wrapped_command_prints_marker_after_command(self):
        command = build_measured_command("printf abc", "__W2_DONE_TEST__")
        result = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True,
        )
        self.assertEqual(result.stdout, "abc__W2_DONE_TEST__:0\n")

    def test_wait_for_completion_handles_split_marker_and_counts_output(self):
        child = FakeChild(["abc__W2_DO", "NE_TEST__:0\r\n"])
        cfg = {
            "COMMAND_TIMEOUT": "1", "COMMAND_IDLE_TIMEOUT": "1",
            "COMMAND_READ_BYTES": "2048", "COMMAND_PARSE_BUFFER_CHARS": "4096",
        }
        output_bytes, exit_code, ended_ns = wait_for_completion(
            child, "__W2_DONE_TEST__", cfg,
        )
        self.assertEqual(output_bytes, 3)
        self.assertEqual(exit_code, 0)
        self.assertGreater(ended_ns, 0)
        self.assertEqual(child.read_sizes, [2048, 2048])

    def test_wait_for_completion_allows_ansi_between_marker_characters(self):
        child = FakeChild(["data__W2_DONE_\x1b[31mTEST__:\x1b[0m7\r\n"])
        cfg = {"COMMAND_TIMEOUT": "1", "COMMAND_IDLE_TIMEOUT": "1"}
        output_bytes, exit_code, _ = wait_for_completion(child, "__W2_DONE_TEST__", cfg)
        self.assertEqual(output_bytes, 4)
        self.assertEqual(exit_code, 7)


if __name__ == "__main__":
    unittest.main()
