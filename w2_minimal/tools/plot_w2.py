#!/usr/bin/env python3
import argparse
import csv
import os
import tempfile
from pathlib import Path

_CACHE_ROOT = str(Path(tempfile.gettempdir()) / "w2_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", _CACHE_ROOT)
os.environ.setdefault("XDG_CACHE_HOME", _CACHE_ROOT)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
WORKLOADS = ("find_usr", "docker_logs", "journalctl", "large_file")
WORKLOAD_LABELS = {
    "find_usr": "find /usr",
    "docker_logs": "docker logs",
    "journalctl": "journalctl",
    "large_file": "cat large_file.txt",
}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
METRICS = {
    "mean": "mean",
    "median": "median",
    "p90": "p90",
    "p95": "p95",
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
def annotate(ax, bars, rows, values, decimals=2):
    top = max(values) if values else 1.0
    for bar, row, value in zip(bars, rows, values):
        rate = row.get("success_rate_pct", "0") if row else "0"
        shown = f"{value:.{decimals}f}" if value else "N/A"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(top * 0.015, 0.001),
            f"{shown}\n({rate}%)", ha="center", va="bottom", fontsize=7,
        )


# Vẽ grouped bar cho bốn workload và ba giao thức.
def plot_workloads(rows, output_dir, field, title, ylabel, stem, scale=1.0, decimals=2):
    lookup = {(row["protocol"], row["workload"]): row for row in rows}
    x = np.arange(len(WORKLOADS))
    width = 0.24
    fig, ax = plt.subplots(figsize=(11, 5.8))
    all_values = []
    plotted = []
    for index, protocol in enumerate(PROTOCOLS):
        selected = [lookup.get((protocol, workload)) for workload in WORKLOADS]
        values = [number(row, field) / scale for row in selected]
        bars = ax.bar(
            x + (index - 1) * width, values, width,
            label=LABELS[protocol], color=COLORS[protocol],
            edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
        )
        all_values.extend(values)
        plotted.append((bars, selected, values))
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x, [WORKLOAD_LABELS[name] for name in WORKLOADS])
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    ceiling = max(all_values) if all_values else 1.0
    ax.set_ylim(0, max(0.01, ceiling * 1.25))
    for bars, selected, values in plotted:
        annotate(ax, bars, selected, values, decimals=decimals)
    fig.tight_layout()
    save_figure(fig, output_dir, stem)


# Vẽ session setup theo giao thức.
def plot_setup(rows, output_dir, field, metric):
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
    ax.set_title(f"W2 session setup — {metric.upper()}")
    ax.set_ylabel("Latency (ms)")
    ax.grid(axis="y", alpha=0.25)
    ceiling = max(values) if values else 1.0
    ax.set_ylim(0, max(1.0, ceiling * 1.20))
    annotate(ax, bars, selected, values, decimals=1)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_4_session_setup_{metric}")


# Đọc tham số, nạp summary và sinh bốn nhóm figure.
def main():
    parser = argparse.ArgumentParser(description="Plot W2 large-output benchmark")
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--metric", choices=METRICS, default="median")
    args = parser.parse_args()
    metric = METRICS[args.metric]
    rows = load_rows(args.result_dir / "summary.csv")
    setup_rows = load_rows(args.result_dir / "setup_summary.csv")

    plot_workloads(
        rows, args.output_dir, f"{metric}_ms",
        f"W2 output completion latency — {metric.upper()}", "Latency (ms)",
        f"figure_1_latency_{metric}", decimals=1,
    )
    plot_workloads(
        rows, args.output_dir, f"throughput_mib_s_{metric}",
        f"W2 display throughput — {metric.upper()}", "Throughput (MiB/s)",
        f"figure_2_throughput_{metric}", decimals=2,
    )
    plot_workloads(
        rows, args.output_dir, f"output_bytes_{metric}",
        f"W2 received output — {metric.upper()}", "Output (MiB)",
        f"figure_3_output_size_{metric}", scale=1024.0 * 1024.0, decimals=2,
    )
    plot_setup(setup_rows, args.output_dir, f"{metric}_ms", metric)
    print(f"Saved W2 {metric} figures to {args.output_dir}")


if __name__ == "__main__":
    main()

