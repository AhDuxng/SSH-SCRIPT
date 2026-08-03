import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from analyze_w2 import summarize_samples
from run_w2 import build_schedule
from terminal_io import wait_for_marker
from workloads import wrap_command


# Tạo một dòng sample tối giản cho analyzer.
def make_sample(protocol, workload, latency, size, throughput):
    return {
        "trial_id": f"{protocol}_{workload}_r01",
        "protocol": protocol,
        "workload": workload,
        "status": "success",
        "latency_ms": str(latency),
        "output_bytes": str(size),
        "output_lines": "10",
        "throughput_mib_s": str(throughput),
    }


class FakeChild:
    # Khởi tạo nguồn chunk giả cho terminal reader.
    def __init__(self, chunks):
        self.chunks = list(chunks)

    # Trả lần lượt từng chunk giống API pexpect.
    def read_nonblocking(self, size, timeout):
        del size, timeout
        return self.chunks.pop(0)


class AnalysisTests(unittest.TestCase):
    # Mỗi block phải chứa đầy đủ mọi tổ hợp protocol × workload.
    def test_schedule_uses_complete_blocks(self):
        protocols = ["ssh", "ssh3"]
        workloads = ["find_usr", "large_file"]
        commands = {"find_usr": "find /usr", "large_file": "cat /tmp/file"}
        rows = build_schedule(protocols, workloads, commands, 2, 42, "test", "low")
        self.assertEqual(len(rows), 8)
        first = {(row["protocol"], row["workload"]) for row in rows[:4]}
        self.assertEqual(len(first), 4)

    # Summary phải có cả latency, output bytes và throughput.
    def test_summary_reports_large_output_metrics(self):
        rows = [
            make_sample("ssh", "large_file", 100, 1000, 1.0),
            make_sample("ssh", "large_file", 200, 3000, 2.0),
        ]
        summary = summarize_samples(rows)[0]
        self.assertEqual(summary["mean_ms"], "150.000000")
        self.assertEqual(summary["output_bytes_mean"], "2000.000000")
        self.assertEqual(summary["throughput_mib_s_mean"], "1.500000")

    # Reader chỉ dừng sau marker và không tính newline phân cách của wrapper.
    def test_marker_reader_counts_complete_output(self):
        marker = "__W2_DONE_TEST__"
        child = FakeChild(["abc\n", "\n__W2_DONE_TEST__ exit_code=0\n"])
        byte_count, line_count, exit_code = wait_for_marker(child, marker, 1, 0, 4096)
        self.assertEqual(byte_count, 4)
        self.assertEqual(line_count, 1)
        self.assertEqual(exit_code, 0)

    # Marker vẫn phải nhận được khi ANSI redraw bị chia giữa hai chunk.
    def test_marker_reader_handles_split_ansi(self):
        marker = "__W2_DONE_TEST__"
        child = FakeChild(["abc\n\n__W2_\x1b[", "31mDONE_TEST__ exit_code=0\n"])
        byte_count, _line_count, exit_code = wait_for_marker(child, marker, 1, 0, 4096)
        self.assertEqual(byte_count, 4)
        self.assertEqual(exit_code, 0)

    # Wrapper phải giữ stderr và phát exit code sau output.
    def test_command_wrapper_contains_completion_marker(self):
        wrapped = wrap_command("find /usr", "DONE", 0)
        self.assertIn("2>&1", wrapped)
        self.assertIn("DONE", wrapped)
        self.assertIn("exit_code=%d", wrapped)


if __name__ == "__main__":
    unittest.main()
