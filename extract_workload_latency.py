#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
import statistics
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
from matplotlib.legend_handler import HandlerTuple
from matplotlib.patches import ConnectionPatch, Patch, Rectangle
from matplotlib.ticker import FuncFormatter, StrMethodFormatter
import numpy as np

from extract_recv_pct import collect_all_recv_pct
from extract_session_setup import (
    BAR_EDGE_LINEWIDTH,
    BAR_FACE_COLOR,
    BAR_SPACING as SESSION_BAR_SPACING,
    BAR_WIDTH as SESSION_BAR_WIDTH,
    COLORS,
    ERROR_BAR_CAPSIZE,
    ERROR_BAR_KW,
    FIG_DPI as SESSION_FIG_DPI,
    HATCHES,
    HATCH_COLORS,
    HATCH_LINEWIDTH,
    PROTOCOLS,
    PROTO_LABELS,
)


ROOT = Path(__file__).resolve().parent
PAPER_FIGS = ROOT / "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs"
W4_RESULTS = ROOT / "test-w4/w4_results_trungnt"
CAT_512K_SELECTED_SOURCE_LOGS = {
    "default": W4_RESULTS / "default" / "w4_line_log.csv",
    "low": W4_RESULTS / "low" / "w4_line_log.csv",
    "medium": W4_RESULTS / "medium" / "w4_line_log.csv",
    "high": W4_RESULTS / "512KB" / "high" / "w4_line_log.csv",
}

SCENARIO_LABELS = ["", "Low", "Medium", "High"]
SCENARIOS = ["default", "low", "medium", "high"]
CAT_512K_KEY = "w2_cat_512KiB"
CAT_512K_SELECTED_KEY = "w2_cat_512KiB_selected_sources"
CAT_512K_COMMAND = "cat /tmp/w4_paths_small.txt"
CAT_100K_KEY = "w2_cat_100KiB"
CAT_100K_COMMAND = "cat /tmp/w4_paths_100kb.txt"
CAT_512K_LATENCY_YTICK_MAX = 19500.0
CAT_512K_LATENCY_INSET_YMAX = 330.0
CAT_100K_LATENCY_INSET_YMAX = 150.0
BAR_WIDTH = SESSION_BAR_WIDTH
BAR_SPACING = SESSION_BAR_SPACING
AXIS_BORDER_COLOR = "#222222"
AXIS_BORDER_LINEWIDTH = 0.45
LATENCY_COMPLETENESS_FIGSIZE = (3.45, 3.65)
LATENCY_COMPLETENESS_HEIGHT_RATIOS = [1.0, 1.0]
LATENCY_COMPLETENESS_HSPACE = 0.30
WORKLOAD_BAR_EDGE_LINEWIDTH = BAR_EDGE_LINEWIDTH * 0.5
WORKLOAD_BRACKET_LINEWIDTH = 0.9 * 0.5
WORKLOAD_GRID_LINEWIDTH = 0.7 * 0.5
WORKLOAD_SEPARATOR_LINEWIDTH = 2.5 * 0.5 * 0.7
WORKLOAD_SEPARATOR_DASHES = (3.1, 2.3)
WORKLOAD_HATCHES = {protocol: HATCHES[protocol] * 2 for protocol in PROTOCOLS}
WORKLOAD_HATCH_LINEWIDTH = HATCH_LINEWIDTH * 0.5
WORKLOAD_ERROR_BAR_CAPSIZE = ERROR_BAR_CAPSIZE * 0.4
WORKLOAD_ERROR_BAR_KW = {
    **ERROR_BAR_KW,
    "elinewidth": ERROR_BAR_KW["elinewidth"] * 0.5,
    "capthick": ERROR_BAR_KW["capthick"] * 0.5,
}
FONT_SIZES = {
    "axis_label": 8,
    "tick": 7,
    "legend": 7,
    "annotation": 6,
}
SAVEFIG_KW = {"bbox_inches": "tight", "pad_inches": 0.02, "dpi": SESSION_FIG_DPI}

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


