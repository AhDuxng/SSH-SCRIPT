import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))
sys.path.insert(0, str(PROJECT_DIR / "tools"))

from analyze_w1 import summarize_commands
from run_w1 import build_schedule


# Tạo một dòng mẫu tối giản cho kiểm thử analyzer.
def make_sample(protocol, command, latency):
    return {
        "trial_id": f"{protocol}_r01",
        "protocol": protocol,
        "loop_index": "1",
        "warmup": "0",
        "command": command,
        "status": "success",
        "latency_ms": str(latency),
        "output_bytes": "100",
    }


class AnalysisTests(unittest.TestCase):
    # Mỗi block phải chứa đúng một trial của mọi giao thức.
    def test_schedule_uses_complete_blocks(self):
        schedule = build_schedule(["ssh", "ssh3", "mosh"], 2, 42, "test")
        self.assertEqual(len(schedule), 6)
        self.assertEqual({row["protocol"] for row in schedule[:3]}, {"ssh", "ssh3", "mosh"})

    # Summary phải tính metric từ các loop thành công không thuộc warm-up.
    def test_command_summary_reports_full_metrics(self):
        rows = [make_sample("ssh", "ls", 10), make_sample("ssh", "ls", 20)]
        summary = summarize_commands(rows)[0]
        self.assertEqual(summary["mean_ms"], "15.000")
        self.assertEqual(summary["median_ms"], "15.000")
        self.assertEqual(summary["success_rate_pct"], "100.000")


if __name__ == "__main__":
    unittest.main()
