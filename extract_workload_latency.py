#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerTuple
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter, StrMethodFormatter
import numpy as np

from extract_recv_pct import collect_all_recv_pct


ROOT = Path(__file__).resolve().parent
PAPER_FIGS = ROOT / "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs"

SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_LABELS = ["", "Low", "Medium", "High"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#d4f4ff", "ssh3": "#fab9ba", "mosh": "#c2fac0"}
HATCHES = {"ssh": "////", "ssh3": "...", "mosh": "\\\\\\\\"}
BAR_WIDTH = 0.23
BAR_SPACING = 0.04
FONT_SIZES = {
    "axis_label": 8,
    "tick": 7,
    "legend": 7,
    "annotation": 6,
}
ERROR_KW = {
    "ecolor": "0.3",
    "elinewidth": 0.8,
    "capsize": 2.5,
    "capthick": 0.8,
}
SAVEFIG_KW = {"bbox_inches": "tight", "pad_inches": 0.02, "dpi": 300}

# Latency means and 95% confidence intervals are from the existing workload
# summary values used by the paper figures. Do not round these values for
# plotting; display formatting is handled separately by tick formatters.
WORKLOADS = {
    "w1": {
        "title": "W1: Small-output",
        "ylabel": "Latency (ms)",
        "output": "latency_w1_by_scenario.pdf",
        "ylim": 450,
        "yticks": [0, 100, 200, 300, 400],
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
        "ylim": 90000,
        "yticks": [0, 20000, 40000, 60000, 80000],
        "values": {
            "default": {"ssh": (4213.5, 667.04), "ssh3": (47732.6, 7605.23), "mosh": (2402.4, 372.90)},
            "low": {"ssh": (439.0, 61.72), "ssh3": (690.8, 102.05), "mosh": (2287.5, 431.50)},
            "medium": {"ssh": (26353.2, 4015.32), "ssh3": (60207.2, 9346.69), "mosh": (2315.3, 401.70)},
            "high": {"ssh": (36363.8, 4416.85), "ssh3": (74664.5, 10281.36), "mosh": (2467.9, 441.87)},
        },
    },
    "w3_1p": {
        "title": "W3-1p: Single-pane",
        "ylabel": "Latency (ms)",
        "output": "latency_w3_1p_by_scenario.pdf",
        "ylim": 450,
        "yticks": [0, 100, 200, 300, 400],
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
        "ylim": 450,
        "yticks": [0, 100, 200, 300, 400],
        "values": {
            "default": {"ssh": (409.0, 10.02), "ssh3": (393.2, 13.46), "mosh": (16.5, 2.67)},
            "low": {"ssh": (57.6, 7.60), "ssh3": (58.3, 4.80), "mosh": (3.9, 0.58)},
            "medium": {"ssh": (153.5, 12.72), "ssh3": (152.2, 12.17), "mosh": (7.6, 1.24)},
            "high": {"ssh": (301.3, 9.38), "ssh3": (284.6, 13.61), "mosh": (28.0, 3.27)},
        },
    },
}

def protocol_positions(x: np.ndarray, protocol_index: int) -> np.ndarray:
    return x + (protocol_index - 1) * (BAR_WIDTH + BAR_SPACING)


def format_k(value: float, _pos: int) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value / 1000:.0f}k"
    return f"{value:.0f}"


def style_axis(ax, *, hide_x_labels: bool = False) -> None:
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.32, zorder=0)
    ax.grid(axis="x", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.8)
    ax.spines["bottom"].set_linewidth(0.8)
    ax.tick_params(axis="both", direction="out", length=3.2, width=0.8)
    if hide_x_labels:
        ax.tick_params(axis="x", labelbottom=False, length=0)


def draw_latency_bars(ax, key: str) -> None:
    x = np.arange(len(SCENARIOS))
    for i, protocol in enumerate(PROTOCOLS):
        means = [WORKLOADS[key]["values"][scenario][protocol][0] for scenario in SCENARIOS]
        errors = [WORKLOADS[key]["values"][scenario][protocol][1] for scenario in SCENARIOS]
        positions = protocol_positions(x, i)
        ax.bar(
            positions,
            means,
            BAR_WIDTH,
            facecolor="white",
            edgecolor=HATCH_COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=0,
            zorder=2,
        )
        ax.bar(
            protocol_positions(x, i),
            means,
            BAR_WIDTH,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.2,
            yerr=errors,
            error_kw=ERROR_KW,
            zorder=3,
        )


