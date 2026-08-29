#!/usr/bin/env python3
"""Vẽ hình W1 từ bảng tổng hợp đã xử lý."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

import matplotlib.pyplot as plt  # noqa: E402

from harness.plotting import (  # noqa: E402
    DOUBLE_COLUMN,
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

SCENARIOS = ("W1-S1", "W1-S2", "W1-S4")
PROTOCOL_ORDER = ("ssh", "ssh3", "mosh")


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def matrix_note(lookup) -> str:
    partial = [
        protocol for protocol in PROTOCOL_ORDER
        if any((protocol, s) in lookup for s in SCENARIOS)
        and not all((protocol, s) in lookup for s in SCENARIOS)
    ]
    if not partial:
        return ""
    names = ", ".join(protocol_label(item) for item in partial)
    return (
        f"{names} chỉ được đo với một workload: giao thức này không cung cấp "
        "stream logic tương đương SSH channel hay QUIC stream."
    )


def plot_metric(lookup, output_dir, column, title, ylabel, stem):
    figure, axis = plt.subplots(figsize=DOUBLE_COLUMN)
    series = [
        Series(
            protocol,
            [value_or_none(lookup, (protocol, s), column) for s in SCENARIOS],
        )
        for protocol in PROTOCOL_ORDER
    ]
    grouped_bars(axis, SCENARIOS, series)
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    deduplicated_legend(axis, ncol=3, loc="upper left")
    note = matrix_note(lookup)
    if note:
        figure.text(0.5, -0.06, note, ha="center", fontsize=6.5)
    save_figure(figure, output_dir, stem)


def plot_reliability(lookup, output_dir, network):
    measures = (
        ("command_completion_rate_pct", "Theo kế hoạch"),
        ("attempted_completion_rate_pct", "Đã gửi"),
        ("stream_completion_rate_pct", "Vai trò"),
        ("output_completeness_pct", "Output"),
    )
    names = [name for _column, name in measures]
    figure, axes = plt.subplots(1, len(SCENARIOS), figsize=(7.0, 2.6), sharey=True)
    for axis, scenario in zip(axes, SCENARIOS):
        series = [
            Series(
                protocol,
                [
                    value_or_none(lookup, (protocol, scenario), column)
                    for column, _name in measures
                ],
            )
            for protocol in PROTOCOL_ORDER
            if (protocol, scenario) in lookup
        ]
        grouped_bars(axis, names, series, annotate=False)
        axis.set_ylim(0, 108)
        axis.set_title(scenario)
        axis.tick_params(axis="x", labelrotation=30)
        for item in series:
            for index, value in enumerate(item.values):
                if value is None:
                    axis.text(index, 3, "n/a", ha="center", fontsize=5, rotation=90)
    axes[0].set_ylabel("Tỷ lệ (%)")
    deduplicated_legend(
        axes[1], ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.72),
    )
    figure.suptitle(f"W1 — hoàn thành lệnh và toàn vẹn output ({network})", y=1.06)
    save_figure(figure, output_dir, "figure_3_reliability")


def plot_per_stream(streams, output_dir, column, title, ylabel, stem):
    """Chỉ giao thức có multiplexing mới có nhiều vai trò để tách."""
    lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in streams
    }
    multi = [p for p in ("ssh", "ssh3") if any(k[0] == p for k in lookup)]
    figure, axes = plt.subplots(1, len(SCENARIOS), figsize=WIDE, sharey=True)
    for axis, scenario in zip(axes, SCENARIOS):
        roles = sorted({k[2] for k in lookup if k[1] == scenario})
        width = 0.8 / max(len(multi), 1)
        for protocol_index, protocol in enumerate(multi):
            for role_index, role in enumerate(roles):
                value = value_or_none(lookup, (protocol, scenario, role), column)
                if value is None:
                    continue
                offset = (protocol_index - (len(multi) - 1) / 2) * width
                axis.bar(
                    role_index + offset, value, width, edgecolor="black",
                    linewidth=0.4,
                    label=protocol_label(protocol) if role_index == 0 else None,
                )
        axis.set_xticks(
            range(len(roles)), [r.replace("command_", "R") for r in roles],
        )
        axis.set_title(scenario)
    axes[0].set_ylabel(ylabel)
    deduplicated_legend(axes[0], ncol=2, loc="upper left")
    figure.suptitle(title)
    figure.text(
        0.5, -0.03,
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
    lookup = {(row["protocol"], row["scenario"]): row for row in scenarios}
    suffix = f" ({args.network})"

    for metric in ("mean", "median", "p95", "p99"):
        plot_metric(
            lookup, args.output_dir, f"{metric}_ms",
            f"W1 — độ trễ hoàn thành lệnh, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_1_command_latency_{metric}",
        )
        plot_metric(
            lookup, args.output_dir, f"setup_{metric}_ms",
            f"W1 — mở connection và sẵn sàng, {metric.upper()}{suffix}",
            "Thời gian thiết lập (ms)", f"figure_2_setup_{metric}",
        )
    plot_reliability(lookup, args.output_dir, args.network)
    for metric in ("median", "p95"):
        plot_per_stream(
            streams, args.output_dir, f"{metric}_ms",
            f"W1 — độ trễ theo từng stream, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_4_per_stream_latency_{metric}",
        )
    print(f"Đã lưu hình W1 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