def protocol_positions(
    x: np.ndarray,
    protocol_index: int,
    *,
    bar_width: float = BAR_WIDTH,
    bar_spacing: float = BAR_SPACING,
) -> np.ndarray:
    return x + (protocol_index - 1) * (bar_width + bar_spacing)


def ci95(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def nice_tick_max(max_value: float) -> float:
    if max_value <= 0:
        return 1.0
    target = max_value * 1.08
    exponent = math.floor(math.log10(target))
    base = 10 ** exponent
    for multiplier in (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        tick_max = multiplier * base
        if tick_max >= target:
            return tick_max
    return 10 * base


def cat_512k_log_candidates(scenario: str) -> list[Path]:
    return [
        W4_RESULTS / "512KB" / scenario / "w4_line_log.csv",
        W4_RESULTS / "512KiB" / scenario / "w4_line_log.csv",
        W4_RESULTS / scenario / "w4_line_log.csv",
    ]


def first_existing_cat_512k_log(scenario: str) -> Path | None:
    for path in cat_512k_log_candidates(scenario):
        if path.exists():
            return path
    return None


def cat_512k_selected_log_candidates(scenario: str) -> list[Path]:
    return [CAT_512K_SELECTED_SOURCE_LOGS[scenario]]


def first_existing_cat_512k_selected_log(scenario: str) -> Path | None:
    for path in cat_512k_selected_log_candidates(scenario):
        if path.exists():
            return path
    return None


def cat_100k_log_candidates(scenario: str) -> list[Path]:
    return [
        W4_RESULTS / "100KB" / scenario / "w4_line_log.csv",
        W4_RESULTS / "100KiB" / scenario / "w4_line_log.csv",
    ]


def first_existing_cat_100k_log(scenario: str) -> Path | None:
    for path in cat_100k_log_candidates(scenario):
        if path.exists():
            return path
    return None


def collect_cat_latency_completeness(
    *,
    key: str,
    workload_name: str,
    command: str,
    title: str,
    output: str,
    first_existing_log,
    log_candidates,
    fixed_tick_max: float | None = None,
) -> tuple[dict, dict] | None:
    latency_values = {
        scenario: {protocol: (math.nan, 0.0) for protocol in PROTOCOLS}
        for scenario in SCENARIOS
    }
    recv_stats = {
        key: {
            scenario: {
                protocol: {"mean": math.nan, "ci95": 0.0}
                for protocol in PROTOCOLS
            }
            for scenario in SCENARIOS
        }
    }
    found = False
    largest_latency = 0.0

    for scenario in SCENARIOS:
        path = first_existing_log(scenario)
        if path is None:
            print(
                "[warn] missing any of: "
                + ", ".join(str(candidate) for candidate in log_candidates(scenario))
            )
            continue

        latencies = {protocol: [] for protocol in PROTOCOLS}
        recv_pct = {protocol: [] for protocol in PROTOCOLS}
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                protocol = row.get("protocol", "")
                if protocol not in PROTOCOLS or row.get("status") != "ok":
                    continue
                workload = row.get("workload", "")
                row_command = row.get("command", "")
                if workload != workload_name and row_command != command:
                    continue
                try:
                    latency_ms = float(row.get("latency_ms", ""))
                    received_pct = float(row.get("received_pct", ""))
                except ValueError:
                    continue
                latencies[protocol].append(latency_ms)
                recv_pct[protocol].append(received_pct)

        for protocol in PROTOCOLS:
            if not latencies[protocol]:
                continue
            found = True
            latency_mean = statistics.mean(latencies[protocol])
            latency_ci = ci95(latencies[protocol])
            latency_values[scenario][protocol] = (latency_mean, latency_ci)
            largest_latency = max(largest_latency, latency_mean + latency_ci)

            recv_mean = statistics.mean(recv_pct[protocol])
            recv_stats[key][scenario][protocol] = {
                "mean": recv_mean,
                "ci95": ci95(recv_pct[protocol]),
            }

    if not found:
        return None

    tick_max = fixed_tick_max if fixed_tick_max is not None else nice_tick_max(largest_latency)
    return (
        {
            "title": title,
            "ylabel": "Latency (ms)",
            "output": output,
            "ylim": tick_max * 1.12,
            "yticks": np.linspace(0, tick_max, 5),
            "values": latency_values,
        },
        recv_stats,
    )


def collect_cat_512k_latency_completeness() -> tuple[dict, dict] | None:
    return collect_cat_latency_completeness(
        key=CAT_512K_KEY,
        workload_name="fixture small",
        command=CAT_512K_COMMAND,
        title="W2: 512 KiB cat output",
        output="latency_w2_cat_512KiB_by_scenario.pdf",
        first_existing_log=first_existing_cat_512k_log,
        log_candidates=cat_512k_log_candidates,
        fixed_tick_max=CAT_512K_LATENCY_YTICK_MAX,
    )


def collect_cat_512k_selected_latency_completeness() -> tuple[dict, dict] | None:
    return collect_cat_latency_completeness(
        key=CAT_512K_SELECTED_KEY,
        workload_name="fixture small",
        command=CAT_512K_COMMAND,
        title="W2: 512 KiB cat output (selected sources)",
        output="latency_w2_cat_512KiB_selected_sources_by_scenario.pdf",
        first_existing_log=first_existing_cat_512k_selected_log,
        log_candidates=cat_512k_selected_log_candidates,
        fixed_tick_max=CAT_512K_LATENCY_YTICK_MAX,
    )


def collect_cat_100k_latency_completeness() -> tuple[dict, dict] | None:
    return collect_cat_latency_completeness(
        key=CAT_100K_KEY,
        workload_name="fixture 100kb",
        command=CAT_100K_COMMAND,
        title="W2: 100 KiB cat output",
        output="latency_w2_cat_100KiB_by_scenario.pdf",
        first_existing_log=first_existing_cat_100k_log,
        log_candidates=cat_100k_log_candidates,
        fixed_tick_max=4500.0,
    )


def _error_bar_kw() -> dict[str, object]:
    return {**WORKLOAD_ERROR_BAR_KW, "zorder": 4}


def _draw_session_style_bars(ax, positions, heights, protocol: str, *, yerr, bar_width: float = BAR_WIDTH):
    ax.bar(
        positions,
        heights,
        bar_width,
        facecolor=BAR_FACE_COLOR,
        edgecolor=HATCH_COLORS[protocol],
        hatch=WORKLOAD_HATCHES[protocol],
        linewidth=0,
        zorder=2,
    )
    return ax.bar(
        positions,
        heights,
        bar_width,
        label=PROTO_LABELS[protocol],
        facecolor="none",
        edgecolor=COLORS[protocol],
        linewidth=WORKLOAD_BAR_EDGE_LINEWIDTH,
        yerr=yerr,
        capsize=WORKLOAD_ERROR_BAR_CAPSIZE,
        error_kw=_error_bar_kw(),
        zorder=3,
    )


def format_k(value: float, _pos: int) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 1000:
        return f"{value / 1000:.0f}k"
    return f"{value:.0f}"


def latency_formatter(key: str):
    if key == "w2":
        return FuncFormatter(format_k)
    return StrMethodFormatter("{x:.0f}")


def style_axis(ax, *, hide_x_labels: bool = False) -> None:
    ax.grid(axis="y", linestyle="--", linewidth=WORKLOAD_GRID_LINEWIDTH, alpha=0.55, zorder=0)
    ax.grid(axis="x", visible=False)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(AXIS_BORDER_COLOR)
        spine.set_linewidth(AXIS_BORDER_LINEWIDTH)
    ax.tick_params(axis="both", direction="out", length=3.2, width=AXIS_BORDER_LINEWIDTH)
    if hide_x_labels:
        ax.tick_params(axis="x", labelbottom=False, length=0)


def draw_latency_bars(
    ax,
    key: str,
    *,
    bar_width: float = BAR_WIDTH,
    bar_spacing: float = BAR_SPACING,
) -> None:
    x = np.arange(len(SCENARIOS))
    for i, protocol in enumerate(PROTOCOLS):
        means = [WORKLOADS[key]["values"][scenario][protocol][0] for scenario in SCENARIOS]
        errors = [WORKLOADS[key]["values"][scenario][protocol][1] for scenario in SCENARIOS]
        positions = protocol_positions(x, i, bar_width=bar_width, bar_spacing=bar_spacing)
        _draw_session_style_bars(ax, positions, means, protocol, yerr=errors, bar_width=bar_width)


def add_cat_latency_inset(ax, *, key: str, inset_ymax: float) -> None:
    inset_bar_width = BAR_WIDTH * 0.8
    inset_bar_spacing = BAR_SPACING * 0.70
    zoom_xlim = (0.55, 1.45)
    marker_ylim = (
        -inset_ymax * 2.0,
        inset_ymax * 7.0,
    )
    zoom_ylim = (0.0, inset_ymax)

    rect_fill = Rectangle(
        (zoom_xlim[0], marker_ylim[0]),
        zoom_xlim[1] - zoom_xlim[0],
        marker_ylim[1] - marker_ylim[0],
        facecolor="#bdbdbd",
        edgecolor="none",
        alpha=0.32,
        zorder=1,
        clip_on=False,
    )
    ax.add_patch(rect_fill)
    rect_border = Rectangle(
        (zoom_xlim[0], marker_ylim[0]),
        zoom_xlim[1] - zoom_xlim[0],
        marker_ylim[1] - marker_ylim[0],
        facecolor="none",
        edgecolor="#555555",
        linewidth=0.4,
        zorder=6,
        clip_on=False,
    )
    ax.add_patch(rect_border)

    inset = ax.inset_axes([0.33, 0.56, 0.24, 0.39])
    inset.set_zorder(8)
    inset.set_facecolor("white")
    inset.patch.set_alpha(1.0)
    draw_latency_bars(
        inset,
        key,
        bar_width=inset_bar_width,
        bar_spacing=inset_bar_spacing,
    )
    inset.set_xlim(*zoom_xlim)
    inset.set_ylim(*zoom_ylim)
    inset.set_xticks([1])
    inset.set_xticklabels(["Low"], fontsize=5)
    inset.set_yticks(np.linspace(0, inset_ymax, 4))
    inset.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))
    inset.grid(axis="y", linestyle="--", linewidth=WORKLOAD_GRID_LINEWIDTH, alpha=0.55, zorder=0)
    inset.grid(axis="x", visible=False)
    for spine in inset.spines.values():
        spine.set_visible(True)
        spine.set_color(AXIS_BORDER_COLOR)
        spine.set_linewidth(AXIS_BORDER_LINEWIDTH)
    inset.tick_params(axis="both", direction="out", length=1.8, width=AXIS_BORDER_LINEWIDTH, labelsize=5, pad=1)

    connectors = [
        ((zoom_xlim[0], marker_ylim[1]), (0, 0)),
        ((zoom_xlim[1], marker_ylim[1]), (1, 0)),
    ]
    for data_xy, axes_xy in connectors:
        con = ConnectionPatch(
            xyA=data_xy,
            coordsA=ax.transData,
            xyB=axes_xy,
            coordsB=inset.transAxes,
            color="#666666",
            linewidth=0.4,
            linestyle=(0, (3.0, 3.0)),
            alpha=0.75,
            zorder=4,
            clip_on=False,
        )
        ax.add_artist(con)


