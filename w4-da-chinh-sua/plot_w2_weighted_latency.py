#!/usr/bin/env python3
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_TICKS = ["", "Low", "Medium", "High"]
COMMANDS = [
    "docker logs $(docker ps -q | head -n 1)",
    "ps aux",
]
PROTOS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
SCENARIO_LABELS = {
    "default": "VPN",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}
COMMAND_LABELS = {
    "docker logs $(docker ps -q | head -n 1)": "docker logs",
    "ps aux": "ps aux",
}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#d4f4ff", "ssh3": "#fab9ba", "mosh": "#c2fac0"}
HATCHES = {"ssh": "////", "ssh3": "////", "mosh": "\\\\\\\\"}


def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def stats(values):
    if not values:
        return {"n": 0, "mean": 0.0, "std": 0.0, "ci95": 0.0}
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    ci95 = 1.96 * std / math.sqrt(len(values)) if len(values) > 1 else 0.0
    return {"n": len(values), "mean": statistics.mean(values), "std": std, "ci95": ci95}


def load_weighted_values():
    values = defaultdict(list)
    per_command = defaultdict(list)
    sources = {}
    for scenario in SCENARIOS:
        path = ROOT / scenario / "w4_results" / "w4_line_log.csv"
        sources[scenario] = path
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                command = row.get("command")
                proto = row.get("protocol")
                latency = safe_float(row.get("latency_ms"))
                if command in COMMANDS and proto in PROTOS and latency is not None:
                    values[(scenario, proto)].append(latency)
                    per_command[(scenario, command, proto)].append(latency)
    return values, per_command, sources


def add_network_region_labels(ax):
    trans = ax.get_xaxis_transform()
    ax.axvline(0.5, color="0.7", linestyle="--", linewidth=1.0, zorder=1)
    ax.text(0, -0.08, "VPN", transform=trans, ha="center", va="top", fontsize=12, color="0.05", clip_on=False)
    ax.text(2, -0.14, "Controlled emulation", transform=trans, ha="center", va="top", fontsize=10, color="0", clip_on=False)


def draw_chart(values):
    fig, ax = plt.subplots(figsize=(6.8, 4.3))
    x = np.arange(len(SCENARIOS))
    width = 0.1
    spacing = 0.01
    max_top = 0.0

    for i, proto in enumerate(PROTOS):
        means = []
        errs = []
        ns = []
        for scenario in SCENARIOS:
            st = stats(values[(scenario, proto)])
            means.append(st["mean"])
            errs.append(st["ci95"])
            ns.append(st["n"])
        max_top = max(max_top, max((m + e for m, e in zip(means, errs)), default=0.0))
        offset = (i - 1) * (width + spacing)
        ax.bar(
            x + offset,
            means,
            width,
            facecolor="white",
            edgecolor=HATCH_COLORS[proto],
            hatch=HATCHES[proto],
            linewidth=0,
            zorder=2,
        )
        bars = ax.bar(
            x + offset,
            means,
            width,
            label=PROTO_LABELS[proto],
            facecolor="none",
            edgecolor=COLORS[proto],
            linewidth=1.2,
            yerr=errs,
            ecolor="black",
            capsize=3,
            error_kw={"linewidth": 0.8},
            zorder=3,
        )
        for bar, mean, err, n in zip(bars, means, errs, ns):
            if n == 0:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                mean + err + max(1.0, max_top * 0.015),
                f"{mean:.0f}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=COLORS[proto],
            )

    add_network_region_labels(ax)
    ax.set_title("W2 Weighted Latency", fontsize=12, fontweight="bold", pad=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_TICKS, fontsize=10)
    ax.tick_params(axis="y", labelsize=10)
    ax.grid(axis="y", linestyle="--", alpha=0.45, zorder=0)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.16), ncol=3, framealpha=0.9, fontsize=10)
    ax.set_ylim(0, max(1.0, max_top * 1.18))
    fig.subplots_adjust(bottom=0.22, top=0.78)
    return fig


