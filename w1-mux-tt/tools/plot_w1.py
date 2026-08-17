#!/usr/bin/env python3
import argparse
import csv
import os
import tempfile
from pathlib import Path

_CACHE = str(Path(tempfile.gettempdir()) / "w1_mux_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", _CACHE)
os.environ.setdefault("XDG_CACHE_HOME", _CACHE)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
SCENARIOS = ("W1-S1", "W1-S2", "W1-S4")
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
SCENARIO_LABELS = {
    "W1-S1": "W1-S1\n1 concurrent workload",
    "W1-S2": "W1-S2\n2 concurrent workloads",
    "W1-S4": "W1-S4\n4 concurrent workloads",
}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
METRICS = ("mean", "median", "p95", "p99")


# Đọc bảng tổng hợp theo kịch bản.
def load_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Chuyển trường CSV sang số và giữ None cho dữ liệu thiếu.
def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


# Matplotlib cần chiều cao số; giá trị thiếu được vẽ bằng cột 0 và ghi N/A.
def heights(values):
    return [value if value is not None else 0.0 for value in values]


# Lưu đồng thời PNG và PDF.
def save_figure(fig, output_dir, stem):
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


# Ghi giá trị lên đầu từng cột latency hoặc setup.
def annotate_values(ax, bars, values, ceiling):
    offset = max(ceiling * 0.015, 0.1)
    for bar, value in zip(bars, values):
        shown = f"{value:.1f}" if value is not None else "N/A"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            (value or 0) + offset,
            shown, ha="center", va="bottom", fontsize=7,
        )


# Vẽ latency hoàn thành lệnh theo số workload đồng thời và giao thức.
def plot_latency(rows, output_dir, metric, network):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    x = np.arange(len(SCENARIOS))
    width = 0.24
    field = f"{metric}_ms"
    series = []
    all_values = []
    for protocol in PROTOCOLS:
        values = [number(lookup.get((protocol, scenario)), field) for scenario in SCENARIOS]
        series.append((protocol, values))
        all_values.extend(value for value in values if value is not None)
    ceiling = max(all_values or [1.0])

    fig, ax = plt.subplots(figsize=(10, 5.8))
    plotted = []
    for index, (protocol, values) in enumerate(series):
        bars = ax.bar(
            x + (index - 1) * width, heights(values), width,
            label=LABELS[protocol], color=COLORS[protocol],
            edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
        )
        plotted.append((bars, values))
    ax.set_title(f"W1 command latency — {network.capitalize()} — {metric.upper()}")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(x, [SCENARIO_LABELS[item] for item in SCENARIOS])
    ax.set_ylim(0, max(1.0, ceiling * 1.24))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=3, loc="upper left")
    for bars, values in plotted:
        annotate_values(ax, bars, values, ceiling)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_1_command_latency_{metric}")


# Vẽ thời gian mở connection và chờ toàn bộ vai trò workload READY.
def plot_setup(rows, output_dir, metric, network):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    x = np.arange(len(SCENARIOS))
    width = 0.24
    field = f"setup_{metric}_ms"
    series = []
    all_values = []
    for protocol in PROTOCOLS:
        values = [number(lookup.get((protocol, scenario)), field) for scenario in SCENARIOS]
        series.append((protocol, values))
        all_values.extend(value for value in values if value is not None)
    ceiling = max(all_values or [1.0])

    fig, ax = plt.subplots(figsize=(10, 5.8))
    plotted = []
    for index, (protocol, values) in enumerate(series):
        bars = ax.bar(
            x + (index - 1) * width, heights(values), width,
            label=LABELS[protocol], color=COLORS[protocol],
            edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
        )
        plotted.append((bars, values))
    ax.set_title(f"W1 connection + workloads READY — {network.capitalize()} — {metric.upper()}")
    ax.set_ylabel("Setup time (ms)")
    ax.set_xticks(x, [SCENARIO_LABELS[item] for item in SCENARIOS])
    ax.set_ylim(0, max(1.0, ceiling * 1.24))
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=3, loc="upper left")
    for bars, values in plotted:
        annotate_values(ax, bars, values, ceiling)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_2_setup_{metric}")


# Vẽ completion theo kế hoạch, mẫu đã gửi, vai trò workload và output.
def plot_reliability(rows, output_dir, network):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    fields = (
        ("command_completion_rate_pct", "Planned"),
        ("attempted_completion_rate_pct", "Attempted"),
        ("stream_completion_rate_pct", "Role complete"),
        ("output_completeness_pct", "Output"),
    )
    x = np.arange(len(SCENARIOS) * len(PROTOCOLS))
    width = 0.19
    fig, ax = plt.subplots(figsize=(12, 5.8))
    for field_index, (field, label) in enumerate(fields):
        values = []
        for scenario in SCENARIOS:
            for protocol in PROTOCOLS:
                values.append(number(lookup.get((protocol, scenario)), field))
        bar_values = [value if value is not None else 0.0 for value in values]
        bars = ax.bar(
            x + (field_index - (len(fields) - 1) / 2) * width,
            bar_values, width,
            label=label, edgecolor="black", linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                (value if value is not None else 0.0) + 0.25,
                "N/A" if value is None else f"{value:.1f}",
                ha="center", va="bottom", fontsize=6, rotation=90,
            )
    tick_labels = [
        f"{scenario}\n{LABELS[protocol]}"
        for scenario in SCENARIOS for protocol in PROTOCOLS
    ]
    ax.set_title(f"W1 completion and output integrity — {network.capitalize()}")
    ax.set_ylabel("Rate (%)")
    ax.set_xticks(x, tick_labels)
    ax.set_ylim(0, 108)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=4, loc="lower center")
    fig.tight_layout()
    save_figure(fig, output_dir, "figure_3_reliability")


