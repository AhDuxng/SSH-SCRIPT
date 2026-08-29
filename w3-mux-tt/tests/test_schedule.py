"""Ma trận của W3 sau khi áp khả năng thật của từng giao thức."""

import sys
import unittest
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT.parent), str(PROJECT / "src")]

from constants import EDITORS, SCENARIO_STREAMS
from harness.experiment import (
    DEFAULT_TRIALS_PER_CONFIGURATION,
    Scenario,
    build_matrix,
    build_schedule,
)


def w3_matrix(protocols=("ssh", "ssh3", "mosh")):
    scenarios = [
        Scenario(name, count) for name, count in SCENARIO_STREAMS.items()
    ]
    return build_matrix(protocols, scenarios, editors=EDITORS)


class ScheduleTests(unittest.TestCase):
    def test_mosh_runs_only_the_single_editor_scenario(self):
        matrix = w3_matrix()
        self.assertEqual(set(matrix.scenarios_for("mosh")), {"W3-I1"})

    def test_ssh_and_ssh3_run_every_scenario_for_every_editor(self):
        matrix = w3_matrix()
        for protocol in ("ssh", "ssh3"):
            counts = Counter(matrix.scenarios_for(protocol))
            self.assertEqual(set(counts), set(SCENARIO_STREAMS))
            self.assertEqual(set(counts.values()), {len(EDITORS)}, protocol)

    def test_every_configuration_gets_the_global_trial_count(self):
        schedule = build_schedule(
            w3_matrix(), DEFAULT_TRIALS_PER_CONFIGURATION, 123, "run",
        )
        counts = Counter(
            (row["protocol"], row["editor"], row["scenario"]) for row in schedule
        )
        self.assertEqual(set(counts.values()), {DEFAULT_TRIALS_PER_CONFIGURATION})
        # (ssh + ssh3) × 3 kịch bản × 2 editor, cộng mosh × 1 kịch bản × 2 editor.
        self.assertEqual(len(counts), 2 * 3 * 2 + 2)

    def test_scenario_stream_counts_reach_the_schedule(self):
        schedule = build_schedule(w3_matrix(("ssh",)), 1, 1, "run")
        by_scenario = {row["scenario"]: row["stream_count"] for row in schedule}
        self.assertEqual(by_scenario["W3-I4"], 4)


if __name__ == "__main__":
    unittest.main()