def add_cat_512k_latency_inset(ax) -> None:
    add_cat_latency_inset(ax, key=CAT_512K_KEY, inset_ymax=CAT_512K_LATENCY_INSET_YMAX)


def add_cat_512k_selected_latency_inset(ax) -> None:
    add_cat_latency_inset(ax, key=CAT_512K_SELECTED_KEY, inset_ymax=CAT_512K_LATENCY_INSET_YMAX)


def add_cat_100k_latency_inset(ax) -> None:
    add_cat_latency_inset(ax, key=CAT_100K_KEY, inset_ymax=CAT_100K_LATENCY_INSET_YMAX)


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
        _draw_session_style_bars(ax, positions, means_arr[valid], protocol, yerr=yerr)


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
        color="#8c8c8c",
        linestyle=(0, WORKLOAD_SEPARATOR_DASHES),
        linewidth=WORKLOAD_SEPARATOR_LINEWIDTH,
        zorder=1,
        clip_on=False,
    )
    if show_bracket:
        ax.plot(
            [-0.42, -0.42, 0.42, 0.42],
            [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
            transform=trans,
            color="#222222",
            linewidth=WORKLOAD_BRACKET_LINEWIDTH,
            zorder=4,
            clip_on=False,
        )
        ax.plot(
            [0.58, 0.58, 3.42, 3.42],
            [bracket_tick_y, bracket_y, bracket_y, bracket_tick_y],
            transform=trans,
            color="#222222",
            linewidth=WORKLOAD_BRACKET_LINEWIDTH,
            zorder=4,
            clip_on=False,
        )
    if show_labels:
        ax.text(
            0,
            group_y,
            "Real network",
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            clip_on=False,
            linespacing=0.9,
        )
        ax.text(
            2,
            group_y,
            "Controlled network",
            transform=trans,
            ha="center",
            va="top",
            fontsize=fontsize,
            clip_on=False,
        )


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
    ax.yaxis.set_major_formatter(latency_formatter(key))
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

        bars = _draw_session_style_bars(ax, positions, means_arr[valid], protocol, yerr=yerr)
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
    recv_ax.set_ylabel("Output completeness (%)", fontsize=FONT_SIZES["axis_label"], labelpad=7)
    lat_ax.set_ylim(0, workload["ylim"])
    lat_ax.set_yticks(workload["yticks"])
    lat_ax.yaxis.set_major_formatter(latency_formatter(key))
    recv_ax.set_ylim(0, 112)
    recv_ax.set_yticks([0, 25, 50, 75, 100])
    recv_ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))

    for ax in (lat_ax, recv_ax):
        ax.set_xlim(-0.55, len(SCENARIOS) - 0.45)
        ax.set_xticks(x)
        ax.tick_params(axis="y", labelsize=text_size)
        style_axis(ax, hide_x_labels=ax is lat_ax)

    if key == CAT_512K_KEY:
        add_cat_512k_latency_inset(lat_ax)
    elif key == CAT_512K_SELECTED_KEY:
        add_cat_512k_selected_latency_inset(lat_ax)
    elif key == CAT_100K_KEY:
        add_cat_100k_latency_inset(lat_ax)

    recv_ax.set_xticklabels(SCENARIO_LABELS, fontsize=text_size)
    recv_ax.tick_params(axis="x", pad=5, labelsize=text_size)
    recv_ax.yaxis.set_label_coords(-0.14, 0.38)


