#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


PROTOCOLS = ("ssh", "ssh3", "mosh")
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
DISPLAY_PROTOCOL = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
METRICS = {
    "mean": ("mean_ms", "Mean (ms)"),
    "median": ("median_ms", "Median (ms)"),
    "p90": ("p90_ms", "P90 (ms)"),
    "p95": ("p95_ms", "P95 (ms)"),
}


# Doc setup_summary.csv va kiem tra co du ba giao thuc.
def load_summary(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = {row["protocol"]: row for row in rows}
    missing = sorted(set(PROTOCOLS) - set(result))
    if missing:
        raise ValueError(f"setup_summary.csv thieu giao thuc: {missing}")
    return result


# Chuyen cot CSV thanh so thuc.
def number(row, field):
    value = row.get(field, "")
    return float(value) if value not in ("", None) else None


# Ve mot hinh rieng so sanh thoi gian mo phien moi.
def plot_setup(rows, output_dir, metric):
    plt.rcParams.update({"font.family": "DejaVu Serif", "font.size": 11, "savefig.dpi": 300})
    field, ylabel = METRICS[metric]
    values = [number(rows[protocol], field) for protocol in PROTOCOLS]
    present = [value for value in values if value is not None]
    if not present:
        raise ValueError("khong co session_setup_ms hop le de ve")
    y_max = max(present) * 1.25

    fig, ax = plt.subplots(figsize=(8.2, 5.8))
    bars = ax.bar(
        range(len(PROTOCOLS)), [value or 0.0 for value in values], 0.58,
        color=[COLORS[p] for p in PROTOCOLS], edgecolor="black", linewidth=1.0,
        hatch=[HATCHES[p] for p in PROTOCOLS],
    )
    for protocol, bar, value in zip(PROTOCOLS, bars, values):
        rate = number(rows[protocol], "success_rate_pct") or 0.0
        if value is None:
            label, height = f"N/A\n({rate:.1f}%)", y_max * 0.01
        else:
            label, height = f"{value:.1f}", value + y_max * 0.018
            if rate < 99.999:
                label += f"\n({rate:.1f}%)"
        ax.text(bar.get_x() + bar.get_width() / 2, height, label, ha="center", va="bottom", fontsize=10)

    ax.set_xticks(range(len(PROTOCOLS)), [DISPLAY_PROTOCOL[p] for p in PROTOCOLS])
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, y_max)
    ax.set_title(f"Thời gian thiết lập phiên {metric.upper()}")
    ax.grid(True, axis="y", color="#c8c8c8", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(
        handles=[Patch(facecolor=COLORS[p], edgecolor="black", hatch=HATCHES[p], label=DISPLAY_PROTOCOL[p]) for p in PROTOCOLS],
        loc="upper center", ncol=3, frameon=True,
    )
    fig.text(0.5, 0.025, "Mỗi mẫu: spawn client → nhận shell prompt đầu tiên; mỗi mẫu dùng một phiên mới.", ha="center", fontsize=9)
    fig.subplots_adjust(left=0.12, right=0.98, top=0.88, bottom=0.14)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / f"figure_5_session_setup_{metric}"
    fig.savefig(stem.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return stem


# Doc tham so va tao hinh setup.
def main():
    parser = argparse.ArgumentParser(description="Ve thoi gian session setup rieng")
    parser.add_argument("summary", nargs="?", default="artifacts/results/setup_summary.csv")
    parser.add_argument("output_dir", nargs="?", default="artifacts/figures")
    parser.add_argument("--metric", choices=tuple(METRICS), default="median")
    args = parser.parse_args()
    stem = plot_setup(load_summary(Path(args.summary)), Path(args.output_dir), args.metric)
    print(f"Da tao {stem}.png va {stem}.pdf")


if __name__ == "__main__":
    main()
