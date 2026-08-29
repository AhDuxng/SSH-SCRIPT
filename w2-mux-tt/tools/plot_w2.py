#!/usr/bin/env python3
"""Vẽ các metric chính từ scenario_summary.csv của W2."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path


cache = str(Path(tempfile.gettempdir()) / "w2_mux_tt_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", cache)
os.environ.setdefault("XDG_CACHE_HOME", cache)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
SCENARIOS = ("W2-S1", "W2-S2", "W2-S4")
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
STREAM_COUNTS = {"W2-S1": 1, "W2-S2": 2, "W2-S4": 4}
MIN_COMPLETION_RATE_PCT = 95.0
SCENARIO_LABELS = {
    scenario: f"{scenario}\n{count} concurrent workload{'s' if count > 1 else ''}"
    for scenario, count in STREAM_COUNTS.items()
}


# Đọc bảng tổng hợp W2.
def load_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Chuyển một trường CSV sang số hoặc None.
def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


# Chỉ công bố lossless completion latency khi ít nhất 95% phép truyền đã
# thử hoàn tất; đây là phép đo chỉ có nghĩa với luồng byte nguyên bản.
def metric_value(row, field):
    value = number(row, field)
    if not field.startswith("completion_"):
        return value
    completion_rate = number(row, "attempted_transfer_completion_rate_pct")
    if completion_rate is None or completion_rate < MIN_COMPLETION_RATE_PCT:
        return None
    return value


# Ghi kèm tỷ lệ mẫu đạt đủ nội dung khi tỷ lệ đó chưa tới ngưỡng công bố.
def coverage_suffix(row, field):
    if not field.startswith("content_complete_"):
        return ""
    rate = number(row, "content_complete_rate_pct")
    if rate is None or rate >= MIN_COMPLETION_RATE_PCT:
        return ""
    return f"\n({rate:.0f}%)"


# Vẽ một metric theo ba giao thức và ba kịch bản.
def plot_metric(rows, output_dir, field, title, ylabel, stem, network):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    x = np.arange(len(SCENARIOS))
    width = 0.24
    fig, axis = plt.subplots(figsize=(10, 5.8))
    series = []
    all_values = []
    for protocol in PROTOCOLS:
        values = [
            metric_value(lookup.get((protocol, scenario)), field)
            for scenario in SCENARIOS
        ]
        series.append((protocol, values))
        all_values.extend(value for value in values if value is not None)
    ceiling = max(all_values or [1.0])
    plotted = []
    for protocol_index, (protocol, values) in enumerate(series):
        heights = [value if value is not None else 0.0 for value in values]
        bars = axis.bar(
            x + (protocol_index - 1) * width,
            heights,
            width,
            label=LABELS[protocol],
            color=COLORS[protocol],
            edgecolor="black",
            linewidth=0.6,
            hatch=HATCHES[protocol],
        )
        plotted.append((bars, values))
    for (bars, values), (protocol, _) in zip(plotted, series):
        for bar, value, scenario in zip(bars, values, SCENARIOS):
            shown = "N/A" if value is None else f"{value:.1f}"
            shown += coverage_suffix(lookup.get((protocol, scenario)), field)
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                (value or 0) + max(ceiling * 0.015, 0.1),
                shown,
                ha="center",
                va="bottom",
                fontsize=7,
            )
    axis.set_title(f"{title} — {network.capitalize()}")
    axis.set_ylabel(ylabel)
    axis.set_xticks(x, [SCENARIO_LABELS[item] for item in SCENARIOS])
    if field.endswith("_pct"):
        axis.set_ylim(0, 108)
    else:
        axis.set_ylim(0, max(1.0, ceiling * 1.24))
    axis.grid(axis="y", alpha=0.25)
    axis.legend(ncol=3)
    footnote = ""
    if field.startswith("completion_"):
        footnote = (
            "N/A: fewer than 95% of attempted transfers completed with full "
            "verified output."
        )
    elif field.startswith("content_complete_"):
        footnote = (
            "Percentage under a bar: share of samples whose full payload was "
            "observed, when below 95%."
        )
    if footnote:
        fig.text(0.5, 0.01, footnote, ha="center", fontsize=8)
        fig.tight_layout(rect=(0, 0.035, 1, 1))
    else:
        fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


# Vẽ từng transport stream của SSH/SSH3 và một terminal vật lý của Mosh.
def plot_stream_metric(
    rows, scenario_rows, output_dir, field, title, ylabel, stem, network,
):
    lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows
    }
    scenario_lookup = {
        (row["protocol"], row["scenario"]): row for row in scenario_rows
    }
    fig, axes = plt.subplots(1, len(SCENARIOS), figsize=(15, 5.5), sharey=True)
    global_values = [
        metric_value(row, field)
        for row in rows if row["protocol"] != "mosh"
    ] + [
        metric_value(scenario_lookup.get(("mosh", scenario)), field)
        for scenario in SCENARIOS
    ]
    ceiling = max(
        (value for value in global_values if value is not None), default=1.0
    )
    for scenario_index, scenario in enumerate(SCENARIOS):
        axis = axes[scenario_index]
        stream_count = STREAM_COUNTS[scenario]
        items = []
        for protocol in ("ssh", "ssh3"):
            for stream_index in range(stream_count):
                items.append((
                    protocol,
                    f"{LABELS[protocol]}\nStream {stream_index}",
                    metric_value(
                        lookup.get((protocol, scenario, f"output_{stream_index}")),
                        field,
                    ),
                ))
        items.append((
            "mosh", "Mosh\nTerminal",
            metric_value(scenario_lookup.get(("mosh", scenario)), field),
        ))
        x = np.arange(len(items))
        bars = axis.bar(
            x,
            [value if value is not None else 0.0 for _, _, value in items],
            0.72,
            color=[COLORS[protocol] for protocol, _, _ in items],
            edgecolor="black",
            linewidth=0.6,
            hatch=[HATCHES[protocol] for protocol, _, _ in items],
        )
        for bar, (protocol, _, value) in zip(bars, items):
            shown = "N/A" if value is None else f"{value:.1f}"
            source = (
                scenario_lookup.get((protocol, scenario))
                if protocol == "mosh" else None
            )
            shown += coverage_suffix(source, field) if source else ""
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                (value or 0) + max(ceiling * 0.02, 0.1),
                shown,
                ha="center",
                va="bottom",
                fontsize=7,
            )
        axis.set_title(
            f"{scenario} — {stream_count} concurrent workload"
            f"{'s' if stream_count > 1 else ''}"
        )
        axis.set_xticks(x, [label for _, label, _ in items], fontsize=7)
        if field.endswith("_pct"):
            axis.set_ylim(0, 105)
        else:
            axis.set_ylim(0, max(1.0, ceiling * 1.22))
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel(ylabel)
    fig.suptitle(f"{title} — {network.capitalize()}")
    footnote = ""
    if field.startswith("completion_"):
        footnote = (
            "N/A: fewer than 95% of attempted transfers completed with full "
            "verified output."
        )
    elif field.startswith("content_complete_"):
        footnote = (
            "Percentage under a bar: share of samples whose full payload was "
            "observed, when below 95%."
        )
    if footnote:
        fig.text(0.5, 0.01, footnote, ha="center", fontsize=8)
        fig.tight_layout(rect=(0, 0.035, 1, 1))
    else:
        fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


# Vẽ completion theo kế hoạch, theo mẫu đã gửi và xác thực nội dung.
def plot_integrity(rows, output_dir, network):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    fields = (
        ("completion_marker_rate_pct", "Command finished"),
        ("content_complete_rate_pct", "Full payload observed"),
        ("fully_verified_output_rate_pct", "Full output + SHA-256"),
        ("verified_output_ratio_pct", "Verified expected content"),
        ("raw_capture_exact_rate_pct", "Lossless raw capture"),
    )
    x = np.arange(len(SCENARIOS) * len(PROTOCOLS))
    width = 0.16
    fig, axis = plt.subplots(figsize=(12, 5.8))
    for field_index, (field, label) in enumerate(fields):
        values = [
            number(lookup.get((protocol, scenario)), field)
            for scenario in SCENARIOS for protocol in PROTOCOLS
        ]
        heights = [value if value is not None else 0.0 for value in values]
        bars = axis.bar(
            x + (field_index - (len(fields) - 1) / 2) * width,
            heights, width,
            label=label, edgecolor="black", linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                (value if value is not None else 0.0) + 0.25,
                "N/A" if value is None else f"{value:.1f}",
                ha="center", va="bottom",
                fontsize=6, rotation=90,
            )
    labels = [
        f"{scenario}\n{LABELS[protocol]}"
        for scenario in SCENARIOS for protocol in PROTOCOLS
    ]
    axis.set_title(
        f"W2 verified output integrity — {network.capitalize()}\n"
        "Mosh values refer to deterministic content observed on its terminal"
    )
    axis.set_ylabel("Rate (%)")
    axis.set_xticks(x, labels)
    axis.set_ylim(0, 108)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(ncol=4, loc="lower center")
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "figure_5_output_integrity.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / "figure_5_output_integrity.pdf", bbox_inches="tight")
    plt.close(fig)


# Vẽ integrity của từng SSH/SSH3 stream và một terminal Mosh tổng hợp.
def plot_stream_integrity(rows, scenario_rows, output_dir, network):
    fields = (
        ("completion_marker_rate_pct", "Command finished"),
        ("fully_verified_output_rate_pct", "Full output + SHA-256"),
        ("verified_output_ratio_pct", "Verified expected content"),
        ("raw_capture_exact_rate_pct", "Lossless raw capture"),
    )
    stream_lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows
    }
    scenario_lookup = {
        (row["protocol"], row["scenario"]): row for row in scenario_rows
    }
    ordered = []
    for scenario in SCENARIOS:
        for protocol in ("ssh", "ssh3"):
            for stream_index in range(STREAM_COUNTS[scenario]):
                ordered.append((
                    scenario,
                    protocol,
                    f"S{stream_index}",
                    stream_lookup.get(
                        (protocol, scenario, f"output_{stream_index}")
                    ),
                ))
        ordered.append((
            scenario,
            "mosh",
            "Terminal",
            scenario_lookup.get(("mosh", scenario)),
        ))
    x = np.arange(len(ordered))
    width = 0.19
    fig, axis = plt.subplots(figsize=(16, 6))
    for field_index, (field, label) in enumerate(fields):
        values = [number(row, field) for _, _, _, row in ordered]
        bars = axis.bar(
            x + (field_index - (len(fields) - 1) / 2) * width,
            [value if value is not None else 0.0 for value in values], width,
            label=label, edgecolor="black", linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            if value is None:
                axis.text(
                    bar.get_x() + bar.get_width() / 2, 0.5, "N/A",
                    ha="center", va="bottom", fontsize=5, rotation=90,
                )
    labels = [
        f"{scenario}\n{LABELS[protocol]}\n{role}"
        for scenario, protocol, role, _ in ordered
    ]
    axis.set_title(
        f"W2 transport-stream output integrity — {network.capitalize()}\n"
        "Mosh is one physical terminal per scenario"
    )
    axis.set_ylabel("Rate (%)")
    axis.set_xticks(x, labels, fontsize=7)
    axis.set_ylim(0, 106)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(ncol=4, loc="lower center")
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "figure_7_per_stream_integrity.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / "figure_7_per_stream_integrity.pdf", bbox_inches="tight")
    plt.close(fig)


# Đọc tham số và vẽ bộ hình chuẩn W2.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()
    rows = load_rows(args.result_dir / "scenario_summary.csv")
    stream_rows = load_rows(args.result_dir / "stream_summary.csv")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Thư mục chỉ chứa hình do plot_w2 tạo; bỏ bộ hình của lần chạy trước.
    for pattern in ("figure_*.png", "figure_*.pdf"):
        for path in args.output_dir.glob(pattern):
            path.unlink()
    plots = []
    for metric in ("mean", "median", "p95", "p99"):
        plots.extend((
            (f"command_visible_{metric}_ms", f"W2 command visible — {metric.upper()}", "Latency (ms)", f"figure_0_command_visible_{metric}"),
            (f"content_complete_{metric}_ms", f"W2 full payload observed — {metric.upper()}", "Latency (ms)", f"figure_1_content_complete_{metric}"),
            (f"completion_{metric}_ms", f"W2 lossless byte-stream completion — {metric.upper()}", "Latency (ms)", f"figure_1b_lossless_completion_{metric}"),
            (f"setup_{metric}_ms", f"W2 connection + streams READY — {metric.upper()}", "Setup time (ms)", f"figure_4_setup_{metric}"),
        ))
    plots.extend((
        ("first_byte_median_ms", "W2 first-byte latency — MEDIAN", "Latency (ms)", "figure_2_first_byte_median"),
        ("throughput_mean_mib_s", "W2 verified payload throughput — MEAN", "MiB/s", "figure_3_throughput_mean"),
    ))
    for field, title, ylabel, stem in plots:
        plot_metric(rows, args.output_dir, field, title, ylabel, stem, args.network)
    plot_integrity(rows, args.output_dir, args.network)
    stream_plots = tuple(
        (
            f"content_complete_{metric}_ms",
            f"W2 per-stream full payload observed — {metric.upper()}",
            "Latency (ms)",
            f"figure_6_per_stream_content_complete_{metric}",
        )
        for metric in ("mean", "median", "p95", "p99")
    ) + tuple(
        (
            f"completion_{metric}_ms",
            f"W2 per-stream lossless completion — {metric.upper()}",
            "Latency (ms)",
            f"figure_6b_per_stream_completion_{metric}",
        )
        for metric in ("mean", "median", "p95", "p99")
    )
    for field, title, ylabel, stem in stream_plots:
        plot_stream_metric(
            stream_rows, rows, args.output_dir, field, title, ylabel, stem,
            args.network,
        )
    plot_stream_integrity(stream_rows, rows, args.output_dir, args.network)
    print(f"Saved W2 figures to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
