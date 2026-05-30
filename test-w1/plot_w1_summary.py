#!/usr/bin/env python3
"""
W1 benchmark: combined summary table + 3 charts
  - Chart 1: Grouped bar — mean latency per command × protocol (4 subplots by scenario)
  - Chart 2: Line chart  — overall mean latency across scenarios
  - Chart 3: Heatmap     — received_pct per command × protocol (4 subplots by scenario)
"""
import csv
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "w1_results_trungnt")
SCENARIOS = ["default", "low", "medium", "high"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
COLORS = {"ssh": "#2196F3", "ssh3": "#FF9800", "mosh": "#4CAF50"}
OUT_DIR = os.path.join(BASE, "_cross_scenario")

COMMAND_ORDER = [
    "ls",
    "df -h",
    "ps aux",
    "grep -n root /etc/passwd",
    "grep -rn root /etc",
    "cat /proc/meminfo",
    "find /usr -maxdepth 3",
]

CMD_LABELS = {
    "ls": "ls",
    "df -h": "df -h",
    "ps aux": "ps aux",
    "grep -n root /etc/passwd": "grep -n",
    "grep -rn root /etc": "grep -rn",
    "cat /proc/meminfo": "cat meminfo",
    "find /usr -maxdepth 3": "find /usr",
}

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data():
    data = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"lats": [], "rcvs": []})))
    for sc in SCENARIOS:
        path = os.path.join(BASE, sc, "w1_line_log.csv")
        if not os.path.exists(path):
            print(f"  [warn] missing: {path}")
            continue
        with open(path) as f:
            for row in csv.DictReader(f):
                if row.get("warmup", "0") == "1":
                    continue
                if row.get("status", "") != "ok":
                    continue
                proto = row["protocol"]
                cmd = row["command"]
                data[sc][proto][cmd]["lats"].append(float(row["latency_ms"]))
                rcv_raw = row.get("received_pct", "")
                data[sc][proto][cmd]["rcvs"].append(float(rcv_raw) if rcv_raw else 0.0)
    return data


def get_commands(data):
    all_cmds = set()
    for sc in SCENARIOS:
        for proto in PROTOCOLS:
            all_cmds.update(data[sc][proto].keys())
    return [c for c in COMMAND_ORDER if c in all_cmds] + sorted(all_cmds - set(COMMAND_ORDER))


def mean_or_none(lst):
    return sum(lst) / len(lst) if lst else None


def stats(data, sc, proto, cmd):
    d = data[sc][proto].get(cmd, {"lats": [], "rcvs": []})
    return mean_or_none(d["lats"]), mean_or_none(d["rcvs"])


def overall_stats(data, sc, proto, cmds):
    lats, rcvs = [], []
    for cmd in cmds:
        d = data[sc][proto].get(cmd, {"lats": [], "rcvs": []})
        lats.extend(d["lats"])
        rcvs.extend(d["rcvs"])
    return mean_or_none(lats), mean_or_none(rcvs)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------
def print_table(data, cmds):
    CMD_W = 30
    COL = 11  # "999.9 100.0" = 11 chars

    print()
    print("=" * (CMD_W + (COL + 2) * len(PROTOCOLS) * len(SCENARIOS) + 2))
    print("W1 BENCHMARK SUMMARY — Mean Latency (ms) & Received % (non-warmup, status=ok)")
    print("=" * (CMD_W + (COL + 2) * len(PROTOCOLS) * len(SCENARIOS) + 2))

    # Row 1: scenario spans
    hdr1 = " " * CMD_W
    for sc in SCENARIOS:
        span = (COL + 2) * len(PROTOCOLS)
        hdr1 += f"  {sc.upper():^{span - 2}}"
    print(hdr1)

    # Row 2: protocol names
    hdr2 = " " * CMD_W
    for _ in SCENARIOS:
        for proto in PROTOCOLS:
            hdr2 += f"  {proto.upper():^{COL}}"
    print(hdr2)

    # Row 3: sub-column labels
    hdr3 = f"{'Command':<{CMD_W}}"
    for _ in SCENARIOS:
        for _ in PROTOCOLS:
            hdr3 += f"  {'ms':>5} {'rcv%':>4}"
    print(hdr3)

    sep = "-" * (CMD_W + (COL + 2) * len(PROTOCOLS) * len(SCENARIOS) + 2)
    print(sep)

    for cmd in cmds:
        row = f"{cmd:<{CMD_W}}"
        for sc in SCENARIOS:
            for proto in PROTOCOLS:
                m, r = stats(data, sc, proto, cmd)
                if m is not None:
                    row += f"  {m:>5.1f} {r:>4.1f}"
                else:
                    row += f"  {'N/A':>5} {'N/A':>4}"
        print(row)

    print(sep)
    row = f"{'OVERALL MEAN':<{CMD_W}}"
    for sc in SCENARIOS:
        for proto in PROTOCOLS:
            m, r = overall_stats(data, sc, proto, cmds)
            if m is not None:
                row += f"  {m:>5.1f} {r:>4.1f}"
            else:
                row += f"  {'N/A':>5} {'N/A':>4}"
    print(row)
    print("=" * (CMD_W + (COL + 2) * len(PROTOCOLS) * len(SCENARIOS) + 2))
    print()


