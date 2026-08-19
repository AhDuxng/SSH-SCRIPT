#!/usr/bin/env python3
"""Vẽ W3 theo bố cục, màu và hatch nhất quán với W1."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path

cache = str(Path(tempfile.gettempdir()) / "w3_mux_tt_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", cache)
os.environ.setdefault("XDG_CACHE_HOME", cache)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
EDITORS = ("vim", "nano")
SCENARIOS = ("W3-I1", "W3-I2", "W3-I4")
STREAMS = {"W3-I1": 1, "W3-I2": 2, "W3-I4": 4}
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}


def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


def label_bars(axis, bars, values, ceiling):
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2,
            (value or 0) + max(ceiling * 0.015, 0.08),
            "N/A" if value is None else f"{value:.1f}",
            ha="center", va="bottom", fontsize=7,
        )


def plot_per_stream(rows, _scenario_rows, output_dir, editor, field, metric, network):
    stream_lookup = {
        (row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows if row["editor"] == editor
    }
    values_all = [number(row, field) for row in rows if row["editor"] == editor]
    ceiling = max((value for value in values_all if value is not None), default=1.0)
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.8), sharey=True)
    width = 0.24
    for scenario_index, scenario in enumerate(SCENARIOS):
        axis = axes[scenario_index]
        count = STREAMS[scenario]
        x = np.arange(count, dtype=float)
        for protocol_index, protocol in enumerate(PROTOCOLS):
            values = [
                number(
                    stream_lookup.get((protocol, scenario, f"interactive_{index}")),
                    field,
                )
                for index in range(count)
            ]
            bars = axis.bar(
                x + (protocol_index - 1) * width,
                [value or 0 for value in values], width,
                label=LABELS[protocol], color=COLORS[protocol],
                edgecolor="black", linewidth=0.6, hatch=HATCHES[protocol],
            )
            label_bars(axis, bars, values, ceiling)
        axis.set_title(
            f"{scenario}\nSSH/SSH3: {count} stream{'s' if count > 1 else ''}; "
            f"Mosh: {count} pane{'s' if count > 1 else ''} / 1 terminal",
            fontsize=11,
        )
        axis.set_xticks(
            x,
            [f"Role {index}\nSSH/SSH3 stream\nMosh pane" for index in range(count)],
            fontsize=7,
        )
        axis.set_xlim(-0.55, count - 0.45)
        axis.set_ylim(0, max(1, ceiling * 1.24))
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("Latency (ms)")
    axes[0].legend(ncol=3, loc="upper left")
    fig.suptitle(
        f"W3 {editor.capitalize()} transport-aware keystroke latency — "
        f"{network} — {metric.upper()}"
    )
    fig.tight_layout()
    stem = f"figure_1_{editor}_per_stream_latency_{metric}"
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_scenario_metric(rows, output_dir, editor, field, title, ylabel, stem, network, percent=False):
    lookup = {
        (row["protocol"], row["scenario"]): row
        for row in rows if row["editor"] == editor
    }
    x = np.arange(3)
    width = 0.24
    all_values = [
        number(lookup.get((protocol, scenario)), field)
        for scenario in SCENARIOS for protocol in PROTOCOLS
    ]
    ceiling = max((value for value in all_values if value is not None), default=1.0)
    fig, axis = plt.subplots(figsize=(10, 5.8))
    for protocol_index, protocol in enumerate(PROTOCOLS):
        values = [number(lookup.get((protocol, scenario)), field) for scenario in SCENARIOS]
        bars = axis.bar(
            x + (protocol_index - 1) * width, [value or 0 for value in values], width,
            label=LABELS[protocol], color=COLORS[protocol], edgecolor="black",
            linewidth=0.6, hatch=HATCHES[protocol],
        )
        label_bars(axis, bars, values, 100 if percent else ceiling)
    axis.set_title(f"W3 {editor.capitalize()} {title} — {network}")
    axis.set_ylabel(ylabel)
    axis.set_xticks(x, [
        f"{scenario}\n{STREAMS[scenario]} interactive role"
        f"{'s' if STREAMS[scenario] > 1 else ''}"
        for scenario in SCENARIOS
    ])
    axis.set_ylim(0, 108 if percent else max(1, ceiling * 1.24))
    axis.grid(axis="y", alpha=0.25)
    axis.legend(ncol=3)
    fig.tight_layout()
    fig.savefig(output_dir / f"{stem}_{editor}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}_{editor}.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_reliability(rows, output_dir, network):
    fields = (
        ("keystroke_completion_rate_pct", "Completed", "#4C78A8"),
        ("stall_rate_pct", "Stall > 1 s", "#F2CF5B"),
        ("timeout_rate_pct", "Timeout ≥ 2 s", "#E45756"),
    )
    fig, axes = plt.subplots(2, 1, figsize=(15, 9), sharey=True)
    for axis, editor in zip(axes, EDITORS):
        ordered = [
            next((row for row in rows if row["editor"] == editor and row["scenario"] == scenario and row["protocol"] == protocol), None)
            for scenario in SCENARIOS for protocol in PROTOCOLS
        ]
        x = np.arange(len(ordered))
        width = 0.24
        for field_index, (field, label, color) in enumerate(fields):
            values = [number(row, field) or 0 for row in ordered]
            bars = axis.bar(x + (field_index - 1) * width, values, width, label=label, color=color, edgecolor="black", linewidth=0.5)
            for bar, value in zip(bars, values):
                if value > 0:
                    axis.text(
                        bar.get_x() + bar.get_width() / 2, value + 0.5,
                        f"{value:.1f}", ha="center", va="bottom", fontsize=7,
                    )
        axis.set_title(editor.capitalize())
        axis.set_xticks(x, [f"{scenario}\n{LABELS[protocol]}" for scenario in SCENARIOS for protocol in PROTOCOLS], fontsize=8)
        axis.set_ylim(0, 110)
        axis.grid(axis="y", alpha=0.25)
        axis.set_ylabel("Rate (%)")
    axes[0].legend(ncol=1, loc="upper left", bbox_to_anchor=(1.005, 1.0))
    fig.suptitle(f"W3 keystroke reliability — {network}")
    fig.tight_layout()
    fig.savefig(output_dir / "figure_3_reliability.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / "figure_3_reliability.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenarios = load(args.result_dir / "scenario_summary.csv")
    streams = load(args.result_dir / "stream_summary.csv")
    for editor in EDITORS:
        for metric in ("mean", "median", "p95", "p99"):
            plot_per_stream(
                streams, scenarios, args.output_dir, editor,
                f"{metric}_ms", metric, args.network,
            )
            plot_scenario_metric(
                scenarios, args.output_dir, editor, f"{metric}_ms",
                f"keystroke latency — {metric.upper()}", "Latency (ms)",
                f"figure_2_scenario_latency_{metric}", args.network,
            )
        plot_scenario_metric(
            scenarios, args.output_dir, editor, "setup_median_ms",
            "connection + editors READY — MEDIAN", "Setup time (ms)",
            "figure_4_setup_median", args.network,
        )
    plot_reliability(scenarios, args.output_dir, args.network)
    print(f"[OK] figures saved to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
