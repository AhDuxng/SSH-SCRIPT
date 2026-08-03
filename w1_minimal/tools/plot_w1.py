#!/usr/bin/env python3
import argparse
import csv
import os
import tempfile
from pathlib import Path

# Dùng cache ghi được cả trên runner bị sandbox và máy headless.
_CACHE_ROOT = str(Path(tempfile.gettempdir()) / "w1_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", _CACHE_ROOT)
os.environ.setdefault("XDG_CACHE_HOME", _CACHE_ROOT)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
COMMANDS = ("ls", "df -h", "free -m", "ps aux", "uptime")
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
METRICS = {
    "mean": ("mean_ms", "Mean"),
    "median": ("median_ms", "Median"),
    "p90": ("p90_ms", "P90"),
    "p95": ("p95_ms", "P95"),
}


# Đọc một bảng summary CSV phục vụ vẽ hình.
def load_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Chuyển một ô metric thành số và dùng 0 cho giá trị thiếu.
def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else 0.0


# Lưu đồng thời bản PNG và PDF của một figure.
def save_figure(fig, output_dir, stem):
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


# Ghi metric và success rate phía trên từng cột.
def annotate(ax, bars, rows, values):
    top = max(values) if values else 1.0
    for bar, row, value in zip(bars, rows, values):
        rate = row.get("success_rate_pct", "0") if row else "0"
        label = f"{value:.1f}\n({rate}%)" if value else f"N/A\n({rate}%)"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(top * 0.015, 0.2),
            label, ha="center", va="bottom", fontsize=7,
        )


# Vẽ grouped bar cho năm lệnh và ba giao thức.
def plot_commands(rows, output_dir, metric, field, title):
    lookup = {(row["protocol"], row["command"]): row for row in rows}
    x = np.arange(len(COMMANDS))
    width = 0.24
    fig, ax = plt.subplots(figsize=(11, 5.8))
    all_values = []
    plotted = []
    for index, protocol in enumerate(PROTOCOLS):
        selected = [lookup.get((protocol, command)) for command in COMMANDS]
        values = [number(row, field) for row in selected]
        bars = ax.bar(
            x + (index - 1) * width, values, width,
            label=LABELS[protocol], color=COLORS[protocol],
            edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
        )
        all_values.extend(values)
        plotted.append((bars, selected, values))
    ax.set_title(f"W1 command latency — {title}")
    ax.set_ylabel("Latency (ms)")
    ax.set_xticks(x, COMMANDS)
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    ceiling = max(all_values) if all_values else 1.0
    ax.set_ylim(0, max(1.0, ceiling * 1.24))
    for bars, selected, values in plotted:
        annotate(ax, bars, selected, values)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_1_command_results_{metric}")


# Vẽ một cột cho mỗi giao thức đối với loop hoặc session setup.
def plot_protocol_bars(rows, output_dir, metric, field, title, stem):
    lookup = {row["protocol"]: row for row in rows}
    selected = [lookup.get(protocol) for protocol in PROTOCOLS]
    values = [number(row, field) for row in selected]
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    bars = ax.bar(
        [LABELS[p] for p in PROTOCOLS], values,
        color=[COLORS[p] for p in PROTOCOLS], edgecolor="black", linewidth=0.7,
    )
    for bar, protocol in zip(bars, PROTOCOLS):
        bar.set_hatch(HATCHES[protocol])
    ax.set_title(title)
    ax.set_ylabel("Latency (ms)")
    ax.grid(axis="y", alpha=0.25)
    ceiling = max(values) if values else 1.0
    ax.set_ylim(0, max(1.0, ceiling * 1.20))
    annotate(ax, bars, selected, values)
    fig.tight_layout()
    save_figure(fig, output_dir, f"{stem}_{metric}")


# Đọc tham số, nạp summary và sinh ba nhóm figure.
def main():
    parser = argparse.ArgumentParser(description="Plot W1 command-loop benchmark")
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--metric", choices=METRICS, default="median")
    args = parser.parse_args()
    field, label = METRICS[args.metric]

    command_rows = load_rows(args.result_dir / "summary.csv")
    loop_rows = load_rows(args.result_dir / "loop_summary.csv")
    setup_rows = load_rows(args.result_dir / "setup_summary.csv")
    plot_commands(command_rows, args.output_dir, args.metric, field, label)
    plot_protocol_bars(
        loop_rows, args.output_dir, args.metric, field,
        f"W1 complete five-command loop — {label}", "figure_2_loop_results",
    )
    plot_protocol_bars(
        setup_rows, args.output_dir, args.metric, field,
        f"W1 session setup — {label}", "figure_3_session_setup",
    )
    print(f"Saved W1 {args.metric} figures to {args.output_dir}")


if __name__ == "__main__":
    main()
