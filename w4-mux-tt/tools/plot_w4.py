#!/usr/bin/env python3
"""Plot W4 interactive latency and background reliability."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path

cache = str(Path(tempfile.gettempdir()) / "w4_mux_tt_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", cache)
os.environ.setdefault("XDG_CACHE_HOME", cache)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
EDITORS = ("vim", "nano")
SCENARIOS = ("W4-CMD", "W4-OUTPUT", "W4-MIX")
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}
HATCHES = {"ssh": "///", "ssh3": "--", "mosh": "\\\\\\"}


def load(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


def annotate(axis, bars, values, scale):
    for bar, value in zip(bars, values):
        axis.text(
            bar.get_x() + bar.get_width() / 2, (value or 0) + max(scale * .015, .08),
            "N/A" if value is None else f"{value:.1f}", ha="center", va="bottom", fontsize=8,
        )


def plot_latency(rows, output, metric, network):
    """Plot Vim and Nano side by side using the same scale, as in W3."""
    lookup = {
        (row["editor"], row["protocol"], row["scenario"]): row
        for row in rows
    }
    all_values = [
        number(lookup.get((editor, protocol, scenario)), f"{metric}_ms")
        for editor in EDITORS
        for scenario in SCENARIOS
        for protocol in PROTOCOLS
    ]
    ceiling = max((value for value in all_values if value is not None), default=1)
    fig, axes = plt.subplots(1, 2, figsize=(16, 5.8), sharey=True)
    x, width = np.arange(len(SCENARIOS)), .24
    for axis, editor in zip(axes, EDITORS):
        for index, protocol in enumerate(PROTOCOLS):
            values = [
                number(
                    lookup.get((editor, protocol, scenario)),
                    f"{metric}_ms",
                )
                for scenario in SCENARIOS
            ]
            bars = axis.bar(
                x + (index - 1) * width,
                [value or 0 for value in values],
                width,
                label=LABELS[protocol], color=COLORS[protocol], hatch=HATCHES[protocol],
                edgecolor="black", linewidth=.6,
            )
            annotate(axis, bars, values, ceiling)
        axis.set_title(editor.capitalize())
        axis.set_xticks(x, SCENARIOS)
        axis.set_ylim(0, max(1, ceiling * 1.25))
        axis.grid(axis="y", alpha=.25)
    axes[0].set_ylabel("Keystroke latency (ms)")
    axes[0].legend(ncol=3, loc="upper left")
    fig.suptitle(
        f"W4 interactive latency under background — {network} — "
        f"{metric.upper()}"
    )
    fig.tight_layout(rect=(0, 0, 1, .95))
    stem = f"figure_1_interactive_latency_{metric}"
    fig.savefig(output / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_reliability(rows, output, network):
    fields = (
        ("keystroke_completion_rate_pct", "Completed", "#4C78A8"),
        ("stall_rate_pct", "Stall > 1 s", "#F2CF5B"),
        ("timeout_rate_pct", "Timeout ≥ 2 s", "#E45756"),
    )
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharey=True)
    for axis, editor in zip(axes, EDITORS):
        ordered = [
            next((row for row in rows if row["editor"] == editor and row["scenario"] == scenario and row["protocol"] == protocol), None)
            for scenario in SCENARIOS for protocol in PROTOCOLS
        ]
        x, width = np.arange(len(ordered)), .24
        for index, (field, label, color) in enumerate(fields):
            values = [number(row, field) or 0 for row in ordered]
            axis.bar(x + (index - 1) * width, values, width, label=label, color=color, edgecolor="black", linewidth=.5)
        axis.set_title(editor.capitalize())
        axis.set_xticks(x, [f"{scenario}\n{LABELS[protocol]}" for scenario in SCENARIOS for protocol in PROTOCOLS], fontsize=8)
        axis.set_ylim(0, 108)
        axis.set_ylabel("Rate (%)")
        axis.grid(axis="y", alpha=.25)
    axes[0].legend(ncol=3, loc="upper left")
    fig.suptitle(f"W4 interactive reliability — {network}")
    fig.tight_layout()
    fig.savefig(output / "figure_2_interactive_reliability.png", dpi=180, bbox_inches="tight")
    fig.savefig(output / "figure_2_interactive_reliability.pdf", bbox_inches="tight")
    plt.close(fig)


def plot_background(rows, output, network):
    grouped = {}
    for row in rows:
        key = (row["protocol"], row["scenario"], row["stream_role"])
        grouped.setdefault(key, []).append(row)
    labels, completion, completeness, colors, hatches = [], [], [], [], []
    for scenario in SCENARIOS:
        for role in ("command_0", "output_0"):
            for protocol in PROTOCOLS:
                matches = grouped.get((protocol, scenario, role), [])
                if not matches:
                    continue
                # Vim/Nano have equal workload semantics; weighted sample aggregate.
                samples = sum(int(row["samples"]) for row in matches)
                completed = sum(int(row["completed_samples"]) for row in matches)
                complete_outputs = sum(int(row["complete_outputs"]) for row in matches)
                labels.append(f"{scenario}\n{role}\n{LABELS[protocol]}")
                completion.append(100 * completed / samples if samples else 0)
                completeness.append(100 * complete_outputs / samples if samples else 0)
                colors.append(COLORS[protocol]); hatches.append(HATCHES[protocol])
    x, width = np.arange(len(labels)), .38
    fig, axis = plt.subplots(figsize=(max(13, len(labels) * .75), 6))
    bars1 = axis.bar(x - width / 2, completion, width, label="Completion rate", color=colors, edgecolor="black")
    bars2 = axis.bar(x + width / 2, completeness, width, label="Output completeness", color=colors, edgecolor="black", alpha=.48)
    for bar, hatch in zip([*bars1, *bars2], hatches * 2):
        bar.set_hatch(hatch)
    axis.set_xticks(x, labels, fontsize=8)
    axis.set_ylim(0, 108)
    axis.set_ylabel("Rate (%)")
    axis.set_title(f"W4 background completion and output completeness — {network}")
    axis.grid(axis="y", alpha=.25)
    axis.legend()
    fig.tight_layout()
    fig.savefig(output / "figure_3_background_reliability.png", dpi=180, bbox_inches="tight")
    fig.savefig(output / "figure_3_background_reliability.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Thư mục chỉ chứa hình do plot_w4 tạo; bỏ các hình tách editor cũ.
    for pattern in ("figure_*.png", "figure_*.pdf"):
        for path in args.output_dir.glob(pattern):
            path.unlink()
    scenarios = load(args.result_dir / "scenario_summary.csv")
    backgrounds = load(args.result_dir / "background_summary.csv")
    for metric in ("mean", "median", "p95", "p99"):
        plot_latency(scenarios, args.output_dir, metric, args.network)
    plot_reliability(scenarios, args.output_dir, args.network)
    plot_background(backgrounds, args.output_dir, args.network)
    print(f"[OK] W4 figures saved to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
