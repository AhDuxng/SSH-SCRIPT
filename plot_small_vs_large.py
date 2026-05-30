#!/usr/bin/env python3
"""So sánh 3 giao thức (ssh, mosh, ssh3) theo nhóm lệnh output nhỏ vs lớn.

Nhóm OUTPUT NHỎ (4 lệnh): df -h, grep -n root /etc/passwd, ls (W1) + git status (W4)
Nhóm OUTPUT LỚN (3 lệnh): ps aux (W1) + find /, docker logs (W4)

Mỗi ô trong bảng là mean của các mean lệnh trong cùng nhóm.
"""
import csv
import os
import statistics
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

ROOT = "/home/twan/NETWORK/COMPARE_MOSH_SSH_SSH3/w3/SSH-SCRIPT"
OUT_DIR = os.path.join(ROOT, "compare_charts")
os.makedirs(OUT_DIR, exist_ok=True)

SCENARIOS = ["default", "low", "medium", "high"]
PROTOS = ["ssh", "mosh", "ssh3"]
COLORS = {"ssh": "#1f77b4", "mosh": "#2ca02c", "ssh3": "#ff7f0e"}

SMALL_W1 = ["df -h", "grep -n root /etc/passwd", "ls"]
SMALL_W4 = ["git status"]
LARGE_W1 = ["ps aux"]
LARGE_W4 = ["find /", "docker logs $(docker ps -q | head -n 1)"]


def read_csv(path, warmup_filter=False):
    data = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "ok":
                continue
            if warmup_filter and row.get("warmup") == "1":
                continue
            data[(row["protocol"], row["command"])].append(float(row["latency_ms"]))
    return data


def collect_group_means():
    """Trả về dict[group][scenario][proto] = mean of per-command means."""
    out = {"small": {sc: {} for sc in SCENARIOS},
           "large": {sc: {} for sc in SCENARIOS}}
    for sc in SCENARIOS:
        w1 = read_csv(f"{ROOT}/w1/w1_results/{sc}/w1_line_log.csv", warmup_filter=True)
        w4 = read_csv(f"{ROOT}/w4/{sc}/w4_results/w4_line_log.csv")
        for p in PROTOS:
            small_means = (
                [statistics.mean(w1[(p, c)]) for c in SMALL_W1] +
                [statistics.mean(w4[(p, c)]) for c in SMALL_W4]
            )
            large_means = (
                [statistics.mean(w1[(p, c)]) for c in LARGE_W1] +
                [statistics.mean(w4[(p, c)]) for c in LARGE_W4]
            )
            out["small"][sc][p] = sum(small_means) / len(small_means)
            out["large"][sc][p] = sum(large_means) / len(large_means)
    return out


def grouped_bar(ax, data, title, ylabel, log_scale=False):
    x = np.arange(len(SCENARIOS))
    width = 0.26
    for i, p in enumerate(PROTOS):
        vals = [data[sc][p] for sc in SCENARIOS]
        offset = (i - 1) * width
        bars = ax.bar(x + offset, vals, width, label=p.upper(),
                      color=COLORS[p], edgecolor="black", linewidth=0.5)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}",
                    ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([sc.upper() for sc in SCENARIOS])
    ax.set_xlabel("Kịch bản mạng")
    ax.set_ylabel(ylabel)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", framealpha=0.9)
    if log_scale:
        ax.set_yscale("log")


def main():
    g = collect_group_means()

    # Figure 1: Small output
    fig, ax = plt.subplots(figsize=(9, 5.5))
    grouped_bar(ax, g["small"],
                "Output NHỎ — Mean của (df -h, grep -n root, ls, git status)",
                "Latency trung bình (ms)")
    plt.tight_layout()
    out1 = os.path.join(OUT_DIR, "small_output_comparison.png")
    plt.savefig(out1, dpi=150)
    plt.close()
    print(f"[saved] {out1}")

    # Figure 2: Large output (linear)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    grouped_bar(ax, g["large"],
                "Output LỚN — Mean của (ps aux, find /, docker logs)",
                "Latency trung bình (ms)")
    plt.tight_layout()
    out2 = os.path.join(OUT_DIR, "large_output_comparison.png")
    plt.savefig(out2, dpi=150)
    plt.close()
    print(f"[saved] {out2}")

    # Figure 3: Large output (log scale, easier to see on default)
    fig, ax = plt.subplots(figsize=(9, 5.5))
    grouped_bar(ax, g["large"],
                "Output LỚN — Mean (log scale)",
                "Latency trung bình (ms, log)", log_scale=True)
    plt.tight_layout()
    out3 = os.path.join(OUT_DIR, "large_output_comparison_log.png")
    plt.savefig(out3, dpi=150)
    plt.close()
    print(f"[saved] {out3}")

    # Figure 4: Combined panel
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5))
    grouped_bar(axes[0], g["small"],
                "Output NHỎ\n(df -h, grep, ls, git status)",
                "Latency trung bình (ms)")
    grouped_bar(axes[1], g["large"],
                "Output LỚN\n(ps aux, find /, docker logs)",
                "Latency trung bình (ms)")
    fig.suptitle("So sánh độ trễ 3 giao thức theo nhóm lệnh output nhỏ vs lớn",
                 fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    out4 = os.path.join(OUT_DIR, "small_vs_large_combined.png")
    plt.savefig(out4, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out4}")

    # Print summary
    for grp in ["small", "large"]:
        print(f"\n=== {grp.upper()} OUTPUT (mean ms) ===")
        print(f"{'Scenario':<10} {'SSH':>10} {'MOSH':>10} {'SSH3':>10}")
        for sc in SCENARIOS:
            d = g[grp][sc]
            print(f"{sc:<10} {d['ssh']:>10.2f} {d['mosh']:>10.2f} {d['ssh3']:>10.2f}")


if __name__ == "__main__":
    main()