# Vẽ từng transport stream của SSH/SSH3 và một terminal vật lý của Mosh.
def plot_stream_latency(rows, scenario_rows, output_dir, metric, network):
    lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows
    }
    scenario_lookup = {
        (row["protocol"], row["scenario"]): row for row in scenario_rows
    }
    field = f"{metric}_ms"
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5), sharey=True)
    all_values = [
        number(row, field) for row in rows if row["protocol"] != "mosh"
    ] + [
        number(scenario_lookup.get(("mosh", scenario)), field)
        for scenario in SCENARIOS
    ]
    ceiling = max((value for value in all_values if value is not None), default=1.0)

    for axis, scenario in zip(axes, SCENARIOS):
        stream_count = int(scenario.rsplit("S", 1)[1])
        items = []
        for protocol in ("ssh", "ssh3"):
            for stream_index in range(stream_count):
                items.append((
                    protocol,
                    f"{LABELS[protocol]}\nStream {stream_index}",
                    number(
                        lookup.get(
                            (protocol, scenario, f"command_{stream_index}")
                        ),
                        field,
                    ),
                ))
        items.append((
            "mosh", "Mosh\nTerminal",
            number(scenario_lookup.get(("mosh", scenario)), field),
        ))
        x = np.arange(len(items))
        bars = axis.bar(
            x,
            heights([value for _, _, value in items]),
            0.72,
            color=[COLORS[protocol] for protocol, _, _ in items],
            edgecolor="black",
            linewidth=0.6,
            hatch=[HATCHES[protocol] for protocol, _, _ in items],
        )
        annotate_values(
            axis, bars, [value for _, _, value in items], ceiling
        )
        axis.set_title(
            f"{scenario} — {stream_count} concurrent workload"
            f"{'s' if stream_count > 1 else ''}"
        )
        axis.set_xticks(
            x, [label for _, label, _ in items], fontsize=7
        )
        axis.set_ylim(0, max(1.0, ceiling * 1.24))
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Latency (ms)")
    fig.suptitle(
        f"W1 transport-stream command latency — {network.capitalize()} — "
        f"{metric.upper()}"
    )
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_4_per_stream_latency_{metric}")


# Vẽ reliability của từng SSH/SSH3 stream và một terminal Mosh tổng hợp.
def plot_stream_reliability(rows, scenario_rows, output_dir, network):
    fields = (
        ("command_completion_rate_pct", "Planned"),
        ("attempted_completion_rate_pct", "Attempted"),
        ("stream_completion_rate_pct", "Stream"),
        ("output_completeness_pct", "Output"),
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
        stream_count = int(scenario.rsplit("S", 1)[1])
        for protocol in ("ssh", "ssh3"):
            for stream_index in range(stream_count):
                ordered.append((
                    scenario,
                    protocol,
                    f"S{stream_index}",
                    stream_lookup.get(
                        (protocol, scenario, f"command_{stream_index}")
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
    fig, ax = plt.subplots(figsize=(16, 6))
    for field_index, (field, label) in enumerate(fields):
        values = [
            None
            if protocol == "mosh" and field in {
                "stream_completion_rate_pct", "output_completeness_pct"
            }
            else number(row, field)
            for _, protocol, _, row in ordered
        ]
        bar_values = [value if value is not None else 0.0 for value in values]
        bars = ax.bar(
            x + (field_index - (len(fields) - 1) / 2) * width,
            bar_values, width,
            label=label, edgecolor="black", linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            if value is None:
                ax.text(
                    bar.get_x() + bar.get_width() / 2, 0.25, "N/A",
                    ha="center", va="bottom", fontsize=5, rotation=90,
                )
    labels = [
        f"{scenario}\n{LABELS[protocol]}\n{role}"
        for scenario, protocol, role, _ in ordered
    ]
    ax.set_title(
        f"W1 transport-stream completion — {network.capitalize()}\n"
        "Mosh is one physical terminal per scenario; output integrity is N/A"
    )
    ax.set_ylabel("Rate (%)")
    ax.set_xticks(x, labels, fontsize=7)
    ax.set_ylim(0, 106)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(ncol=4, loc="lower center")
    fig.tight_layout()
    save_figure(fig, output_dir, "figure_5_per_stream_reliability")


# Đọc tham số và tạo toàn bộ hình của một môi trường mạng.
def main():
    parser = argparse.ArgumentParser(description="Plot W1 multiplex benchmark")
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()
    rows = load_rows(args.result_dir / "scenario_summary.csv")
    stream_rows = load_rows(args.result_dir / "stream_summary.csv")
    for metric in METRICS:
        plot_latency(rows, args.output_dir, metric, args.network)
        plot_setup(rows, args.output_dir, metric, args.network)
        plot_stream_latency(
            stream_rows, rows, args.output_dir, metric, args.network
        )
    plot_reliability(rows, args.output_dir, args.network)
    plot_stream_reliability(
        stream_rows, rows, args.output_dir, args.network
    )
    print(f"Saved W1 figures to {args.output_dir}")


if __name__ == "__main__":
    main()
