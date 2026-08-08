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
WORKLOADS = ("find_usr", "docker_logs", "large_file")
WORKLOAD_LABELS = {
    "find_usr": "find /usr",
    "docker_logs": "docker logs",
    "large_file": "cat large_file.txt",
}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
METRICS = {"mean": "mean", "median": "median", "p90": "p90", "p95": "p95"}


def load_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


def save_figure(fig, output_dir, stem):
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def annotate(ax, bars, rows, values):
    valid = [v for v in values if v is not None]
    top = max(valid) if valid else 1.0
    for bar, row, value in zip(bars, rows, values):
        rate = row.get("success_rate_pct", "0") if row else "0"
        shown = f"{value:.1f}" if value is not None else "N/A"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            (value if value is not None else 0) + max(top * 0.015, 0.01),
            f"{shown}\n({rate}%)", ha="center", va="bottom", fontsize=7,
        )


def plot_latency(rows, output_dir, field, metric):
    lookup = {(row["protocol"], row["workload"]): row for row in rows}
    x = np.arange(len(WORKLOADS))
    width = 0.24
    fig, ax = plt.subplots(figsize=(10, 5.8))
    plotted, all_values = [], []
    for index, protocol in enumerate(PROTOCOLS):
        selected = [lookup.get((protocol, workload)) for workload in WORKLOADS]
        values = [number(row, field) for row in selected]
        bars = ax.bar(
            x + (index - 1) * width, values, width,
            label=LABELS[protocol], color=COLORS[protocol],
            edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
        )
        plotted.append((bars, selected, values))
        all_values.extend([v for v in values if v is not None])
    ax.set_title(f"W2 end-to-end command completion time — {metric.upper()}")
    ax.set_ylabel("Completion time (ms)")
    ax.set_xticks(x, [WORKLOAD_LABELS[name] for name in WORKLOADS])
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    ceiling = max(all_values) if all_values else 1.0
    ax.set_ylim(0, max(1.0, ceiling * 1.25))
    for bars, selected, values in plotted:
        annotate(ax, bars, selected, values)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_1_command_completion_{metric}")


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
    valid = [v for v in values if v is not None]
    ax.set_ylim(0, max(1.0, max(valid or [1.0]) * 1.20))
    annotate(ax, bars, selected, values)
    fig.tight_layout()
    save_figure(fig, output_dir, f"figure_2_session_setup_{metric}")


def main():
    parser = argparse.ArgumentParser(description="Plot W2 command-completion benchmark")
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--metric", choices=METRICS, default="median")
    args = parser.parse_args()
    metric = METRICS[args.metric]
    plot_latency(
        load_rows(args.result_dir / "summary.csv"), args.output_dir,
        f"{metric}_ms", metric,
    )
    plot_setup(
        load_rows(args.result_dir / "setup_summary.csv"), args.output_dir,
        f"{metric}_ms", metric,
    )
    print(f"Saved W2 {metric} figures to {args.output_dir}")


if __name__ == "__main__":
    main()
