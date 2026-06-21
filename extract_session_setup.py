#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerTuple
from matplotlib.patches import Patch
from matplotlib.ticker import StrMethodFormatter
import numpy as np


ROOT = Path(__file__).resolve().parent
PAPER_FIGS = ROOT / "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs"

SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_LABELS = ["", "Low", "Medium", "High"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#d4f4ff", "ssh3": "#fab9ba", "mosh": "#c2fac0"}
HATCHES = {"ssh": "////", "ssh3": "...", "mosh": "\\\\\\\\"}

SETUP_LATENCY = {
    "default": {"ssh": (2873.3, 187.7), "ssh3": (876.0, 71.1), "mosh": (3280.0, 191.5)},
    "low": {"ssh": (1674.3, 141.5), "ssh3": (982.3, 103.0), "mosh": (2270.5, 174.5)},
    "medium": {"ssh": (2577.1, 138.4), "ssh3": (1294.2, 96.5), "mosh": (3503.6, 200.8)},
    "high": {"ssh": (3960.5, 143.8), "ssh3": (1685.7, 107.0), "mosh": (5161.6, 317.8)},
}


def add_network_region_labels(
    ax,
    *,
    fontsize: int = 21,
    group_y: float = -0.19,
    bracket_y: float = -0.14,
    bracket_tick_y: float = -0.10,
    line_bottom: float = -0,
) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [line_bottom, 1.0],
        transform=trans,
        color="#8c8c8c",
        linestyle="--",
        linewidth=2.5,
        zorder=1,
        clip_on=False,
    )
    ax.plot(
        [-0.42, -0.42, 0.42, 0.42],
        [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
        transform=trans,
        color="#222222",
        linewidth=0.9,
        zorder=4,
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
    ax.text(0, group_y, "Internet", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=500, linespacing=0.9)
    ax.text(2, group_y, "Simulated (Dynamicity level)", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, fontweight=500)


def legend_handles() -> list[tuple[Patch, Patch]]:
    return [
        (
            Patch(
                facecolor="white",
                edgecolor=HATCH_COLORS[protocol],
                hatch=HATCHES[protocol],
                linewidth=0,
            ),
            Patch(
                facecolor="none",
                edgecolor=COLORS[protocol],
                linewidth=1.2,
            ),
        )
        for protocol in PROTOCOLS
    ]


def legend_labels() -> list[str]:
    return [PROTO_LABELS[protocol] for protocol in PROTOCOLS]


def plot_session_setup_legend() -> None:
    output_pdf = PAPER_FIGS / "session_setup_legend.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 0.62), dpi=180)
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])
    ax.legend(
        handles=legend_handles(),
        labels=legend_labels(),
        handler_map={tuple: HandlerTuple(ndivide=1, pad=0)},
        ncol=3,
        frameon=True,
        framealpha=0.9,
        loc="center",
        bbox_to_anchor=(0.56, 0.5),
        borderaxespad=0,
        fontsize=12,
        handlelength=1.7,
        handleheight=0.7,
        columnspacing=1.4,
        borderpad=0.35,
    )
    fig.savefig(output_pdf)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path)
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def plot_session_setup() -> None:
    output_pdf = PAPER_FIGS / "session_setup_bar.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    x = np.arange(len(SCENARIOS))
    width = 0.25
    spacing = 0.02
    ylim = 6750
    ytick_max = 6000

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Nimbus Sans", "Liberation Sans", "Arial", "DejaVu Sans"],
        "font.size": 19,
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
            capsize=5,
            error_kw={"ecolor": "#222222", "elinewidth": 1.0, "capthick": 1.0},
            zorder=3,
        )

    ax.set_ylabel("Time (ms)", fontsize=21, fontweight=500)
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=21, fontweight=500)
    ax.tick_params(axis="x", pad=10)
    ax.set_ylim(0, ylim)
    ax.set_yticks(np.linspace(0, ytick_max, 5))
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontweight(500)
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.55, zorder=0)

    fig.subplots_adjust(left=0.14, right=0.98, bottom=0.25, top=0.98)
    fig.savefig(output_pdf, bbox_inches="tight", pad_inches=0.02)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def main() -> int:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Nimbus Sans", "Liberation Sans", "Arial", "DejaVu Sans"],
        "font.size": 19,
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.9,
        "hatch.linewidth": 0.8,
    })
    plot_session_setup_legend()
    plot_session_setup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