def draw_recv_bars(ax, key: str, recv_stats) -> None:
    x = np.arange(len(SCENARIOS))
    for i, protocol in enumerate(PROTOCOLS):
        means = [recv_stats[key][scenario][protocol]["mean"] for scenario in SCENARIOS]
        errors = [recv_stats[key][scenario][protocol]["ci95"] for scenario in SCENARIOS]
        valid = np.array([not np.isnan(mean) for mean in means])
        if not np.all(valid):
            missing = [SCENARIO_LABELS[idx] for idx, ok in enumerate(valid) if not ok]
            print(f"[warn] missing received-byte data for {key}/{protocol}: {', '.join(missing)}")
        means_arr = np.array(means, dtype=float)
        errors_arr = np.array(errors, dtype=float)
        yerr = np.array([
            [min(err, max(0.0, mean)) for mean, err in zip(means_arr[valid], errors_arr[valid])],
            [min(err, max(0.0, 100.0 - mean)) for mean, err in zip(means_arr[valid], errors_arr[valid])],
        ])
        positions = protocol_positions(x, i)[valid]
        ax.bar(
            positions,
            means_arr[valid],
            BAR_WIDTH,
            facecolor="white",
            edgecolor=HATCH_COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=0,
            zorder=2,
        )
        bars = ax.bar(
            positions,
            means_arr[valid],
            BAR_WIDTH,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.0,
            yerr=yerr,
            error_kw=ERROR_KW,
            zorder=3,
        )
        for bar, mean, err_high in zip(bars, means_arr[valid], yerr[1]):
            if mean >= 10.0:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(101.5, mean + err_high + 2.6),
                f"{mean:.1f}",
                ha="center",
                va="bottom",
                fontsize=FONT_SIZES["annotation"],
                color=COLORS[protocol],
                zorder=6,
            )


