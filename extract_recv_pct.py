#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import os
import statistics
from pathlib import Path
from typing import Dict, List

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
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
FONT_SIZES = {
    "axis_label": 9,
    "tick": 8,
    "annotation": 7,
}
SAVEFIG_KW = {"bbox_inches": "tight", "pad_inches": 0.02, "dpi": 300}

INPUTS = {
    "w1": {
        scenario: ROOT / f"test-w1/w1_results_trungnt/{scenario}/w1_line_log.csv"
        for scenario in SCENARIOS
    },
    "w2": {
        scenario: ROOT / f"test-w4/w4_results_trungnt/{scenario}/w4_line_log.csv"
        for scenario in SCENARIOS
    },
}


def _safe_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _ci95(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def collect_recv_pct(paths: Dict[str, Path], warmup_filter: bool = False) -> Dict[str, Dict[str, dict]]:
    stats = {
        scenario: {
            protocol: {"values": [], "ok": 0, "fail": 0}
            for protocol in PROTOCOLS
        }
        for scenario in SCENARIOS
    }

    for scenario, csv_path in paths.items():
        if not csv_path.exists():
            print(f"[warn] missing {csv_path}")
            continue

        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                protocol = (row.get("protocol") or "").strip()
                if protocol not in PROTOCOLS:
                    continue
                row_scenario = (row.get("scenario") or scenario).strip().lower()
                if row_scenario and row_scenario not in {"unspecified", scenario}:
                    continue
                if warmup_filter and (row.get("warmup") or "").strip() == "1":
                    continue

                status = (row.get("status") or "").strip().lower()
                if status == "ok":
                    value = _safe_float(row.get("received_pct"))
                    if value is None:
                        continue
                    stats[scenario][protocol]["values"].append(value)
                    stats[scenario][protocol]["ok"] += 1
                elif status == "fail":
                    stats[scenario][protocol]["fail"] += 1

    for scenario in SCENARIOS:
        for protocol in PROTOCOLS:
            values = stats[scenario][protocol]["values"]
            if values:
                stats[scenario][protocol]["mean"] = statistics.mean(values)
                stats[scenario][protocol]["min"] = min(values)
                stats[scenario][protocol]["std"] = statistics.stdev(values) if len(values) > 1 else 0.0
                stats[scenario][protocol]["ci95"] = _ci95(values)
                stats[scenario][protocol]["n"] = len(values)
            else:
                stats[scenario][protocol]["mean"] = math.nan
                stats[scenario][protocol]["min"] = math.nan
                stats[scenario][protocol]["std"] = math.nan
                stats[scenario][protocol]["ci95"] = math.nan
                stats[scenario][protocol]["n"] = 0
    return stats


def collect_all_recv_pct() -> Dict[str, Dict[str, Dict[str, dict]]]:
    return {
        "w1": collect_recv_pct(INPUTS["w1"], warmup_filter=True),
        "w2": collect_recv_pct(INPUTS["w2"], warmup_filter=False),
    }


def write_summary_csv(w1: Dict[str, Dict[str, dict]], w2: Dict[str, Dict[str, dict]]) -> None:
    out_csv = ROOT / "recv_pct_summary_all.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Test",
            "Scenario",
            "Protocol",
            "N",
            "Ok",
            "Fail",
            "Recv_Pct_Mean",
            "Recv_Pct_Min",
            "Recv_Pct_Std",
            "Recv_Pct_CI95",
        ])
        for test_name, stats in [("W1", w1), ("W2", w2)]:
            for scenario in SCENARIOS:
                for protocol in PROTOCOLS:
                    entry = stats[scenario][protocol]
                    writer.writerow([
                        test_name,
                        scenario,
                        protocol,
                        entry["n"],
                        entry["ok"],
                        entry["fail"],
                        f"{entry['mean']:.6f}" if not math.isnan(entry["mean"]) else "",
                        f"{entry['min']:.6f}" if not math.isnan(entry["min"]) else "",
                        f"{entry['std']:.6f}" if not math.isnan(entry["std"]) else "",
                        f"{entry['ci95']:.6f}" if not math.isnan(entry["ci95"]) else "",
                    ])
    print(f"[saved] {out_csv}")


def save_figure(fig, output_pdf: Path) -> None:
    fig.savefig(output_pdf, **SAVEFIG_KW)
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, **SAVEFIG_KW)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


