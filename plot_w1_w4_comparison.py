#!/usr/bin/env python3
"""So sánh 3 giao thức (mosh, ssh, ssh3) trên W1 và W4 theo 4 kịch bản mạng.

W1: MoM của 4 lệnh (df -h, grep, ls, ps aux) — loại warmup
W4: latency mean của 2 lệnh (git status, docker logs)
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


def read_latency(csv_path, warmup_filter=False):
    data = defaultdict(list)
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "ok":
                continue
            if warmup_filter and row.get("warmup") == "1":
                continue
            data[(row["protocol"], row["command"])].append(float(row["latency_ms"]))
    return data


def collect_w1():
    """Returns dict[scenario][proto] = MoM(4 commands)"""
    cmds = ["df -h", "grep -n root /etc/passwd", "ls", "ps aux"]
    out = {sc: {} for sc in SCENARIOS}
    for sc in SCENARIOS:
        path = f"{ROOT}/w1/w1_results/{sc}/w1_line_log.csv"
        data = read_latency(path, warmup_filter=True)
        for p in PROTOS:
            means = [statistics.mean(data[(p, c)]) for c in cmds]
            out[sc][p] = sum(means) / len(means)
    return out


def collect_w4():
    """Returns dict[command][scenario][proto] = mean latency"""
    cmds = {
        "git status": "git status",
        "docker logs": "docker logs $(docker ps -q | head -n 1)",
    }
    out = {short: {sc: {} for sc in SCENARIOS} for short in cmds}
    for sc in SCENARIOS:
        path = f"{ROOT}/w4/{sc}/w4_results/w4_line_log.csv"
        data = read_latency(path)
        for short, full in cmds.items():
            for p in PROTOS:
                vals = data.get((p, full), [])
                out[short][sc][p] = statistics.mean(vals) if vals else 0
    return out


def grouped_bar(ax, scenario_data, title, ylabel, log_scale=False):
    """scenario_data: dict[scenario][proto] = value"""
    x = np.arange(len(SCENARIOS))
    width = 0.26
    for i, p in enumerate(PROTOS):
        vals = [scenario_data[sc][p] for sc in SCENARIOS]
        offset = (i - 1) * width
        bars = ax.bar(x + offset, vals, width, label=p.upper(), color=COLORS[p],
                      edgecolor="black", linewidth=0.5)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(x)
    ax.set_xticklabels([sc.upper() for sc in SCENARIOS])
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Kịch bản mạng")
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", framealpha=0.9)
    if log_scale:
        ax.set_yscale("log")


def main():
    w1 = collect_w1()
    w4 = collect_w4()

    # === Figure 1: W1 MoM ===
    fig, ax = plt.subplots(figsize=(9, 5.5))
    grouped_bar(ax, w1,
                "W1 — Mean-of-Means của 4 lệnh (df -h, grep, ls, ps aux)",
                "Latency trung bình (ms)")
    plt.tight_layout()
    out1 = os.path.join(OUT_DIR, "w1_mom_comparison.png")
    plt.savefig(out1, dpi=150)
    plt.close()
    print(f"[saved] {out1}")

    # === Figure 2: W4 — git status + docker logs (2 subplots) ===
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
    grouped_bar(axes[0], w4["git status"],
                "W4 — git status",
                "Latency trung bình (ms)")
    grouped_bar(axes[1], w4["docker logs"],
                "W4 — docker logs (\\$(docker ps -q | head -n 1))",
                "Latency trung bình (ms)")
    plt.tight_layout()
    out2 = os.path.join(OUT_DIR, "w4_commands_comparison.png")
    plt.savefig(out2, dpi=150)
    plt.close()
    print(f"[saved] {out2}")

    # === Figure 3: Combined panel ===
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    grouped_bar(axes[0], w1,
                "W1 — MoM (4 lệnh nhẹ)",
                "Latency trung bình (ms)")
    grouped_bar(axes[1], w4["git status"],
                "W4 — git status",
                "Latency trung bình (ms)")
    grouped_bar(axes[2], w4["docker logs"],
                "W4 — docker logs",
                "Latency trung bình (ms)")
    fig.suptitle("So sánh độ trễ 3 giao thức (SSH / MOSH / SSH3) — W1 & W4",
                 fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    out3 = os.path.join(OUT_DIR, "w1_w4_combined.png")
    plt.savefig(out3, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out3}")

    # Print summary tables to stdout
    print("\n=== W1 MoM (ms) ===")
    print(f"{'Scenario':<10} {'SSH':>10} {'MOSH':>10} {'SSH3':>10}")
    for sc in SCENARIOS:
        print(f"{sc:<10} {w1[sc]['ssh']:>10.2f} {w1[sc]['mosh']:>10.2f} {w1[sc]['ssh3']:>10.2f}")

    for cmd in ["git status", "docker logs"]:
        print(f"\n=== W4 {cmd} (ms) ===")
        print(f"{'Scenario':<10} {'SSH':>10} {'MOSH':>10} {'SSH3':>10}")
        for sc in SCENARIOS:
            d = w4[cmd][sc]
            print(f"{sc:<10} {d['ssh']:>10.2f} {d['mosh']:>10.2f} {d['ssh3']:>10.2f}")


if __name__ == "__main__":
    main()
