"""Kiểm tra các bất biến của khung thí nghiệm.

Những kiểm tra ở đây bảo vệ các quyết định về phương pháp, không phải chi tiết
cài đặt: giao thức nào được chạy kịch bản nào, số trial toàn cục, và việc thống
kê không lặng lẽ làm sai lệch phân bố độ trễ.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from harness.experiment import (
    DEFAULT_TRIALS_PER_CONFIGURATION,
    Scenario,
    build_matrix,
    build_schedule,
    render_matrix,
)
from harness.results import read_rows, write_rows
from harness.settings import ConfigurationError, build_plan, load_settings
from harness.statistics import latency_stats, percentile, rate_pct, summarize_latency
from stream_mux.capability import capability, supports_stream_count

MULTIPLEXING = [Scenario("S1", 1), Scenario("S2", 2), Scenario("S4", 4)]
ALL_PROTOCOLS = ["ssh", "ssh3", "mosh"]


class ExperimentMatrixTests(unittest.TestCase):
    def test_mosh_is_limited_to_the_single_workload_scenario(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        self.assertEqual(matrix.scenarios_for("mosh"), ("S1",))

    def test_ssh_and_ssh3_cover_every_multiplexing_scenario(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        for protocol in ("ssh", "ssh3"):
            self.assertEqual(
                matrix.scenarios_for(protocol), ("S1", "S2", "S4"), protocol,
            )

    def test_exclusion_carries_a_reason(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        excluded = {(item.protocol, item.scenario.name) for item in matrix.skipped}
        self.assertEqual(excluded, {("mosh", "S2"), ("mosh", "S4")})
        self.assertTrue(all(item.reason for item in matrix.skipped))

    def test_interference_scenarios_keep_every_protocol(self):
        """Kịch bản đo can nhiễu giữ mọi giao thức; khác biệt nằm ở stream_count."""
        scenarios = [Scenario("MIX", 3, measures_multiplexing=False)]
        matrix = build_matrix(ALL_PROTOCOLS, scenarios)
        self.assertEqual(len(matrix), 3)
        self.assertEqual(matrix.skipped, ())

    def test_stream_count_never_exceeds_protocol_capability(self):
        matrix = build_matrix(
            ALL_PROTOCOLS, [Scenario("MIX", 3, measures_multiplexing=False)],
        )
        by_protocol = {item.protocol: item.stream_count for item in matrix.configurations}
        self.assertEqual(by_protocol["mosh"], 1)
        self.assertEqual(by_protocol["ssh"], 3)

    def test_editor_dimension_multiplies_only_valid_cells(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING, editors=("vim", "nano"))
        # (ssh + ssh3) × 3 kịch bản × 2 editor, cộng mosh × 1 kịch bản × 2 editor.
        self.assertEqual(len(matrix), 2 * 3 * 2 + 1 * 1 * 2)

    def test_rendered_matrix_marks_excluded_cells(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        text = render_matrix(matrix, MULTIPLEXING, 5)
        mosh_row = next(line for line in text.splitlines() if line.startswith("mosh"))
        self.assertEqual(mosh_row.count("✓"), 1)
        self.assertEqual(mosh_row.count("—"), 2)


class ScheduleTests(unittest.TestCase):
    def test_every_configuration_runs_the_global_trial_count(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        schedule = build_schedule(
            matrix, DEFAULT_TRIALS_PER_CONFIGURATION, 1, "run",
        )
        counts: dict[tuple[str, str], int] = {}
        for row in schedule:
            key = (row["protocol"], row["scenario"])
            counts[key] = counts.get(key, 0) + 1
        self.assertEqual(set(counts.values()), {DEFAULT_TRIALS_PER_CONFIGURATION})

    def test_global_trial_count_is_five(self):
        self.assertEqual(DEFAULT_TRIALS_PER_CONFIGURATION, 5)

    def test_blocks_are_complete_and_reproducible(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING)
        first = build_schedule(matrix, 3, 7, "run")
        second = build_schedule(matrix, 3, 7, "run")
        self.assertEqual(first, second)
        for block in (1, 2, 3):
            in_block = [row for row in first if row["block_id"] == block]
            self.assertEqual(len(in_block), len(matrix))

    def test_schedule_separates_logical_workloads_from_transport_streams(self):
        matrix = build_matrix(
            ["mosh"], [Scenario("MIX", 3, measures_multiplexing=False)],
        )
        row = build_schedule(matrix, 1, 1, "run")[0]
        self.assertEqual(row["logical_workload_count"], 3)
        self.assertEqual(row["stream_count"], 1)

    def test_trial_identifiers_are_unique(self):
        matrix = build_matrix(ALL_PROTOCOLS, MULTIPLEXING, editors=("vim", "nano"))
        schedule = build_schedule(matrix, 5, 3, "run")
        self.assertEqual(len({row["trial_id"] for row in schedule}), len(schedule))
        self.assertEqual(len({row["trial_tag"] for row in schedule}), len(schedule))


class CapabilityTests(unittest.TestCase):
    def test_single_stream_is_allowed_for_every_protocol(self):
        for protocol in ALL_PROTOCOLS:
            self.assertTrue(supports_stream_count(protocol, 1), protocol)

    def test_only_multiplexing_protocols_accept_more_than_one_stream(self):
        self.assertTrue(supports_stream_count("ssh", 4))
        self.assertTrue(supports_stream_count("ssh3", 4))
        self.assertFalse(supports_stream_count("mosh", 2))

    def test_unknown_protocol_is_rejected_with_the_known_list(self):
        with self.assertRaises(ValueError) as caught:
            capability("telnet")
        self.assertIn("ssh3", str(caught.exception))


class StatisticsTests(unittest.TestCase):
    def test_percentile_interpolates_between_neighbours(self):
        self.assertAlmostEqual(percentile([10, 20, 30, 40], 0.5), 25.0)
        self.assertAlmostEqual(percentile([10, 20], 0.95), 19.5)

    def test_empty_sample_yields_blank_not_zero(self):
        """Ô trống và 0 mang ý nghĩa khác nhau trong bảng kết quả."""
        self.assertEqual(percentile([], 0.95), "")
        self.assertEqual(summarize_latency([]).median_ms, "")
        self.assertEqual(rate_pct(0, 0), "")

    def test_rate_of_zero_successes_is_zero_not_blank(self):
        self.assertEqual(rate_pct(0, 10), "0.000")

    def test_latency_columns_carry_their_unit(self):
        self.assertEqual(set(latency_stats([1, 2])), {
            "mean_ms", "median_ms", "p95_ms", "p99_ms",
        })


class ResultSerializationTests(unittest.TestCase):
    def test_declared_schema_is_preserved_and_extras_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            write_rows(
                path, ["a", "b"],
                [{"a": 1, "b": 2, "unused": 3}],
            )
            rows = read_rows(path, required={"a", "b"})
        self.assertEqual(rows, [{"a": "1", "b": "2"}])

    def test_missing_required_column_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.csv"
            write_rows(path, ["a"], [{"a": 1}])
            with self.assertRaises(ValueError) as caught:
                read_rows(path, required={"a", "missing"})
        self.assertIn("missing", str(caught.exception))


class ConfigurationValidationTests(unittest.TestCase):
    def _write(self, directory: str, body: str) -> Path:
        path = Path(directory) / "config.env"
        path.write_text(
            "SERVER_USER=user\nSERVER_HOST=host\n" + body, encoding="utf-8",
        )
        return path

    def test_invalid_scenario_is_rejected_before_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "SCENARIOS=S1,NOPE\n")
            with self.assertRaises(ConfigurationError):
                build_plan(
                    load_settings(path),
                    {item.name: item for item in MULTIPLEXING},
                    default_seed=1,
                )

    def test_missing_server_host_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.env"
            path.write_text("SERVER_USER=user\nSERVER_HOST=CHANGE_ME\n")
            with self.assertRaises(ConfigurationError):
                build_plan(
                    load_settings(path),
                    {item.name: item for item in MULTIPLEXING},
                    default_seed=1,
                )

    def test_non_numeric_value_names_the_offending_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "WARMUP_SECONDS=abc\n")
            with self.assertRaises(ConfigurationError) as caught:
                build_plan(
                    load_settings(path),
                    {item.name: item for item in MULTIPLEXING},
                    default_seed=1,
                )
        self.assertIn("WARMUP_SECONDS", str(caught.exception))

    def test_plan_defaults_to_the_global_trial_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._write(directory, "")
            plan = build_plan(
                load_settings(path),
                {item.name: item for item in MULTIPLEXING},
                default_seed=1,
            )
        self.assertEqual(plan.trials, DEFAULT_TRIALS_PER_CONFIGURATION)
        self.assertEqual(plan.matrix.scenarios_for("mosh"), ("S1",))


if __name__ == "__main__":
    unittest.main()
