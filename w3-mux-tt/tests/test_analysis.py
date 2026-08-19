import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from analyze_w3 import summarize_group


class AnalysisTests(unittest.TestCase):
    def test_stall_is_completed_but_reported_separately(self):
        base = {
            "protocol": "ssh", "editor": "vim", "scenario": "W3-I1",
            "measurement_mode": "remote_terminal_render",
        }
        keys = [
            {**base, "completed": "1", "latency_ms": "20", "stall": "0", "timeout": "0"},
            {**base, "completed": "1", "latency_ms": "1200", "stall": "1", "timeout": "0"},
            {**base, "completed": "0", "latency_ms": "", "stall": "0", "timeout": "1"},
        ]
        streams = [{"stream_complete": "0"}]
        trials = [{"connection_valid": "1", "setup_ms": "100"}]
        result = summarize_group(keys, streams, trials)
        self.assertEqual(result["completed_keystrokes"], 2)
        self.assertEqual(result["keystroke_completion_rate_pct"], "66.667")
        self.assertEqual(result["stall_rate_pct"], "33.333")
        self.assertEqual(result["timeout_rate_pct"], "33.333")
        self.assertEqual(result["stream_completion_rate_pct"], "0.000")


if __name__ == "__main__":
    unittest.main()
