#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np


SCENARIOS = ["default", "low", "medium", "high"]
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "ssh3": "#d62728", "mosh": "#2ca02c"}
HATCH_COLORS = {"ssh": "#bfefff", "ssh3": "#ffb3b3", "mosh": "#a8f0a8"}
HATCHES = {"ssh": "////", "ssh3": "////", "mosh": "\\\\\\\\"}


def safe_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def collect(input_dir: Path) -> Tuple[Dict[str, Dict[str, dict]], List[dict]]:
    stats: Dict[str, Dict[str, dict]] = {
        scenario: {
            proto: {"values": [], "ok": 0, "fail": 0, "content_bad": 0}
            for proto in PROTOCOLS
        }
        for scenario in SCENARIOS
    }
    rows_out: List[dict] = []

    for scenario in SCENARIOS:
        csv_path = input_dir / scenario / "w4_line_log.csv"
        if not csv_path.exists():
            print(f"[warn] missing {csv_path}")
            continue
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                proto = row.get("protocol", "")
                if proto not in PROTOCOLS:
                    continue
                status = row.get("status", "")
                if status == "ok":
                    value = safe_float(row.get("received_pct", ""))
                    if value is None:
                        continue
                    content_match = row.get("content_match", "")
                    if content_match and content_match.lower() != "true":
                        value = 0.0
                        stats[scenario][proto]["content_bad"] += 1
                    stats[scenario][proto]["values"].append(value)
                    stats[scenario][proto]["ok"] += 1
                elif status == "fail":
                    stats[scenario][proto]["values"].append(0.0)
                    stats[scenario][proto]["fail"] += 1

    for scenario in SCENARIOS:
        for proto in PROTOCOLS:
            entry = stats[scenario][proto]
            values = entry["values"]
            mean = statistics.mean(values) if values else math.nan
            mini = min(values) if values else math.nan
            stdev = statistics.stdev(values) if len(values) > 1 else 0.0
            ci95 = 1.96 * stdev / math.sqrt(len(values)) if len(values) > 1 else 0.0
            entry.update(mean=mean, min=mini, ci95=ci95)
            rows_out.append(
                {
                    "scenario": scenario,
                    "protocol": proto,
                    "ok_samples": entry["ok"],
                    "fail_samples": entry["fail"],
                    "content_bad_samples": entry["content_bad"],
                    "mean_received_pct": "" if math.isnan(mean) else f"{mean:.6f}",
                    "min_received_pct": "" if math.isnan(mini) else f"{mini:.6f}",
                    "ci95_received_pct": "" if math.isnan(mean) else f"{ci95:.6f}",
                }
            )
    return stats, rows_out


def write_summary(rows: List[dict], output_csv: Path) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "scenario",
                "protocol",
                "ok_samples",
                "fail_samples",
                "content_bad_samples",
                "mean_received_pct",
                "min_received_pct",
                "ci95_received_pct",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def plot(stats: Dict[str, Dict[str, dict]], output_png: Path) -> None:
    output_png.parent.mkdir(parents=True, exist_ok=True)

    x = np.arange(len(SCENARIOS))
    width = 0.25

    plt.rcParams.update({
        "font.size": 11,
        "axes.edgecolor": "#222222",
        "axes.linewidth": 0.9,
        "hatch.linewidth": 0.8,
    })

    fig, ax = plt.subplots(figsize=(7.2, 4.4), dpi=180)
    for i, proto in enumerate(PROTOCOLS):
        means = [
            stats[scenario][proto]["mean"]
            if not math.isnan(stats[scenario][proto]["mean"])
            else 0.0
            for scenario in SCENARIOS
        ]
        ci95 = [stats[scenario][proto]["ci95"] for scenario in SCENARIOS]
        offsets = x + (i - 1) * width

        ax.bar(
            offsets,
            means,
            width,
            facecolor="white",
            edgecolor=HATCH_COLORS[proto],
            linewidth=0.0,
            hatch=HATCHES[proto],
            zorder=2,
        )
        bars = ax.bar(
            offsets,
            means,
            width,
            label=PROTO_LABELS[proto],
            facecolor="none",
            edgecolor=COLORS[proto],
            linewidth=1.1,
            yerr=ci95,
            capsize=3,
            error_kw={"ecolor": "#222222", "elinewidth": 1.0, "capthick": 1.0},
            zorder=3,
        )
        for bar, mean in zip(bars, means):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                min(103.0, bar.get_height() + 0.8),
                f"{mean:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
                color=COLORS[proto],
            )

    for boundary in [0.5, 1.5, 2.5]:
        ax.axvline(boundary, color="#bfbfbf", linestyle="--", linewidth=0.8, zorder=1)

    ax.set_ylabel("Bytes received (%)", fontsize=12)
    ax.set_xlabel("Controlled emulation", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(["VPN", "Low", "Medium", "High"], fontsize=12)
    ax.set_ylim(0, 110)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.grid(axis="y", linestyle="--", linewidth=0.7, alpha=0.55, zorder=0)
    ax.legend(ncol=3, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.13))
    fig.tight_layout()
    fig.savefig(output_png, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot W4 received_pct by scenario/protocol")
    parser.add_argument("--input-dir", default="w4_results_trungnt")
    parser.add_argument("--output-png", default="w4_results_trungnt/w4_recv_pct_by_scenario.png")
    parser.add_argument("--output-csv", default="w4_results_trungnt/w4_recv_pct_summary.csv")
    args = parser.parse_args()

    stats, rows = collect(Path(args.input_dir))
    write_summary(rows, Path(args.output_csv))
    plot(stats, Path(args.output_png))
    print(f"Saved summary: {args.output_csv}")
    print(f"Saved chart  : {args.output_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