def save_figure(fig, output_pdf: Path) -> None:
    fig.savefig(output_pdf, **SAVEFIG_KW)
    print(f"[saved] {output_pdf}")


def workload_legend_handles() -> list[tuple[Patch, Patch]]:
    return [
        (
            Patch(
                facecolor=BAR_FACE_COLOR,
                edgecolor=HATCH_COLORS[protocol],
                hatch=WORKLOAD_HATCHES[protocol],
                linewidth=0,
            ),
            Patch(
                facecolor="none",
                edgecolor=COLORS[protocol],
                linewidth=WORKLOAD_BAR_EDGE_LINEWIDTH * 1.3,
            ),
        )
        for protocol in PROTOCOLS
    ]


def workload_legend_labels() -> list[str]:
    return [PROTO_LABELS[protocol] for protocol in PROTOCOLS]


def plot_workload(key: str, show_legend: bool, show_title: bool = False) -> None:
    output_pdf = PAPER_FIGS / WORKLOADS[key]["output"]
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(3.45, 2.25), dpi=SESSION_FIG_DPI)
    plot_workload_panel(ax, key, compact=False, show_title=show_title)
    if show_legend:
        fig.legend(
            handles=workload_legend_handles(),
            labels=workload_legend_labels(),
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

    fig, ax = plt.subplots(figsize=(3.45, 2.25), dpi=SESSION_FIG_DPI)
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
        figsize=LATENCY_COMPLETENESS_FIGSIZE,
        dpi=SESSION_FIG_DPI,
        sharex=True,
        gridspec_kw={
            "height_ratios": LATENCY_COMPLETENESS_HEIGHT_RATIOS,
            "hspace": LATENCY_COMPLETENESS_HSPACE,
        },
    )
    plot_latency_completeness_panel(lat_ax, recv_ax, key, recv_stats)
    fig.align_ylabels([lat_ax, recv_ax])
    recv_ax.yaxis.set_label_coords(-0.14, 0.38)
    fig.subplots_adjust(left=0.20, right=0.985, bottom=0.32, top=0.98)
    save_figure(fig, output_pdf)
    plt.close(fig)


