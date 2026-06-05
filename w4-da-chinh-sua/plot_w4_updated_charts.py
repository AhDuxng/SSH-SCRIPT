#!/usr/bin/env python3
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_LABELS = {
    "default": "VPN",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}
COMMANDS = [
    "docker logs $(docker ps -q | head -n 1)",
    "ps aux",
]
COMMAND_LABELS = {
    "find /": "find /",
    "docker logs $(docker ps -q | head -n 1)": "docker logs",
    "ps aux": "ps aux",
}
PROTOS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
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


def load_all():
    values = defaultdict(list)
    source_files = {}
    for scenario in SCENARIOS:
        path = ROOT / scenario / "w4_results" / "w4_line_log.csv"
        source_files[scenario] = path
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                proto = row.get("protocol")
                command = row.get("command")
                latency = safe_float(row.get("latency_ms"))
                if proto in PROTOS and command in COMMANDS and latency is not None:
                    values[(scenario, command, proto)].append(latency)
    return values, source_files


def load_scenario(scenario):
    command_sources = {
        "docker logs $(docker ps -q | head -n 1)": REPO_ROOT / "test-w4" / "w4_results" / scenario / "w4_results" / "w4_line_log.csv",
        "ps aux": ROOT / scenario / "w4_results" / "w4_line_log.csv",
    }
    values = defaultdict(list)
    for command, path in command_sources.items():
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "ok":
                    continue
                proto = row.get("protocol")
                latency = safe_float(row.get("latency_ms"))
                if proto in PROTOS and row.get("command") == command and latency is not None:
                    values[(command, proto)].append(latency)
    return command_sources, values


def draw_scenario_chart(scenario, values):
    fig, ax = plt.subplots(figsize=(6.4, 4.3))
    x = np.arange(len(COMMANDS))
    width = 0.24
    spacing = 0.02
    max_top = 0.0

    for i, proto in enumerate(PROTOS):
        means = []
        errs = []
        ns = []
        for command in COMMANDS:
            st = stats(values[(command, proto)])
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
            pad = max(1.0, max_top * 0.015)
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                mean + err + pad,
                f"{mean:.0f}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=COLORS[proto],
            )

    ax.set_title(f"W4 Command Latency - {SCENARIO_LABELS[scenario]}", fontsize=12, fontweight="bold", pad=12)
    ax.set_ylabel("Latency (ms)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels([COMMAND_LABELS[c] for c in COMMANDS], fontsize=10)
    ax.tick_params(axis="y", labelsize=10)
    ax.grid(axis="y", linestyle="--", alpha=0.45, zorder=0)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.16), ncol=3, framealpha=0.9, fontsize=10)
    ax.set_ylim(0, max(1.0, max_top * 1.18))
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    return fig


def main():
    summary_path = ROOT / "w4_updated_latency_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["scenario", "command", "protocol", "n", "mean", "std", "ci95_half_width", "source_file"])
        for scenario in SCENARIOS:
            command_sources, values = load_scenario(scenario)
            fig = draw_scenario_chart(scenario, values)
            png = ROOT / f"w4_{scenario}_protocol_latency.png"
            pdf = ROOT / f"w4_{scenario}_protocol_latency.pdf"
            fig.savefig(png, dpi=220, bbox_inches="tight")
            fig.savefig(pdf, bbox_inches="tight")
            plt.close(fig)
            print(f"[saved] {png}")
            print(f"[saved] {pdf}")

            for command in COMMANDS:
                for proto in PROTOS:
                    st = stats(values[(command, proto)])
                    writer.writerow([
                        scenario,
                        command,
                        proto,
                        st["n"],
                        st["mean"],
                        st["std"],
                        st["ci95"],
                        command_sources[command],
                    ])

    print(f"[saved] {summary_path}")


if __name__ == "__main__":
    main()
