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
    per_stream_panels,
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
        # Hình tỷ lệ vẫn phải hiện số: đây là con số công bố, và mắt không đọc
        # được chênh lệch 98,7 % với 99,4 % trên một trục 0–100 %.
        grouped_bars(axis, SCENARIOS, series, percent=percent)
        axis.set_title(editor.capitalize())
    axes[0].set_ylabel(ylabel)
    if percent:
        # Trục tỷ lệ luôn kín tới 100 %, không còn góc trống cho chú giải.
        deduplicated_legend(
            axes[0], sources=axes, ncol=3, loc="upper center",
            bbox_to_anchor=(1.03, -0.10),
        )
    else:
        deduplicated_legend(axes[0], sources=axes, ncol=3, loc="upper left")
    figure.suptitle(title, y=1.02)
    note = matrix_note(lookup)
    if note:
        figure.text(0.5, -0.05, note, ha="center", fontsize=6.5)
    save_figure(figure, output_dir, stem)


def plot_per_stream(streams, output_dir, column, title, ylabel, stem_template):
    """Một hình cho mỗi editor, dùng chung bố cục per-stream với W1 và W2."""
    for editor in EDITORS:
        lookup = {
            (row["protocol"], row["scenario"], row["stream_role"]): row
            for row in streams
            if row["editor"] == editor
        }
        if not lookup:
            continue
        figure = per_stream_panels(
            SCENARIOS, lookup, column, PROTOCOL_ORDER,
            ylabel=ylabel,
            title=f"{title} — {editor.capitalize()}",
            role_label=lambda protocol, role: (
                # Mosh chia màn hình trong một terminal duy nhất; gọi nó là
                # "stream" sẽ ngụ ý sai rằng đó là transport stream độc lập.
                f"Pane {role.removeprefix('interactive_')}"
                if protocol == "mosh"
                else f"Stream {role.removeprefix('interactive_')}"
            ),
            note=(
                "Mosh chỉ có một phiên tương tác: các pane nằm trong cùng một "
                "terminal, không phải stream transport độc lập như SSH channel "
                "hay QUIC stream."
            ),
        )
        save_figure(figure, output_dir, stem_template.format(editor=editor))


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
    # Ba mức "hoàn thành" của W3, từ nhỏ tới lớn: từng phím, từng stream, rồi
    # cả connection. W3 không có payload để đối chiếu nên không có phép đo "đầy
    # đủ output" tương ứng như W1/W2/W4 — chỉ số gần nhất là tỷ lệ phím render
    # kịp dưới đây.
    plot_by_editor(
        lookup, args.output_dir, "keystroke_completion_rate_pct",
        f"W3 — tỷ lệ phím render kịp{suffix}",
        "Tỷ lệ (%)", "figure_3_completion_rate", percent=True,
    )
    plot_by_editor(
        lookup, args.output_dir, "stream_completion_rate_pct",
        f"W3 — tỷ lệ stream hoàn thành{suffix}",
        "Tỷ lệ (%)", "figure_3c_stream_completion_rate", percent=True,
    )
    plot_by_editor(
        lookup, args.output_dir, "timeout_rate_pct",
        f"W3 — tỷ lệ phím quá hạn{suffix}",
        "Tỷ lệ (%)", "figure_3b_timeout_rate", percent=True,
    )
    plot_by_editor(
        lookup, args.output_dir, "stall_rate_pct",
        f"W3 — tỷ lệ phím bị khựng{suffix}",
        "Tỷ lệ (%)", "figure_3d_stall_rate", percent=True,
    )
    for metric in ("mean", "median", "p95", "p99"):
        plot_per_stream(
            streams, args.output_dir, f"{metric}_ms",
            f"W3 — độ trễ theo từng stream, {metric.upper()}{suffix}",
            "Độ trễ (ms)",
            "figure_1_{editor}_per_stream_latency_" + metric,
        )
    print(f"Đã lưu hình W3 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
