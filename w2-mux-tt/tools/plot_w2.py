#!/usr/bin/env python3
"""Vẽ hình W2 từ bảng tổng hợp đã xử lý.

Script này chỉ đọc `scenario_summary.csv` và `stream_summary.csv`; nó không
biết gì về cách chạy thí nghiệm và không chứa giá trị kết quả nào được nhúng
sẵn. Mọi hình đều tái tạo được từ dữ liệu trong thư mục kết quả.
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

SCENARIOS = ("W2-S1", "W2-S2", "W2-S4")
PROTOCOL_ORDER = ("ssh", "ssh3", "mosh")

# Chỉ công bố độ trễ lossless khi phần lớn phép truyền thực sự hoàn tất; nếu
# không, con số chỉ phản ánh những mẫu may mắn còn sót lại.
MIN_COMPLETION_RATE_PCT = 95.0


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def scenario_index(rows) -> dict:
    return {(row["protocol"], row["scenario"]): row for row in rows}


def stream_index(rows) -> dict:
    return {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows
    }


def gated_value(lookup, key, column):
    """Ẩn độ trễ lossless của nhóm có tỷ lệ hoàn tất quá thấp."""
    value = value_or_none(lookup, key, column)
    if value is None or not column.startswith("completion_"):
        return value
    rate = value_or_none(lookup, key, "attempted_transfer_completion_rate_pct")
    if rate is None or rate < MIN_COMPLETION_RATE_PCT:
        return None
    return value


def build_series(lookup, column, gated=False):
    reader = gated_value if gated else value_or_none
    return [
        Series(
            protocol,
            [reader(lookup, (protocol, scenario), column) for scenario in SCENARIOS],
        )
        for protocol in PROTOCOL_ORDER
    ]


def matrix_note(lookup) -> str:
    """Nêu rõ vì sao một giao thức vắng mặt ở kịch bản nhiều stream."""
    missing = [
        protocol
        for protocol in PROTOCOL_ORDER
        if any((protocol, scenario) in lookup for scenario in SCENARIOS)
        and not all((protocol, scenario) in lookup for scenario in SCENARIOS)
    ]
    if not missing:
        return ""
    names = ", ".join(protocol_label(item) for item in missing)
    return (
        f"{names} chỉ được đo với một workload: giao thức này không cung cấp "
        "stream logic tương đương SSH channel hay QUIC stream."
    )


def plot_scenario_metric(lookup, output_dir, column, title, ylabel, stem, *, gated=False):
    figure, axis = plt.subplots(figsize=DOUBLE_COLUMN)
    series = build_series(lookup, column, gated=gated)
    grouped_bars(axis, SCENARIOS, series)
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    deduplicated_legend(axis, ncol=3, loc="upper left")
    note = matrix_note(lookup)
    if gated:
        note = (note + " " if note else "") + (
            f"Không hiển thị nhóm có dưới {MIN_COMPLETION_RATE_PCT:.0f}% phép "
            "truyền hoàn tất."
        )
    if note:
        figure.text(0.5, -0.04, note, ha="center", fontsize=6.5, wrap=True)
    save_figure(figure, output_dir, stem)


def plot_integrity(lookup, output_dir):
    """Bốn mức xác thực, từ lỏng tới chặt, cho từng kịch bản.

    Trục x là mức xác thực còn màu vẫn là giao thức, để nhận dạng trực quan của
    giao thức giữ nguyên trên mọi hình trong bài.
    """
    measures = (
        ("completion_marker_rate_pct", "Marker"),
        ("content_complete_rate_pct", "Payload"),
        ("fully_verified_output_rate_pct", "+SHA-256"),
        ("raw_capture_exact_rate_pct", "Byte thô"),
    )
    names = [name for _column, name in measures]
    figure, axes = plt.subplots(
        1, len(SCENARIOS), figsize=(7.0, 2.6), sharey=True,
    )
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
        # "Luồng byte nguyên bản" không áp dụng cho giao thức đồng bộ màn hình;
        # ghi rõ n/a thay vì để trống gây hiểu là phép đo bị thiếu.
        for item in series:
            for index, value in enumerate(item.values):
                if value is None:
                    axis.text(
                        index, 3, "n/a", ha="center", va="bottom",
                        fontsize=5, rotation=90,
                    )
    axes[0].set_ylabel("Tỷ lệ (%)")
    deduplicated_legend(
        axes[1], ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.72),
    )
    figure.suptitle("W2 — mức độ xác thực output", y=1.06)
    figure.text(
        0.5, -0.46,
        "Marker: lệnh báo kết thúc · Payload: quan sát đủ nội dung · "
        "+SHA-256: khớp băm · Byte thô: luồng byte nguyên bản (n/a với Mosh)",
        ha="center", fontsize=6,
    )
    save_figure(figure, output_dir, "figure_5_output_integrity")


def plot_per_stream(streams, output_dir, column, title, ylabel, stem):
    """Từng vai trò của các giao thức có multiplexing.

    Mosh không xuất hiện ở đây: nó chỉ có một terminal, nên "vai trò thứ hai"
    không tồn tại để so sánh.
    """
    lookup = stream_index(streams)
    multi = [
        protocol for protocol in ("ssh", "ssh3")
        if any(key[0] == protocol for key in lookup)
    ]
    figure, axes = plt.subplots(1, len(SCENARIOS), figsize=WIDE, sharey=True)
    for axis, scenario in zip(axes, SCENARIOS):
        roles = sorted({
            key[2] for key in lookup if key[1] == scenario
        })
        width = 0.8 / max(len(multi), 1)
        for protocol_index, protocol in enumerate(multi):
            for role_index, role in enumerate(roles):
                value = value_or_none(lookup, (protocol, scenario, role), column)
                if value is None:
                    continue
                offset = (protocol_index - (len(multi) - 1) / 2) * width
                axis.bar(
                    role_index + offset, value, width,
                    color=None, edgecolor="black", linewidth=0.4,
                    label=protocol_label(protocol) if role_index == 0 else None,
                )
        axis.set_xticks(
            range(len(roles)), [role.replace("output_", "S") for role in roles],
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
    lookup = scenario_index(scenarios)
    suffix = f" ({args.network})"

    for metric in ("mean", "median", "p95", "p99"):
        plot_scenario_metric(
            lookup, args.output_dir, f"content_complete_{metric}_ms",
            f"W2 — thời điểm quan sát đủ payload, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_1_content_complete_{metric}",
        )
        plot_scenario_metric(
            lookup, args.output_dir, f"completion_{metric}_ms",
            f"W2 — byte cuối của luồng, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_1b_lossless_completion_{metric}", gated=True,
        )
        plot_scenario_metric(
            lookup, args.output_dir, f"setup_{metric}_ms",
            f"W2 — mở connection và sẵn sàng, {metric.upper()}{suffix}",
            "Thời gian thiết lập (ms)", f"figure_4_setup_{metric}",
        )
    plot_scenario_metric(
        lookup, args.output_dir, "command_visible_median_ms",
        f"W2 — thời điểm lệnh hiện là đã kết thúc, MEDIAN{suffix}",
        "Độ trễ (ms)", "figure_0_command_visible_median",
    )
    plot_scenario_metric(
        lookup, args.output_dir, "first_byte_median_ms",
        f"W2 — byte đầu tiên, MEDIAN{suffix}",
        "Độ trễ (ms)", "figure_2_first_byte_median",
    )
    plot_scenario_metric(
        lookup, args.output_dir, "throughput_mean_mib_s",
        f"W2 — thông lượng payload đã xác thực, MEAN{suffix}",
        "Thông lượng (MiB/s)", "figure_3_throughput_mean",
    )
    plot_integrity(lookup, args.output_dir)
    for metric in ("median", "p95"):
        plot_per_stream(
            streams, args.output_dir, f"content_complete_{metric}_ms",
            f"W2 — quan sát đủ payload theo từng stream, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_6_per_stream_content_complete_{metric}",
        )
    print(f"Đã lưu hình W2 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