def add_network_region_labels(ax, *, fontsize: int = FONT_SIZES["tick"]) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [0, 1.0],
        transform=trans,
        color="0.65",
        linestyle=":",
        linewidth=0.6,
        alpha=0.8,
        zorder=1,
        clip_on=False,
    )
    ax.plot(
        [-0.42, -0.42, 0.42, 0.42],
        [-0.10, -0.14, -0.14, -0.10],
        transform=trans,
        color="#222222",
        linewidth=0.6,
        zorder=4,
        clip_on=False,
    )
    ax.plot(
        [0.58, 0.58, 3.42, 3.42],
        [-0.10, -0.14, -0.14, -0.10],
        transform=trans,
        color="#222222",
        linewidth=0.6,
        zorder=4,
        clip_on=False,
    )
    ax.text(0, -0.19, "Internet", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False)
    ax.text(2, -0.19, "Emulated", transform=trans, ha="center", va="top", fontsize=fontsize, clip_on=False)


def plot_recv_pct(stats: Dict[str, Dict[str, dict]], output_pdf: Path, show_legend: bool = False) -> None:
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(SCENARIOS))
    width = 0.25
    spacing = 0.02

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Nimbus Sans", "Liberation Sans", "Arial", "DejaVu Sans"],
        "font.size": FONT_SIZES["tick"],
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.7,
        "hatch.linewidth": 0.55,
    })

    fig, ax = plt.subplots(figsize=(3.45, 2.35), dpi=300)
    add_network_region_labels(ax)

    for i, protocol in enumerate(PROTOCOLS):
        means = []
        ci95 = []
        for scenario in SCENARIOS:
            mean = stats[scenario][protocol]["mean"]
            ci = stats[scenario][protocol]["ci95"]
            means.append(0.0 if math.isnan(mean) else mean)
            ci95.append(0.0 if math.isnan(ci) else ci)

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
        yerr = np.array([
            [min(err, max(0.0, mean)) for mean, err in zip(means, ci95)],
            [min(err, max(0.0, 100.0 - mean)) for mean, err in zip(means, ci95)],
        ])
        bars = ax.bar(
            positions,
            means,
            width,
            label=PROTO_LABELS[protocol],
            facecolor="none",
            edgecolor=COLORS[protocol],
            linewidth=1.2,
            yerr=yerr,
            capsize=5,
            error_kw={"ecolor": "0.3", "elinewidth": 0.8, "capthick": 0.8},
            zorder=3,
        )
        for bar, mean, err_high in zip(bars, means, yerr[1]):
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

    ax.set_ylabel("Received bytes (%)", fontsize=FONT_SIZES["axis_label"])
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=FONT_SIZES["tick"])
    ax.tick_params(axis="x", pad=5)
    ax.tick_params(axis="y", labelsize=FONT_SIZES["tick"])
    ax.set_ylim(0, 112.5)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.32, zorder=0)
    ax.grid(axis="x", visible=False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.subplots_adjust(left=0.16, right=0.98, bottom=0.25, top=0.98)
    save_figure(fig, output_pdf)
    plt.close(fig)


def print_report(name: str, stats: Dict[str, Dict[str, dict]]) -> None:
    print(f"\n={' ' + name + ' ':=^86}")
    print(f"{'Scenario':<10} | {'Protocol':<6} | {'N':>4} | {'Ok':>4} | {'Fail':>4} | {'Mean%':>8} | {'CI95':>8}")
    print("-" * 86)
    for scenario in SCENARIOS:
        for protocol in PROTOCOLS:
            entry = stats[scenario][protocol]
            mean = entry["mean"]
            ci = entry["ci95"]
            print(
                f"{scenario:<10} | {protocol:<6} | {entry['n']:>4} | {entry['ok']:>4} | {entry['fail']:>4} | "
                f"{mean:>8.2f} | {ci:>8.2f}"
            )


def main() -> int:
    stats = collect_all_recv_pct()
    w1 = stats["w1"]
    w2 = stats["w2"]

    print_report("W1 recv_pct", w1)
    print_report("W2 recv_pct", w2)
    write_summary_csv(w1, w2)

    plot_recv_pct(w1, PAPER_FIGS / "recv_pct_w1_by_scenario.pdf", show_legend=False)
    plot_recv_pct(w2, PAPER_FIGS / "recv_pct_w2_by_scenario.pdf", show_legend=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
