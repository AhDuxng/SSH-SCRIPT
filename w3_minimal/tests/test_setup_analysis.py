import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analyze_setup import summarize_setup


class SetupAnalysisTests(unittest.TestCase):
    # Chi cac phien mo thanh cong moi duoc dua vao metric setup.
    def test_setup_uses_only_successful_independent_sessions(self):
        base = {"protocol": "ssh3"}
        rows = [
            {**base, "trial_id": "r01", "status": "success", "session_setup_ms": "100"},
            {**base, "trial_id": "r02", "status": "success", "session_setup_ms": "200"},
            {**base, "trial_id": "r03", "status": "timeout", "session_setup_ms": ""},
        ]

        summary = summarize_setup(rows)[0]

        self.assertEqual(summary["sessions"], 3)
        self.assertEqual(summary["success"], 2)
        self.assertEqual(summary["success_rate_pct"], "66.667")
        self.assertEqual(summary["median_ms"], "150.000")


if __name__ == "__main__":
    unittest.main()