def draw_scenario_chart(scenario, per_command):
    fig, ax = plt.subplots(figsize=(7.0, 4.3))
    x = np.arange(len(COMMANDS)) * 0.50
    width = 0.1
    spacing = 0.01
    max_top = 0.0

    for i, proto in enumerate(PROTOS):
        means = []
        errs = []
        ns = []
        for command in COMMANDS:
            st = stats(per_command[(scenario, command, proto)])
            means.append(st["mean"])
            errs.append(st["ci95"])
            ns.append(st["n"])
        max_top = max(max_top, max((m + e for m, e in zip(means, errs)), default=0.0))
        offset = (i - 1) * (width + spacing)
        ax.bar(
            x + offset,
            means,
            width,
            facecolor="white",
            edgecolor=HATCH_COLORS[proto],
            hatch=HATCHES[proto],
            linewidth=0,
            zorder=2,
        )
        bars = ax.bar(
            x + offset,
            means,
            width,
            label=PROTO_LABELS[proto],
            facecolor="none",
            edgecolor=COLORS[proto],
            linewidth=1.2,
            yerr=errs,
            ecolor="black",
            capsize=3,
            error_kw={"linewidth": 0.8},
            zorder=3,
        )
        for bar, mean, err, n in zip(bars, means, errs, ns):
            if n == 0:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                mean + err + max(1.0, max_top * 0.015),
                f"{mean:.0f}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=COLORS[proto],
            )

    ax.set_title(f"W2 Latency - {SCENARIO_LABELS[scenario]}", fontsize=12, fontweight="bold", pad=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels([COMMAND_LABELS[c] for c in COMMANDS], fontsize=10)
    ax.tick_params(axis="y", labelsize=10)
    ax.grid(axis="y", linestyle="--", alpha=0.45, zorder=0)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.16), ncol=3, framealpha=0.9, fontsize=10)
    ax.set_ylim(0, max(1.0, max_top * 1.18))
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    return fig


def write_summary(values, per_command, sources):
    summary_path = ROOT / "w2_weighted_latency_summary.csv"
    detail_path = ROOT / "w2_weighted_latency_by_command.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scenario", "protocol", "n", "mean", "std", "ci95_half_width", "commands", "source_file"])
        for scenario in SCENARIOS:
            for proto in PROTOS:
                st = stats(values[(scenario, proto)])
                writer.writerow([
                    scenario,
                    proto,
                    st["n"],
                    st["mean"],
                    st["std"],
                    st["ci95"],
                    "; ".join(COMMANDS),
                    sources[scenario],
                ])
    with detail_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scenario", "command", "protocol", "n", "mean", "std", "ci95_half_width", "source_file"])
        for scenario in SCENARIOS:
            for command in COMMANDS:
                for proto in PROTOS:
                    st = stats(per_command[(scenario, command, proto)])
                    writer.writerow([scenario, command, proto, st["n"], st["mean"], st["std"], st["ci95"], sources[scenario]])
    print(f"[saved] {summary_path}")
    print(f"[saved] {detail_path}")


def main():
    values, per_command, sources = load_weighted_values()
    fig = draw_chart(values)
    png = ROOT / "w2_weighted_protocol_latency.png"
    pdf = ROOT / "w2_weighted_protocol_latency.pdf"
    fig.savefig(png, dpi=220, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {png}")
    print(f"[saved] {pdf}")
    for scenario in SCENARIOS:
        fig = draw_scenario_chart(scenario, per_command)
        png = ROOT / f"w2_{scenario}_command_latency.png"
        pdf = ROOT / f"w2_{scenario}_command_latency.pdf"
        fig.savefig(png, dpi=220, bbox_inches="tight")
        fig.savefig(pdf, bbox_inches="tight")
        plt.close(fig)
        print(f"[saved] {png}")
        print(f"[saved] {pdf}")
    write_summary(values, per_command, sources)


if __name__ == "__main__":
    main()
