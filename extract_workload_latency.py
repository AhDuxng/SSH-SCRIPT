#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np


ROOT = Path(__file__).resolve().parent
PAPER_FIGS = ROOT / "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs"

SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_LABELS = ["VPN", "Low", "Medium", "High"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#d4f4ff", "ssh3": "#fab9ba", "mosh": "#c2fac0"}
HATCHES = {"ssh": "////", "ssh3": "...", "mosh": "\\\\\\\\"}

WORKLOADS = {
    "w1": {
        "title": "W1: Small-output",
        "ylabel": "Latency (ms)",
        "output": "latency_w1_by_scenario.pdf",
        "ylim": 430,
        "values": {
            "default": {"ssh": (188.8, 0.96), "ssh3": (177.9, 3.99), "mosh": (190.9, 2.49)},
            "low": {"ssh": (76.1, 0.60), "ssh3": (75.5, 0.33), "mosh": (82.2, 0.51)},
            "medium": {"ssh": (173.6, 8.55), "ssh3": (170.4, 6.05), "mosh": (187.9, 19.03)},
            "high": {"ssh": (367.0, 33.30), "ssh3": (307.5, 14.86), "mosh": (317.3, 24.04)},
        },
    },
    "w2": {
        "title": "W2: Large-output",
        "ylabel": "Latency (ms)",
        "output": "latency_w2_by_scenario.pdf",
        "ylim": 88000,
        "values": {
            "default": {"ssh": (4213.5, 667.04), "ssh3": (47732.6, 7605.23), "mosh": (2402.4, 372.90)},
            "low": {"ssh": (439.0, 61.72), "ssh3": (690.8, 102.05), "mosh": (2287.5, 431.50)},
            "medium": {"ssh": (26353.2, 4015.32), "ssh3": (60207.2, 9346.69), "mosh": (2315.3, 401.70)},
            "high": {"ssh": (36363.8, 476.39), "ssh3": (74664.5, 568.63), "mosh": (2467.9, 423.26)},
        },
    },
    "w3_1p": {
        "title": "W3-1p: Single-pane",
        "ylabel": "Latency (ms)",
        "output": "latency_w3_1p_by_scenario.pdf",
        "ylim": 340,
        "values": {
            "default": {"ssh": (301.0, 4.08), "ssh3": (285.9, 3.82), "mosh": (23.5, 3.06)},
            "low": {"ssh": (26.5, 0.20), "ssh3": (29.8, 0.24), "mosh": (2.6, 0.43)},
            "medium": {"ssh": (117.4, 2.20), "ssh3": (113.9, 1.07), "mosh": (17.5, 1.57)},
            "high": {"ssh": (240.0, 4.35), "ssh3": (229.9, 2.82), "mosh": (19.1, 2.59)},
        },
    },
    "w3_5p": {
        "title": "W3-5p: Five-pane",
        "ylabel": "Latency (ms)",
        "output": "latency_w3_5p_by_scenario.pdf",
        "ylim": 460,
        "values": {
            "default": {"ssh": (409.0, 10.02), "ssh3": (393.2, 13.46), "mosh": (16.5, 2.67)},
            "low": {"ssh": (57.6, 7.60), "ssh3": (58.3, 4.80), "mosh": (3.9, 0.58)},
            "medium": {"ssh": (153.5, 12.72), "ssh3": (152.2, 12.17), "mosh": (7.6, 1.24)},
            "high": {"ssh": (301.3, 9.38), "ssh3": (284.6, 13.61), "mosh": (28.0, 3.27)},
        },
    },
}


def add_network_region_labels(
    ax,
    *,
    fontsize: int = 10,
    group_y: float = -0.30,
    bracket_y: float = -0.14,
    bracket_tick_y: float = -0.15,
    line_bottom: float = -0.36,
) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [line_bottom, 1.0],
        transform=trans,
        color="#9a9a9a",
        linestyle="--",
        linewidth=1.5,
        zorder=1,
        clip_on=False,
    )
    ax.plot(
        [0.58, 0.58, 3.42, 3.42],
        [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
        transform=trans,
        color="#222222",
        linewidth=0.9,
        zorder=4,
        clip_on=False,
    )
    ax.text(0, group_y, "Real", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=520)
    ax.text(2, group_y, "Emulated", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=520)


def plot_workload_panel(ax, key: str, *, compact: bool = False, show_title: bool = True) -> None:
    workload = WORKLOADS[key]
    x = np.arange(len(SCENARIOS))
    width = 0.25
    spacing = 0.02
    add_network_region_labels(
        ax,
        fontsize=12 if compact else 17,
        group_y=-0.23 if compact else -0.30,
        bracket_y=-0.15 if compact else -0.19,
        bracket_tick_y=-0.13 if compact else -0.15,
        line_bottom=-0.15 if compact else -0.36,
    )

    for i, protocol in enumerate(PROTOCOLS):
        means = [workload["values"][scenario][protocol][0] for scenario in SCENARIOS]
        ci95 = [workload["values"][scenario][protocol][1] for scenario in SCENARIOS]
        offset = (i - 1) * (width + spacing)
        positions = x + offset

        ax.bar(
            positions,
            means,
            width,
            facecolor="white",
            edgecolor=HATCH_COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=0,
            zorder=2,
        )
        bars = ax.bar(
            positions,
            means,
            width,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.2,
            yerr=ci95,
            capsize=3,
            error_kw={"ecolor": "#222222", "elinewidth": 0.3, "capthick": 0.3},
            zorder=3,
        )

    if show_title:
        ax.set_title(workload["title"], fontsize=12 if compact else 12, pad=5)
    ax.set_ylabel(workload["ylabel"], fontsize=11 if compact else 12)
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=11 if compact else 11, fontweight=520)
    ax.tick_params(axis="y", labelsize=10 if compact else 11)
    ax.set_ylim(0, workload["ylim"])
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.55, zorder=0)

