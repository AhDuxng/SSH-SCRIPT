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

SETUP_LATENCY = {
    "default": {"ssh": (765.0, 39.22), "ssh3": (219.0, 14.17), "mosh": (908.0, 41.24)},
    "low": {"ssh": (811.0, 22.63), "ssh3": (362.0, 23.10), "mosh": (1166.0, 16.63)},
    "medium": {"ssh": (1796.0, 63.29), "ssh3": (645.0, 40.66), "mosh": (2366.0, 130.75)},
    "high": {"ssh": (3565.0, 326.11), "ssh3": (1042.0, 93.30), "mosh": (3856.0, 163.06)},
}


def add_network_region_labels(
    ax,
    *,
    fontsize: int = 14,
    group_y: float = -0.20,
    bracket_y: float = -0.13,
    bracket_tick_y: float = -0.10,
    line_bottom: float = -0.17,
) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [line_bottom, 1.0],
        transform=trans,
        color="#bfbfbf",
        linestyle="--",
        linewidth=1.0,
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
    ax.text(0, group_y, "Real-World", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=520)
    ax.text(2, group_y, "Emulated", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=520)


def plot_session_setup() -> None:
    output_pdf = PAPER_FIGS / "session_setup_bar.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    x = np.arange(len(SCENARIOS))
    width = 0.25
    spacing = 0.02
    ylim = 4400

    plt.rcParams.update({
        "font.size": 11,
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.9,
        "hatch.linewidth": 0.8,
    })

    fig, ax = plt.subplots(figsize=(9, 6), dpi=180)
    add_network_region_labels(ax)

    for i, protocol in enumerate(PROTOCOLS):
        means = [SETUP_LATENCY[scenario][protocol][0] for scenario in SCENARIOS]
        ci95 = [SETUP_LATENCY[scenario][protocol][1] for scenario in SCENARIOS]
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
        ax.bar(
            positions,
            means,
            width,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.2,
            yerr=ci95,
            capsize=3,
            error_kw={"ecolor": "#222222", "elinewidth": 1.0, "capthick": 1.0},
            zorder=3,
        )

    legend_handles = [
        Patch(
            facecolor="white",
            edgecolor=COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=1.2,
            label=PROTO_LABELS[protocol],
        )
        for protocol in PROTOCOLS
    ]
    ax.legend(
        handles=legend_handles,
        ncol=3,
        frameon=True,
        framealpha=0.9,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.18),
        fontsize=17,
        handlelength=1.7,
        handleheight=0.7,
        columnspacing=1.4,
        borderpad=0.35,
    )
    ax.set_ylabel("Time (ms)", fontsize=14)
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=14)
    ax.set_ylim(0, ylim)
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.55, zorder=0)

    fig.subplots_adjust(left=0.11, right=0.85, bottom=0.25, top=0.86)
    fig.savefig(output_pdf, bbox_inches="tight", pad_inches=0.02)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def main() -> int:
    plot_session_setup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
