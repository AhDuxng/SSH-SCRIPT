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
    Series,
    clear_figures,
    deduplicated_legend,
    grouped_bars,
    per_stream_panels,
    percent_panels,
    save_figure,
    use_paper_style,
    value_or_none,
)
from stream_mux.capability import (  # noqa: E402
    label as protocol_label, supports_stream_count,
)

SCENARIOS = ("W1-S1", "W1-S2", "W1-S4")
SCENARIO_STREAMS = {"W1-S1": 1, "W1-S2": 2, "W1-S4": 4}
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


# Tỷ lệ hoàn thành lệnh và tỷ lệ đầy đủ output, cùng một trục 0–100 %.
#
# Bốn phép đo đi từ lỏng tới chặt: "kế hoạch" tính trên toàn bộ lệnh dự kiến,
# "đã gửi" chỉ tính những lệnh thực sự được gửi đi, "Stream hoàn thành" là vai
# trò chạy trọn vẹn, và "Output đủ" là phần lệnh có output xác thực được và
# khớp nội dung mong đợi.
RELIABILITY_MEASURES = (
    ("command_completion_rate_pct", "Kế hoạch"),
    ("attempted_completion_rate_pct", "Đã gửi"),
    ("stream_completion_rate_pct", "Stream"),
    ("output_completeness_pct", "Output đủ"),
)


def plot_reliability(lookup, output_dir, network):
    figure = percent_panels(
        [(scenario, scenario) for scenario in SCENARIOS],
        RELIABILITY_MEASURES,
        PROTOCOL_ORDER,
        lambda scenario, protocol, column: value_or_none(
            lookup, (protocol, scenario), column,
        ),
        title=f"W1 — tỷ lệ hoàn thành lệnh và đầy đủ output ({network})",
        note=" · ".join(filter(None, (
            "Kế hoạch: hoàn thành trên tổng lệnh dự kiến · "
            "Đã gửi: hoàn thành trên số lệnh thực sự gửi đi · "
            "Stream: vai trò chạy trọn vẹn · "
            "Output đủ: output xác thực được và khớp nội dung mong đợi",
            matrix_note(lookup),
        ))),
    )
    save_figure(figure, output_dir, "figure_3_reliability")


# Cùng bốn phép đo nhưng tách theo từng stream: một stream hỏng lẻ bị trung
# bình của kịch bản che mất, mà đó lại là thứ cần thấy khi so multiplexing.
def plot_per_stream_reliability(streams, output_dir, network):
    lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in streams
        if supports_stream_count(row["protocol"], SCENARIO_STREAMS[row["scenario"]])
    }
    figure = per_stream_panels(
        SCENARIOS, lookup, "command_completion_rate_pct", PROTOCOL_ORDER,
        ylabel="Tỷ lệ hoàn thành (%)",
        title=f"W1 — tỷ lệ hoàn thành theo từng stream ({network})",
        scenario_titles=SCENARIO_TITLES, role_label=role_label, percent=True,
    )
    save_figure(figure, output_dir, "figure_5_per_stream_completion_rate")


SCENARIO_TITLES = {
    "W1-S1": "W1-S1 · 1 workload",
    "W1-S2": "W1-S2 · 2 workload",
    "W1-S4": "W1-S4 · 4 workload",
}


# Nhãn trục: Mosh là một terminal session, không phải stream truyền tải.
def role_label(protocol: str, role: str) -> str:
    if protocol == "mosh":
        return "Terminal"
    if role.startswith("command_"):
        return f"Stream {role.split('_', 1)[1]}"
    return role.replace("_", " ").title()


def plot_per_stream(streams, output_dir, column, title, ylabel, stem):
    """Giữ nguyên chi tiết từng stream mà stream_summary.csv đã thống kê."""
    lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in streams
        # Giao thức không hỗ trợ đa stream chỉ được vẽ ở kịch bản một workload.
        if supports_stream_count(row["protocol"], SCENARIO_STREAMS[row["scenario"]])
    }
    dropped = sorted({
        row["protocol"] for row in streams
        if not supports_stream_count(
            row["protocol"], SCENARIO_STREAMS[row["scenario"]]
        )
    })
    note = ""
    if dropped:
        names = ", ".join(protocol_label(item) for item in dropped)
        note = (
            f"{names} chỉ được đo với một workload: giao thức này không cung "
            "cấp stream logic tương đương SSH channel hay QUIC stream."
        )
    figure = per_stream_panels(
        SCENARIOS, lookup, column, PROTOCOL_ORDER,
        ylabel=ylabel, title=title, scenario_titles=SCENARIO_TITLES,
        role_label=role_label, note=note,
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
    plot_per_stream_reliability(streams, args.output_dir, args.network)
    for metric in ("mean", "median", "p95", "p99"):
        plot_per_stream(
            streams, args.output_dir, f"{metric}_ms",
            f"W1 — độ trễ theo từng stream, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_4_per_stream_latency_{metric}",
        )
    print(f"Đã lưu hình W1 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