def add_network_region_labels(
    ax,
    *,
    fontsize: int = FONT_SIZES["tick"],
    group_y: float = -0.19,
    bracket_y: float = -0.14,
    bracket_tick_y: float = -0.10,
    line_bottom: float = -0,
    show_bracket: bool = True,
    show_labels: bool = True,
) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [line_bottom, 1.0],
        transform=trans,
        color="0.65",
        linestyle=":",
        linewidth=0.6,
        alpha=0.8,
        zorder=1,
        clip_on=False,
    )
    if show_bracket:
        ax.plot(
            [-0.42, -0.42, 0.42, 0.42],
            [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
            transform=trans,
            color="#222222",
            linewidth=0.6,
            zorder=4,
            clip_on=False,
        )
        ax.plot(
            [0.58, 0.58, 3.42, 3.42],
            [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
            transform=trans,
            color="#222222",
            linewidth=0.6,
            zorder=4,
            clip_on=False,
        )
    if show_labels:
        ax.text(0, group_y, "Internet", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False, linespacing=0.9)
        ax.text(2, group_y, "Simulated (Dynamicity level)", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False)


def plot_workload_panel(ax, key: str, *, compact: bool = False, show_title: bool = True) -> None:
    workload = WORKLOADS[key]
    text_size = FONT_SIZES["tick"]
    x = np.arange(len(SCENARIOS))
    add_network_region_labels(
        ax,
        fontsize=text_size,
        group_y=-0.27,
        bracket_y=-0.20,
        bracket_tick_y=-0.15,
        line_bottom=0,
    )

    draw_latency_bars(ax, key)

    if show_title:
        ax.set_title(workload["title"], fontsize=FONT_SIZES["axis_label"], pad=3)
    ax.set_ylabel(workload["ylabel"], fontsize=FONT_SIZES["axis_label"])
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=text_size)
    ax.tick_params(axis="x", pad=5)
    ax.tick_params(axis="y", labelsize=text_size)
    ax.set_ylim(0, workload["ylim"])
    ax.set_yticks(workload["yticks"])
    ax.yaxis.set_major_formatter(FuncFormatter(format_k) if key == "w2" else StrMethodFormatter("{x:.0f}"))
    style_axis(ax)


def plot_recv_pct_panel(ax, key: str, recv_stats, *, compact: bool = False, show_title: bool = True) -> None:
    text_size = FONT_SIZES["tick"]
    x = np.arange(len(SCENARIOS))
    add_network_region_labels(
        ax,
        fontsize=text_size,
        group_y=-0.27,
        bracket_y=-0.20,
        bracket_tick_y=-0.15,
        line_bottom=0,
    )

    for i, protocol in enumerate(PROTOCOLS):
        means = [recv_stats[key][scenario][protocol]["mean"] for scenario in SCENARIOS]
        errors = [recv_stats[key][scenario][protocol]["ci95"] for scenario in SCENARIOS]
        valid = np.array([not np.isnan(mean) for mean in means])
        means_arr = np.array(means, dtype=float)
        errors_arr = np.array(errors, dtype=float)
        yerr = np.array([
            [min(err, max(0.0, mean)) for mean, err in zip(means_arr[valid], errors_arr[valid])],
            [min(err, max(0.0, 100.0 - mean)) for mean, err in zip(means_arr[valid], errors_arr[valid])],
        ])
        positions = protocol_positions(x, i)[valid]

        ax.bar(
            positions,
            means_arr[valid],
            BAR_WIDTH,
            facecolor="white",
            edgecolor=HATCH_COLORS[protocol],
            hatch=HATCHES[protocol],
            linewidth=0,
            zorder=2,
        )
        bars = ax.bar(
            positions,
            means_arr[valid],
            BAR_WIDTH,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.0,
            yerr=yerr,
            error_kw=ERROR_KW,
            zorder=3,
        )
        for bar, mean, err_high in zip(bars, means_arr[valid], yerr[1]):
            if mean >= 99.5:
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(103.0, mean + err_high + 1.2),
                f"{mean:.1f}",
                ha="center",
                va="bottom",
                fontsize=FONT_SIZES["annotation"],
                color=COLORS[protocol],
            )

    if show_title:
        ax.set_title("Output completeness", fontsize=FONT_SIZES["axis_label"], pad=3)
    ax.set_ylabel("Received bytes (%)", fontsize=FONT_SIZES["axis_label"])
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=text_size)
    ax.tick_params(axis="x", pad=5)
    ax.tick_params(axis="y", labelsize=text_size)
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    style_axis(ax)


def plot_latency_completeness_panel(lat_ax, recv_ax, key: str, recv_stats) -> None:
    workload = WORKLOADS[key]
    text_size = FONT_SIZES["tick"]
    x = np.arange(len(SCENARIOS))
    draw_latency_bars(lat_ax, key)
    draw_recv_bars(recv_ax, key, recv_stats)

    add_network_region_labels(
        lat_ax,
        fontsize=text_size,
        group_y=-0.19,
        bracket_y=-0.14,
        bracket_tick_y=-0.10,
        line_bottom=0,
        show_bracket=False,
        show_labels=False,
    )
    add_network_region_labels(
        recv_ax,
        fontsize=text_size,
        group_y=-0.44,
        bracket_y=-0.34,
        bracket_tick_y=-0.25,
        line_bottom=0,
    )

    lat_ax.set_ylabel("Latency (ms)", fontsize=FONT_SIZES["axis_label"], labelpad=7)
    recv_ax.set_ylabel("Received bytes (%)", fontsize=FONT_SIZES["axis_label"], labelpad=7)
    lat_ax.set_ylim(0, workload["ylim"])
    lat_ax.set_yticks(workload["yticks"])
    lat_ax.yaxis.set_major_formatter(FuncFormatter(format_k) if key == "w2" else StrMethodFormatter("{x:.0f}"))
    recv_ax.set_ylim(0, 112)
    recv_ax.set_yticks([0, 25, 50, 75, 100])
    recv_ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))

    for ax in (lat_ax, recv_ax):
        ax.set_xlim(-0.55, len(SCENARIOS) - 0.45)
        ax.set_xticks(x)
        ax.tick_params(axis="y", labelsize=text_size)
        style_axis(ax, hide_x_labels=ax is lat_ax)

    recv_ax.set_xticklabels(SCENARIO_LABELS, fontsize=text_size)
    recv_ax.tick_params(axis="x", pad=5, labelsize=text_size)

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


