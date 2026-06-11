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
SCENARIO_LABELS = ["", "Low", "Medium", "High"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#d4f4ff", "ssh3": "#fab9ba", "mosh": "#c2fac0"}
HATCHES = {"ssh": "////", "ssh3": "////", "mosh": "\\\\\\\\"}

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


def add_network_region_labels(ax) -> None:
    trans = ax.get_xaxis_transform()
    ax.plot(
        [0.5, 0.5],
        [-0.26, 1.0],
        transform=trans,
        color="#bfbfbf",
        linestyle="--",
        linewidth=1.0,
        zorder=1,
        clip_on=False,
    )
    ax.text(0, -0.08, "VPN", transform=trans, ha="center", va="top", fontsize=13, clip_on=False)
    ax.text(2, -0.14, "Controlled emulation", transform=trans, ha="center", va="top", fontsize=13, clip_on=False)


def plot_recv_pct(stats: Dict[str, Dict[str, dict]], output_pdf: Path, show_legend: bool = True) -> None:
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(SCENARIOS))
    width = 0.25
    spacing = 0.02

    plt.rcParams.update({
        "font.size": 11,
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.9,
        "hatch.linewidth": 0.8,
    })

    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=180)
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
            capsize=3,
            error_kw={"ecolor": "#222222", "elinewidth": 1.0, "capthick": 1.0},
            zorder=3,
        )
        for bar, mean, err_high in zip(bars, means, yerr[1]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(103.0, mean + err_high + 0.9),
                f"{mean:.1f}",
                ha="center",
                va="bottom",
                fontsize=7,
                color=COLORS[protocol],
            )

    ax.set_ylabel("Bytes received (%)", fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(SCENARIO_LABELS, fontsize=11)
    ax.set_ylim(0, 110)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.55, zorder=0)
    if show_legend:
        ax.legend(
            ncol=3,
            frameon=True,
            framealpha=0.9,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.18),
        )
    fig.subplots_adjust(bottom=0.22, top=0.86)
    fig.savefig(output_pdf, bbox_inches="tight")
    png_path = output_pdf.with_suffix(".png")
    fig.savefig(png_path, bbox_inches="tight")
    plt.close(fig)
    print(f"[saved] {output_pdf}")
    print(f"[saved] {png_path}")


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
    w1 = collect_recv_pct(INPUTS["w1"], warmup_filter=True)
    w2 = collect_recv_pct(INPUTS["w2"], warmup_filter=False)

    print_report("W1 recv_pct", w1)
    print_report("W2 recv_pct", w2)
    write_summary_csv(w1, w2)

    plot_recv_pct(w1, PAPER_FIGS / "recv_pct_w1_by_scenario.pdf", show_legend=True)
    plot_recv_pct(w2, PAPER_FIGS / "recv_pct_w2_by_scenario.pdf", show_legend=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
