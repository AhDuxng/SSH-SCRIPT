import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from analyze_w3 import summarize_samples


# Tao cac dong mau toi gian cho mot connection.
def sample_rows(trial_id, protocol, latencies, timeout_count=0):
    rows = []
    for latency in latencies:
        rows.append({
            "trial_id": trial_id,
            "protocol": protocol,
            "target": "shell",
            "profile": "c0_only",
            "status": "success",
            "latency_ms": str(latency),
            "stall": "0",
        })
    for _ in range(timeout_count):
        rows.append({
            "trial_id": trial_id,
            "protocol": protocol,
            "target": "shell",
            "profile": "c0_only",
            "status": "timeout",
            "latency_ms": "",
            "stall": "0",
        })
    return rows


class AnalysisTests(unittest.TestCase):
    # Metric phai gop mau thanh cong cua nhieu trial nhu cach thong ke cu.
    def test_metrics_pool_successful_samples_across_trials(self):
        rows = sample_rows("r1", "ssh", [10, 20]) + sample_rows("r2", "ssh", [30, 40])
        summary = summarize_samples(rows)[0]

        self.assertEqual(summary["connections"], 2)
        self.assertEqual(summary["mean_ms"], "25.000")
        self.assertEqual(summary["median_ms"], "25.000")

    # Timeout duoc bao cao rieng nhung khong lam an P95 cua mau thanh cong.
    def test_timeout_keeps_success_only_p95_visible(self):
        rows = sample_rows("r1", "mosh", [10, 20], timeout_count=8)
        summary = summarize_samples(rows)[0]

        self.assertEqual(summary["success_rate_pct"], "20.000")
        self.assertNotEqual(summary["p95_ms"], "")
        self.assertEqual(summary["timeout_count"], 8)


if __name__ == "__main__":
    unittest.main()
