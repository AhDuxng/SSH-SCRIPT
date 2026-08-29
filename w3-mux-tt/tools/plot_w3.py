#!/usr/bin/env python3
"""Vẽ hình W3 từ bảng tổng hợp đã xử lý.

W3 có thêm chiều editor, nên mỗi hình là một hàng panel theo editor. Giao thức
giữ nguyên màu và hatch như ở W1/W2/W4.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

import matplotlib.pyplot as plt  # noqa: E402

from harness.plotting import (  # noqa: E402
    WIDE,
    Series,
    clear_figures,
    deduplicated_legend,
    grouped_bars,
    save_figure,
    use_paper_style,
    value_or_none,
)
from stream_mux.capability import label as protocol_label  # noqa: E402

SCENARIOS = ("W3-I1", "W3-I2", "W3-I4")
EDITORS = ("vim", "nano")
PROTOCOL_ORDER = ("ssh", "ssh3", "mosh")


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def matrix_note(lookup) -> str:
    partial = [
        protocol for protocol in PROTOCOL_ORDER
        if any((editor, protocol, s) in lookup for editor in EDITORS for s in SCENARIOS)
        and not all(
            (editor, protocol, s) in lookup for editor in EDITORS for s in SCENARIOS
        )
    ]
    if not partial:
        return ""
    names = ", ".join(protocol_label(item) for item in partial)
    return (
        f"{names} chỉ được đo với một phiên tương tác: giao thức này không cung "
        "cấp stream logic tương đương SSH channel hay QUIC stream."
    )


def plot_by_editor(lookup, output_dir, column, title, ylabel, stem, *, percent=False):
    figure, axes = plt.subplots(1, len(EDITORS), figsize=WIDE, sharey=True)
    for axis, editor in zip(axes, EDITORS):
        series = [
            Series(
                protocol,
                [
                    value_or_none(lookup, (editor, protocol, s), column)
                    for s in SCENARIOS
                ],
            )
            for protocol in PROTOCOL_ORDER
        ]
        grouped_bars(axis, SCENARIOS, series, annotate=not percent)
        if percent:
            axis.set_ylim(0, 108)
        axis.set_title(editor.capitalize())
    axes[0].set_ylabel(ylabel)
    deduplicated_legend(axes[0], ncol=3, loc="upper left")
    figure.suptitle(title, y=1.02)
    note = matrix_note(lookup)
    if note:
        figure.text(0.5, -0.05, note, ha="center", fontsize=6.5)
    save_figure(figure, output_dir, stem)


def plot_per_stream(streams, output_dir, column, title, ylabel, stem):
    """Chỉ SSH và SSH3 có nhiều vai trò tương tác để tách riêng."""
    lookup = {
        (row["editor"], row["protocol"], row["scenario"], row["stream_role"]): row
        for row in streams
    }
    multi = [p for p in ("ssh", "ssh3") if any(k[1] == p for k in lookup)]
    figure, axes = plt.subplots(
        len(EDITORS), len(SCENARIOS), figsize=(7.0, 4.2), sharey=True, squeeze=False,
    )
    for row_index, editor in enumerate(EDITORS):
        for column_index, scenario in enumerate(SCENARIOS):
            axis = axes[row_index][column_index]
            roles = sorted({
                key[3] for key in lookup
                if key[0] == editor and key[2] == scenario
            })
            width = 0.8 / max(len(multi), 1)
            for protocol_index, protocol in enumerate(multi):
                for role_index, role in enumerate(roles):
                    value = value_or_none(
                        lookup, (editor, protocol, scenario, role), column,
                    )
                    if value is None:
                        continue
                    offset = (protocol_index - (len(multi) - 1) / 2) * width
                    axis.bar(
                        role_index + offset, value, width, edgecolor="black",
                        linewidth=0.4,
                        label=(
                            protocol_label(protocol)
                            if role_index == 0 and row_index == 0
                            else None
                        ),
                    )
            axis.set_xticks(
                range(len(roles)),
                [role.replace("interactive_", "R") for role in roles],
            )
            if row_index == 0:
                axis.set_title(scenario)
            if column_index == 0:
                axis.set_ylabel(f"{editor.capitalize()}\n{ylabel}")
    deduplicated_legend(axes[0][0], ncol=2, loc="upper left")
    figure.suptitle(title, y=1.0)
    figure.text(
        0.5, -0.02,
        "Mosh không xuất hiện: một terminal session không có vai trò song song "
        "để so sánh theo stream.",
        ha="center", fontsize=6.5,
    )
    save_figure(figure, output_dir, stem)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()

    use_paper_style()
    clear_figures(args.output_dir)
    scenarios = load(args.result_dir / "scenario_summary.csv")
    streams = load(args.result_dir / "stream_summary.csv")
    lookup = {
        (row["editor"], row["protocol"], row["scenario"]): row for row in scenarios
    }
    suffix = f" ({args.network})"

    for metric in ("mean", "median", "p95", "p99"):
        plot_by_editor(
            lookup, args.output_dir, f"{metric}_ms",
            f"W3 — độ trễ từng phím, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_2_scenario_latency_{metric}",
        )
    plot_by_editor(
        lookup, args.output_dir, "setup_median_ms",
        f"W3 — mở connection và editor sẵn sàng, MEDIAN{suffix}",
        "Thời gian thiết lập (ms)", "figure_4_setup_median",
    )
    plot_by_editor(
        lookup, args.output_dir, "keystroke_completion_rate_pct",
        f"W3 — tỷ lệ phím render kịp{suffix}",
        "Tỷ lệ (%)", "figure_3_completion_rate", percent=True,
    )
    plot_by_editor(
        lookup, args.output_dir, "timeout_rate_pct",
        f"W3 — tỷ lệ phím quá hạn{suffix}",
        "Tỷ lệ (%)", "figure_3b_timeout_rate", percent=True,
    )
    for metric in ("median", "p95"):
        plot_per_stream(
            streams, args.output_dir, f"{metric}_ms",
            f"W3 — độ trễ theo từng stream, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_1_per_stream_latency_{metric}",
        )
    print(f"Đã lưu hình W3 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