def legend_handles() -> list[Patch]:
    return [
        Patch(
            facecolor="white",
            edgecolor=COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=1.2,
            label=PROTO_LABELS[protocol],
        )
        for protocol in PROTOCOLS
    ]


def plot_workload(key: str, show_legend: bool, show_title: bool = False) -> None:
    output_pdf = PAPER_FIGS / WORKLOADS[key]["output"]
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(3.8, 2.55), dpi=180)
    plot_workload_panel(ax, key, compact=True, show_title=show_title)
    if show_legend:
        ax.legend(
            handles=legend_handles(),
            ncol=3,
            frameon=True,
            framealpha=0.9,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.18),
        )
    fig.subplots_adjust(left=0.20, right=0.98, bottom=0.26, top=0.82 if show_legend else 0.96)
    fig.savefig(output_pdf)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path)
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def plot_combined_workloads() -> None:
    output_pdf = PAPER_FIGS / "latency_workloads_by_scenario.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 4, figsize=(13, 5), dpi=180)
    for ax, key in zip(axes, WORKLOADS):
        plot_workload_panel(ax, key, compact=True)

    fig.legend(
        handles=legend_handles(),
        ncol=3,
        frameon=True,
        framealpha=0.9,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
    )
    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.38, top=0.76, wspace=0.4)
    fig.savefig(output_pdf, bbox_inches="tight")
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def plot_workload_legend() -> None:
    output_pdf = PAPER_FIGS / "latency_workload_legend.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(4.2, 0.42), dpi=180)
    ax.axis("off")
    ax.legend(
        handles=legend_handles(),
        ncol=3,
        frameon=True,
        framealpha=0.9,
        loc="center",
        borderaxespad=0,
    )
    fig.savefig(output_pdf, bbox_inches="tight", pad_inches=0.02)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def main() -> int:
    plt.rcParams.update({
        "font.size": 11,
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.9,
        "hatch.linewidth": 0.8,
    })
    plot_combined_workloads()
    plot_workload_legend()
    for key in WORKLOADS:
        plot_workload(key, show_legend=False, show_title=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