# ---------------------------------------------------------------------------
# Chart 1: Grouped bar — mean latency per command (4 subplots)
# ---------------------------------------------------------------------------
def chart_bar_latency(data, cmds, out_dir):
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), sharey=False)
    fig.suptitle("W1: Mean Command Completion Latency (ms)\nby Protocol & Scenario", fontsize=14, fontweight="bold")

    x = np.arange(len(cmds))
    width = 0.25
    labels = [CMD_LABELS.get(c, c) for c in cmds]

    for idx, sc in enumerate(SCENARIOS):
        ax = axes[idx // 2][idx % 2]
        for i, proto in enumerate(PROTOCOLS):
            vals = [stats(data, sc, proto, cmd)[0] or 0 for cmd in cmds]
            bars = ax.bar(x + (i - 1) * width, vals, width, label=proto.upper(),
                          color=COLORS[proto], alpha=0.85, edgecolor="white", linewidth=0.5)
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                            f"{v:.0f}", ha="center", va="bottom", fontsize=7, rotation=90)

        ax.set_title(f"Scenario: {sc.upper()}", fontsize=12, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9, rotation=20, ha="right")
        ax.set_ylabel("Mean Latency (ms)", fontsize=10)
        ax.set_ylim(0, ax.get_ylim()[1] * 1.25)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.legend(fontsize=9)

    plt.tight_layout()
    out = os.path.join(out_dir, "w1_summary_bar_latency.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out}")


# ---------------------------------------------------------------------------
# Chart 2: Line chart — overall mean latency across scenarios
# ---------------------------------------------------------------------------
def chart_line_overall(data, cmds, out_dir):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("W1: Overall Mean Latency & Received % Across Scenarios", fontsize=13, fontweight="bold")

    sc_labels = [s.upper() for s in SCENARIOS]
    x = np.arange(len(SCENARIOS))

    for proto in PROTOCOLS:
        lats = [overall_stats(data, sc, proto, cmds)[0] or 0 for sc in SCENARIOS]
        rcvs = [overall_stats(data, sc, proto, cmds)[1] or 0 for sc in SCENARIOS]
        ax1.plot(x, lats, marker="o", linewidth=2, markersize=7,
                 color=COLORS[proto], label=proto.upper())
        ax2.plot(x, rcvs, marker="s", linewidth=2, markersize=7,
                 color=COLORS[proto], label=proto.upper())
        for xi, (lat, rcv) in enumerate(zip(lats, rcvs)):
            ax1.annotate(f"{lat:.1f}", (xi, lat), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=8, color=COLORS[proto])
            ax2.annotate(f"{rcv:.1f}%", (xi, rcv), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=8, color=COLORS[proto])

    for ax, ylabel, title in [
        (ax1, "Mean Latency (ms)", "Mean Latency"),
        (ax2, "Received % (%)", "Mean Received %"),
    ]:
        ax.set_xticks(x)
        ax.set_xticklabels(sc_labels, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=11)
        ax.grid(linestyle="--", alpha=0.4)
        ax.legend(fontsize=9)
        ax.set_ylim(bottom=0)

    plt.tight_layout()
    out = os.path.join(out_dir, "w1_summary_line_overall.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out}")


# ---------------------------------------------------------------------------
# Chart 3: Heatmap — received_pct per command × protocol (4 subplots)
# ---------------------------------------------------------------------------
def chart_heatmap_rcv(data, cmds, out_dir):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("W1: Mean Received % by Command & Protocol\n(100% = full output received)", fontsize=13, fontweight="bold")

    labels = [CMD_LABELS.get(c, c) for c in cmds]
    proto_labels = [p.upper() for p in PROTOCOLS]

    for idx, sc in enumerate(SCENARIOS):
        ax = axes[idx // 2][idx % 2]
        matrix = np.array([
            [stats(data, sc, proto, cmd)[1] if stats(data, sc, proto, cmd)[1] is not None else np.nan
             for proto in PROTOCOLS]
            for cmd in cmds
        ])
        im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0, vmax=100)
        ax.set_xticks(range(len(PROTOCOLS)))
        ax.set_xticklabels(proto_labels, fontsize=10)
        ax.set_yticks(range(len(cmds)))
        ax.set_yticklabels(labels, fontsize=9)
        ax.set_title(f"Scenario: {sc.upper()}", fontsize=11, fontweight="bold")

        for r in range(len(cmds)):
            for c in range(len(PROTOCOLS)):
                val = matrix[r, c]
                txt = f"{val:.1f}%" if not np.isnan(val) else "N/A"
                color = "black" if val > 40 else "white"
                ax.text(c, r, txt, ha="center", va="center", fontsize=9, color=color, fontweight="bold")

        plt.colorbar(im, ax=ax, label="Received %", fraction=0.046, pad=0.04)

    plt.tight_layout()
    out = os.path.join(out_dir, "w1_summary_heatmap_rcv.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {out}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Loading data...")
    data = load_data()
    cmds = get_commands(data)
    print(f"  Commands found: {cmds}")

    print_table(data, cmds)

    print("Generating charts...")
    chart_bar_latency(data, cmds, OUT_DIR)
    chart_line_overall(data, cmds, OUT_DIR)
    chart_heatmap_rcv(data, cmds, OUT_DIR)

    print("\nDone.")
