"""Hình phải phản ánh trung thực ma trận thí nghiệm.

Một giao thức vắng mặt ở kịch bản nào đó là thông tin về thiết kế thí nghiệm,
không phải dữ liệu thiếu. Hình không được lấp chỗ trống bằng cột 0 hay bằng giá
trị lặp từ kịch bản khác.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from harness.plotting import (
    PROTOCOL_STYLE,
    Series,
    grouped_bars,
    use_paper_style,
    value_or_none,
)
import matplotlib.pyplot as plt


class GroupedBarTests(unittest.TestCase):
    def setUp(self):
        use_paper_style()
        self.figure, self.axis = plt.subplots()

    def tearDown(self):
        plt.close(self.figure)

    def test_absent_configuration_draws_no_bar(self):
        series = [
            Series("ssh", [10.0, 20.0, 30.0]),
            Series("mosh", [40.0, None, None]),
        ]
        grouped_bars(self.axis, ["S1", "S2", "S4"], series)
        # Bốn giá trị thật, không phải sáu: hai ô None không sinh cột nào.
        self.assertEqual(len(self.axis.patches), 4)

    def test_no_bar_has_zero_height_as_a_placeholder(self):
        series = [Series("ssh", [10.0, 20.0]), Series("mosh", [30.0, None])]
        grouped_bars(self.axis, ["S1", "S2"], series)
        self.assertTrue(all(bar.get_height() > 0 for bar in self.axis.patches))

    def test_present_protocols_are_centred_in_their_group(self):
        """Nhóm phải căn giữa lại, nếu không người đọc tưởng phép đo bị mất."""
        series = [
            Series("ssh", [1.0, 1.0]),
            Series("ssh3", [1.0, 1.0]),
            Series("mosh", [1.0, None]),
        ]
        grouped_bars(self.axis, ["S1", "S2"], series)
        centres = sorted(
            bar.get_x() + bar.get_width() / 2 for bar in self.axis.patches
        )
        s2_centres = [value for value in centres if value > 0.5]
        self.assertEqual(len(s2_centres), 2)
        self.assertAlmostEqual(sum(s2_centres) / 2, 1.0, places=6)

    def test_all_values_missing_leaves_the_group_empty(self):
        series = [Series("mosh", [None, None])]
        grouped_bars(self.axis, ["S2", "S4"], series)
        self.assertEqual(len(self.axis.patches), 0)

    def test_axis_stays_valid_when_every_value_is_zero(self):
        series = [Series("ssh", [0.0, 0.0])]
        grouped_bars(self.axis, ["S1", "S2"], series)
        lower, upper = self.axis.get_ylim()
        self.assertGreater(upper, lower)

    def test_every_protocol_has_a_distinct_stable_style(self):
        colours = {name: style["color"] for name, style in PROTOCOL_STYLE.items()}
        self.assertEqual(len(set(colours.values())), len(colours))
        hatches = {name: style["hatch"] for name, style in PROTOCOL_STYLE.items()}
        self.assertEqual(len(set(hatches.values())), len(hatches))


class SummaryReadingTests(unittest.TestCase):
    def test_missing_row_and_blank_cell_both_read_as_absent(self):
        lookup = {("ssh", "S1"): {"median_ms": "12.5"}, ("ssh", "S2"): {"median_ms": ""}}
        self.assertEqual(value_or_none(lookup, ("ssh", "S1"), "median_ms"), 12.5)
        self.assertIsNone(value_or_none(lookup, ("ssh", "S2"), "median_ms"))
        self.assertIsNone(value_or_none(lookup, ("mosh", "S2"), "median_ms"))


if __name__ == "__main__":
    unittest.main()
