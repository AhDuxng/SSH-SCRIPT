#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


PROTOCOLS = ("ssh", "ssh3", "mosh")
TARGETS = ("vim", "nano")
PROFILES = ("c0_only", "c0_bg4", "c0_bg4_heavy")
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}
DISPLAY_PROTOCOL = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
DISPLAY_TARGET = {"vim": "Vim", "nano": "Nano"}
DISPLAY_PROFILE = {"c0_only": "Không tải", "c0_bg4": "4 tải · 100 KiB/s", "c0_bg4_heavy": "4 tải · 1 MiB/s"}
METRICS = {
    "mean": ("mean_ms", "Mean (ms)"),
    "median": ("median_ms", "Median (ms)"),
    "p90": ("p90_ms", "P90 (ms)"),
    "p95": ("p95_ms", "P95 (ms)"),
}


# Doc summary va kiem tra du 18 to hop Vim/Nano.
def load_summary(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = {(row["protocol"], row["target"], row["profile"]): row for row in rows}
    expected = {
        (protocol, target, profile)
        for protocol in PROTOCOLS
        for target in TARGETS
        for profile in PROFILES
    }
    missing = sorted(expected - set(result))
    if missing:
        raise ValueError(f"summary.csv thieu cac to hop: {missing}")
    return result


# Chuyen mot cot CSV thanh so thuc.
def number(row, field):
    value = row.get(field, "")
    return float(value) if value not in ("", None) else None


# Ghi gia tri va ty le hoan thanh len tung cot.
def annotate_bar(ax, bar, value, success_rate, y_max):
    center = bar.get_x() + bar.get_width() / 2
    label = f"{value:.1f}"
    if success_rate < 99.999:
        label += f"\n({success_rate:.1f}%)"
    ax.text(
        center,
        value + y_max * 0.012,
        label,
        ha="center",
        va="bottom",
        fontsize=7.6,
        linespacing=0.9,
    )


# Ve SSH, SSH3 va Mosh chung tren mot bieu do cot.
def plot_combined(rows: dict, output_dir: Path, metric: str):
    plt.rcParams.update({
        "font.family": "DejaVu Serif",
        "font.size": 10,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
        "legend.fontsize": 10,
        "savefig.dpi": 300,
    })
    scenarios = [(target, profile) for target in TARGETS for profile in PROFILES]
    centers = list(range(len(scenarios)))
    labels = [f"{DISPLAY_TARGET[target]}\n{DISPLAY_PROFILE[profile]}" for target, profile in scenarios]
    field, ylabel = METRICS[metric]
    values = [number(rows[(protocol, *scenario)], field) for scenario in scenarios for protocol in PROTOCOLS]
    y_max = max(value for value in values if value is not None) * 1.22

    fig, ax = plt.subplots(figsize=(12.5, 7.2))
    width = 0.24
    offsets = {"ssh": -width, "ssh3": 0.0, "mosh": width}
    for protocol in PROTOCOLS:
        positions = [center + offsets[protocol] for center in centers]
        protocol_values = [number(rows[(protocol, *scenario)], field) for scenario in scenarios]
        success_rates = [number(rows[(protocol, *scenario)], "success_rate_pct") for scenario in scenarios]
        bars = ax.bar(
            positions,
            protocol_values,
            width,
            color=COLORS[protocol],
            edgecolor="black",
            linewidth=1.0,
            hatch=HATCHES[protocol],
        )
        for bar, value, success_rate in zip(bars, protocol_values, success_rates):
            annotate_bar(ax, bar, value, success_rate, y_max)

    for boundary in (2.5,):
        ax.axvline(boundary, color="#777777", linewidth=0.9, linestyle="--")
    ax.set_xticks(centers, labels)
    ax.set_ylabel(ylabel)
    ax.set_ylim(0, y_max)
    ax.set_title(f"Độ trễ tương tác {metric.upper()} – SSH, SSH3 và Mosh")
    ax.grid(True, axis="y", color="#c8c8c8", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_linewidth(1.0)
    ax.legend(
        handles=[
            Patch(facecolor=COLORS[p], edgecolor="black", hatch=HATCHES[p], label=DISPLAY_PROTOCOL[p])
            for p in PROTOCOLS
        ],
        loc="upper center",
        ncol=3,
        frameon=True,
    )
    fig.text(
        0.5,
        0.025,
        "Metric tính trên các ký tự thành công; nhãn trong cột là tỷ lệ hoàn thành. Mosh dùng --predict=always.",
        ha="center",
        fontsize=9,
    )
    fig.subplots_adjust(left=0.07, right=0.99, top=0.91, bottom=0.18)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_dir / f"figure_4_{metric}_results"
    fig.savefig(stem.with_suffix(".png"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    return stem


# Doc tham so va tao mot hinh chung cho metric da chon.
def main():
    parser = argparse.ArgumentParser(description="Ve SSH, SSH3 va Mosh tren cung mot hinh")
    parser.add_argument("summary", nargs="?", default="artifacts/results/summary.csv")
    parser.add_argument("output_dir", nargs="?", default="artifacts/figures")
    parser.add_argument("--metric", choices=tuple(METRICS), default="median")
    args = parser.parse_args()
    stem = plot_combined(load_summary(Path(args.summary)), Path(args.output_dir), args.metric)
    print(f"Da tao {stem}.png va {stem}.pdf")


if __name__ == "__main__":
    main()