def plot_cat_512k_latency_completeness() -> None:
    collected = collect_cat_512k_latency_completeness()
    if collected is None:
        print("[warn] no W4 512 KiB cat rows found; skip latency/completeness chart")
        return

    workload, recv_stats = collected
    WORKLOADS[CAT_512K_KEY] = workload
    try:
        plot_latency_completeness_workload(CAT_512K_KEY, recv_stats)
    finally:
        WORKLOADS.pop(CAT_512K_KEY, None)


def plot_cat_512k_selected_latency_completeness() -> None:
    collected = collect_cat_512k_selected_latency_completeness()
    if collected is None:
        print("[warn] no selected-source W4 512 KiB cat rows found; skip latency/completeness chart")
        return

    workload, recv_stats = collected
    WORKLOADS[CAT_512K_SELECTED_KEY] = workload
    try:
        plot_latency_completeness_workload(CAT_512K_SELECTED_KEY, recv_stats)
    finally:
        WORKLOADS.pop(CAT_512K_SELECTED_KEY, None)


def plot_cat_100k_latency_completeness() -> None:
    collected = collect_cat_100k_latency_completeness()
    if collected is None:
        print("[warn] no W4 100 KiB cat rows found; skip latency/completeness chart")
        return

    workload, recv_stats = collected
    WORKLOADS[CAT_100K_KEY] = workload
    try:
        plot_latency_completeness_workload(CAT_100K_KEY, recv_stats)
    finally:
        WORKLOADS.pop(CAT_100K_KEY, None)


def plot_combined_workloads() -> None:
    output_pdf = PAPER_FIGS / "latency_workloads_by_scenario.pdf"
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(1, 4, figsize=(13, 5), dpi=SESSION_FIG_DPI)
    for ax, key in zip(axes, WORKLOADS):
        plot_workload_panel(ax, key, compact=True)

    fig.legend(
        handles=workload_legend_handles(),
        labels=workload_legend_labels(),
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

    fig, ax = plt.subplots(figsize=(9, 0.62), dpi=SESSION_FIG_DPI)
    ax.axis("off")
    ax.set_position([0, 0, 1, 1])
    ax.legend(
        handles=workload_legend_handles(),
        labels=workload_legend_labels(),
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
        "axes.linewidth": AXIS_BORDER_LINEWIDTH,
        "hatch.linewidth": WORKLOAD_HATCH_LINEWIDTH,
    })
    recv_stats = collect_all_recv_pct()
    plot_workload_legend()
    for key in ("w1", "w2"):
        plot_latency_completeness_workload(key, recv_stats)
    for key in ("w3_1p", "w3_5p"):
        plot_workload(key, show_legend=False, show_title=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