def save_figure(fig, output_pdf: Path) -> None:
    fig.savefig(output_pdf, **SAVEFIG_KW)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, **SAVEFIG_KW)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def plot_workload(key: str, show_legend: bool, show_title: bool = False) -> None:
    output_pdf = PAPER_FIGS / WORKLOADS[key]["output"]
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(3.45, 2.25), dpi=300)
    plot_workload_panel(ax, key, compact=False, show_title=show_title)
    if show_legend:
        fig.legend(
            handles=legend_handles(),
            labels=legend_labels(),
            handler_map={tuple: HandlerTuple(ndivide=1, pad=0)},
            ncol=3,
            frameon=True,
            framealpha=0.9,
            loc="center",
            bbox_to_anchor=(0.5, 0.995),
            fontsize=12,
            handlelength=1.7,
            handleheight=0.7,
            columnspacing=1.4,
            borderpad=0.35,
        )
    fig.subplots_adjust(left=0.14, right=0.98, bottom=0.30, top=0.97)
    save_figure(fig, output_pdf)
    plt.close(fig)


def plot_recv_pct_workload(key: str, recv_stats) -> None:
    output_pdf = PAPER_FIGS / f"recv_pct_{key}_by_scenario.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(3.45, 2.25), dpi=300)
    plot_recv_pct_panel(ax, key, recv_stats, compact=False, show_title=False)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.30, top=0.97)
    save_figure(fig, output_pdf)
    plt.close(fig)


def plot_latency_completeness_workload(key: str, recv_stats) -> None:
    output_pdf = PAPER_FIGS / f"latency_completeness_{key}_by_scenario.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, (lat_ax, recv_ax) = plt.subplots(
        2,
        1,
        figsize=(3.45, 3.05),
        dpi=300,
        sharex=True,
        gridspec_kw={"height_ratios": [3.0, 2.00], "hspace": 0.24},
    )
    plot_latency_completeness_panel(lat_ax, recv_ax, key, recv_stats)
    fig.align_ylabels([lat_ax, recv_ax])
    fig.subplots_adjust(left=0.18, right=0.985, bottom=0.35, top=0.98)
    save_figure(fig, output_pdf)
    plt.close(fig)


def plot_combined_workloads() -> None:
    output_pdf = PAPER_FIGS / "latency_workloads_by_scenario.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 4, figsize=(13, 5), dpi=180)
    for ax, key in zip(axes, WORKLOADS):
        plot_workload_panel(ax, key, compact=True)

    fig.legend(
        handles=legend_handles(),
        labels=legend_labels(),
        handler_map={tuple: HandlerTuple(ndivide=1, pad=0)},
        ncol=3,
        frameon=True,
        framealpha=0.9,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.995),
        fontsize=10,
        handlelength=1.7,
        handleheight=0.7,
        columnspacing=1.4,
        borderpad=0.35,
    )
    fig.subplots_adjust(left=0.055, right=0.995, bottom=0.34, top=0.76, wspace=0.4)
    save_figure(fig, output_pdf)
    plt.close(fig)


def plot_workload_legend() -> None:
    output_pdf = PAPER_FIGS / "latency_workload_legend.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 0.62), dpi=300)
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
    save_figure(fig, output_pdf)
    plt.close(fig)


def main() -> int:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Nimbus Sans", "Liberation Sans", "Arial", "DejaVu Sans"],
        "font.size": FONT_SIZES["tick"],
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.7,
        "hatch.linewidth": 0.55,
    })
    recv_stats = collect_all_recv_pct()
    plot_workload_legend()
    for key in WORKLOADS:
        plot_workload(key, show_legend=False, show_title=False)
        if key in recv_stats:
            plot_recv_pct_workload(key, recv_stats)
            plot_latency_completeness_workload(key, recv_stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
