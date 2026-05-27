#!/usr/bin/env python3
"""Plot W1 low scenario: latency and received_pct per protocol per command."""

import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

CSV_PATH = Path("w1_results/low/w1_line_log.csv")
OUTPUT_DIR = Path("w1_results/low")

PROTOCOL_ORDER = ["ssh", "ssh3", "mosh"]
PROTOCOL_COLORS = {"ssh": "#2196F3", "ssh3": "#FF9800", "mosh": "#4CAF50"}
PROTOCOL_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}


def load_data():
    data = defaultdict(lambda: defaultdict(lambda: {"latency": [], "received_pct": []}))
    with CSV_PATH.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["warmup"] == "1" or row["status"] != "ok":
                continue
            protocol = row["protocol"]
            command = row["command"]
            latency = float(row["latency_ms"])
            received = float(row["received_pct"])
            data[command][protocol]["latency"].append(latency)
            data[command][protocol]["received_pct"].append(received)
    return data


def plot_dual(data):
    commands = sorted(data.keys())
    n_cmds = len(commands)
    x = np.arange(n_cmds)
    width = 0.25

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9), sharex=True)
    fig.suptitle("W1 Command Completion — Low Scenario (RTT ≈ 20 ms)", fontsize=13, fontweight="bold")

    # --- Top: Latency ---
    for i, proto in enumerate(PROTOCOL_ORDER):
        means = []
        stds = []
        for cmd in commands:
            vals = data[cmd][proto]["latency"]
            if vals:
                means.append(statistics.mean(vals))
                stds.append(statistics.stdev(vals) if len(vals) > 1 else 0)
            else:
                means.append(0)
                stds.append(0)
        bars = ax1.bar(
            x + i * width, means, width,
            yerr=stds, capsize=3,
            label=PROTOCOL_LABELS[proto],
            color=PROTOCOL_COLORS[proto], alpha=0.85,
        )
        for bar, m in zip(bars, means):
            if m > 0:
                ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                         f"{m:.0f}", ha="center", va="bottom", fontsize=7)

    ax1.set_ylabel("Latency (ms)")
    ax1.set_title("Command Completion Latency (mean ± std)")
    ax1.legend(loc="upper right")
    ax1.grid(axis="y", alpha=0.3)

    # --- Bottom: Received % ---
    for i, proto in enumerate(PROTOCOL_ORDER):
        means = []
        stds = []
        for cmd in commands:
            vals = data[cmd][proto]["received_pct"]
            if vals:
                means.append(statistics.mean(vals))
                stds.append(statistics.stdev(vals) if len(vals) > 1 else 0)
            else:
                means.append(0)
                stds.append(0)
        bars = ax2.bar(
            x + i * width, means, width,
            yerr=stds, capsize=3,
            label=PROTOCOL_LABELS[proto],
            color=PROTOCOL_COLORS[proto], alpha=0.85,
        )
        for bar, m in zip(bars, means):
            ax2.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                     f"{m:.1f}%", ha="center", va="bottom", fontsize=7)

    ax2.set_ylabel("Output Received (%)")
    ax2.set_title("Output Completeness (mean ± std)")
    ax2.set_ylim(0, 115)
    ax2.axhline(100, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
    ax2.legend(loc="upper right")
    ax2.grid(axis="y", alpha=0.3)

    cmd_labels = [c.replace("/etc/passwd", "\n/etc/passwd").replace("-maxdepth", "\n-maxdepth") for c in commands]
    ax2.set_xticks(x + width)
    ax2.set_xticklabels(cmd_labels, fontsize=9)

    plt.tight_layout()
    out_path = OUTPUT_DIR / "w1_low_latency_received_summary.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"Saved: {out_path}")
    plt.close()


def main():
    data = load_data()
    if not data:
        print("No data found. Check CSV path.")
        return
    print(f"Loaded {sum(len(data[c][p]['latency']) for c in data for p in data[c])} samples")
    print(f"Commands: {sorted(data.keys())}")
    print(f"Protocols: {PROTOCOL_ORDER}")
    plot_dual(data)


if __name__ == "__main__":
    main()
