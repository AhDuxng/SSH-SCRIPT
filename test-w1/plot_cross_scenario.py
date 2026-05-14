#!/usr/bin/env python3
"""Cross-scenario visualization for W1 results.

Reads w1_line_log.csv and w1_session_setup.csv from every scenario subfolder
under w1_results/ and produces comparison charts under
w1_results/_cross_scenario/.

Usage:
  python plot_cross_scenario.py
  python plot_cross_scenario.py --results-dir w1_results --scenarios default low medium high
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DEFAULT_SCENARIOS = ["default", "low", "medium", "high"]
PROTOCOL_ORDER = ["ssh", "ssh3", "mosh"]
PROTOCOL_COLOR = {"ssh": "#1f77b4", "ssh3": "#2ca02c", "mosh": "#d62728"}
SCENARIO_LABEL = {
    "default": "default\n(VPN ~56ms)",
    "low": "low\n(RTT 20ms)",
    "medium": "medium\n(RTT 100ms,\n1.5% loss)",
    "high": "high\n(RTT 200ms,\n3% loss)",
}


def percentile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    return float(np.percentile(values, p))


def load_line_log(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_setup_log(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def collect_latencies(rows: List[dict]) -> Dict[Tuple[str, str], List[float]]:
    """Group latency_ms by (protocol, command); skip warmup + failures."""
    buckets: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for r in rows:
        if (r.get("status") or "").lower() != "ok":
            continue
        if (r.get("warmup") or "").strip() == "1":
            continue
        try:
            lat = float(r.get("latency_ms") or "")
        except ValueError:
            continue
        buckets[(r["protocol"], r["command"])].append(lat)
    return buckets


def collect_setups(rows: List[dict]) -> Dict[str, List[float]]:
    """Group session_setup_ms by protocol (flattened across commands)."""
    buckets: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        try:
            buckets[r["protocol"]].append(float(r["session_setup_ms"]))
        except (ValueError, KeyError):
            continue
    return buckets


def sorted_commands(buckets: Dict[Tuple[str, str], List[float]]) -> List[str]:
    seen = []
    for _, cmd in buckets.keys():
        if cmd not in seen:
            seen.append(cmd)
    return seen


def plot_bar_per_command(
    data: Dict[str, Dict[Tuple[str, str], List[float]]],
    commands: List[str],
    scenarios: List[str],
    output_dir: Path,
) -> List[Path]:
    """One PNG per command: grouped bars (x=scenario, hue=protocol, y=mean)."""
    created: List[Path] = []
    n_scen = len(scenarios)
    n_proto = len(PROTOCOL_ORDER)
    bar_w = 0.8 / n_proto

    for cmd in commands:
        fig, ax = plt.subplots(figsize=(10, 6))
        x = np.arange(n_scen)

        for pi, proto in enumerate(PROTOCOL_ORDER):
            means = []
            errs = []
            for scen in scenarios:
                vals = data.get(scen, {}).get((proto, cmd), [])
                if vals:
                    m = statistics.mean(vals)
                    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
                    ci = 1.96 * s / (len(vals) ** 0.5) if len(vals) > 1 else 0.0
                    means.append(m)
                    errs.append(ci)
                else:
                    means.append(0.0)
                    errs.append(0.0)
            positions = x + (pi - (n_proto - 1) / 2) * bar_w
            ax.bar(
                positions, means, bar_w, yerr=errs, capsize=3,
                label=proto, color=PROTOCOL_COLOR[proto], alpha=0.85,
            )
            for px, m in zip(positions, means):
                if m > 0:
                    ax.text(px, m, f"{m:.0f}", ha="center", va="bottom", fontsize=7)

        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABEL.get(s, s) for s in scenarios], fontsize=9)
        ax.set_ylabel("Mean latency (ms) ± 95% CI")
        ax.set_title(f"W1 Command Completion Latency — {cmd!r}")
        ax.grid(True, axis="y", alpha=0.3)
        ax.legend(title="Protocol", loc="upper left")
        fig.tight_layout()
        safe = _safe(cmd)
        p = output_dir / f"w1_cross_bar_{safe}.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        created.append(p)
    return created


def plot_box_per_command(
    data: Dict[str, Dict[Tuple[str, str], List[float]]],
    commands: List[str],
    scenarios: List[str],
    output_dir: Path,
) -> List[Path]:
    """Box plot showing distribution (median/IQR/whiskers/outliers)."""
    created: List[Path] = []
    n_proto = len(PROTOCOL_ORDER)
    n_scen = len(scenarios)
    group_w = 0.8
    bar_w = group_w / n_proto

    for cmd in commands:
        fig, ax = plt.subplots(figsize=(11, 6))
        x = np.arange(n_scen)

        for pi, proto in enumerate(PROTOCOL_ORDER):
            box_data = []
            positions = []
            for si, scen in enumerate(scenarios):
                vals = data.get(scen, {}).get((proto, cmd), [])
                if vals:
                    box_data.append(vals)
                    positions.append(si + (pi - (n_proto - 1) / 2) * bar_w)
            if not box_data:
                continue
            bp = ax.boxplot(
                box_data, positions=positions, widths=bar_w * 0.9,
                patch_artist=True, showfliers=True,
                flierprops=dict(marker=".", markersize=3, alpha=0.4),
                medianprops=dict(color="black", linewidth=1.2),
            )
            for patch in bp["boxes"]:
                patch.set_facecolor(PROTOCOL_COLOR[proto])
                patch.set_alpha(0.7)

        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABEL.get(s, s) for s in scenarios], fontsize=9)
        ax.set_ylabel("Latency (ms)")
        ax.set_title(f"W1 Latency Distribution — {cmd!r}")
        ax.grid(True, axis="y", alpha=0.3)
        handles = [
            plt.Rectangle((0, 0), 1, 1, facecolor=PROTOCOL_COLOR[p], alpha=0.7)
            for p in PROTOCOL_ORDER
        ]
        ax.legend(handles, PROTOCOL_ORDER, title="Protocol", loc="upper left")
        fig.tight_layout()
        safe = _safe(cmd)
        p = output_dir / f"w1_cross_box_{safe}.png"
        fig.savefig(p, dpi=180)
        plt.close(fig)
        created.append(p)
    return created


def plot_percentile_trend(
    data: Dict[str, Dict[Tuple[str, str], List[float]]],
    commands: List[str],
    scenarios: List[str],
    output_dir: Path,
) -> Path:
    """2x2 grid (one per command): mean + p95 lines vs scenario, hue=protocol."""
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), sharex=True)
    axes = axes.flatten()
    x = np.arange(len(scenarios))

    for i, cmd in enumerate(commands):
        if i >= len(axes):
            break
        ax = axes[i]
        for proto in PROTOCOL_ORDER:
            means = []
            p95s = []
            for scen in scenarios:
                vals = data.get(scen, {}).get((proto, cmd), [])
                if vals:
                    means.append(statistics.mean(vals))
                    p95s.append(percentile(vals, 95))
                else:
                    means.append(float("nan"))
                    p95s.append(float("nan"))
            color = PROTOCOL_COLOR[proto]
            ax.plot(x, means, "-o", color=color, linewidth=2, markersize=6,
                    label=f"{proto} mean")
            ax.plot(x, p95s, "--s", color=color, linewidth=1.2, markersize=5,
                    alpha=0.7, label=f"{proto} p95")
        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABEL.get(s, s) for s in scenarios], fontsize=8)
        ax.set_title(f"{cmd}")
        ax.set_ylabel("Latency (ms)")
        ax.grid(True, alpha=0.3)
        if i == 0:
            ax.legend(fontsize=7, ncol=3, loc="upper left")

    # Hide unused axes
    for j in range(len(commands), len(axes)):
        axes[j].axis("off")

    fig.suptitle("W1 Command Completion Latency — mean (solid) vs p95 (dashed)",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p = output_dir / "w1_cross_percentile_trend.png"
    fig.savefig(p, dpi=180)
    plt.close(fig)
    return p


def plot_session_setup(
    setups: Dict[str, Dict[str, List[float]]],
    scenarios: List[str],
    output_dir: Path,
) -> Path:
    """Grouped bar: x=scenario, hue=protocol, y=median session setup."""
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(scenarios))
    n_proto = len(PROTOCOL_ORDER)
    bar_w = 0.8 / n_proto

    for pi, proto in enumerate(PROTOCOL_ORDER):
        medians = []
        errs = []
        for scen in scenarios:
            vals = setups.get(scen, {}).get(proto, [])
            if vals:
                medians.append(statistics.median(vals))
                s = statistics.stdev(vals) if len(vals) > 1 else 0.0
                errs.append(s)
            else:
                medians.append(0.0)
                errs.append(0.0)
        positions = x + (pi - (n_proto - 1) / 2) * bar_w
        ax.bar(positions, medians, bar_w, yerr=errs, capsize=3,
               label=proto, color=PROTOCOL_COLOR[proto], alpha=0.85)
        for px, m in zip(positions, medians):
            if m > 0:
                ax.text(px, m, f"{m:.0f}", ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([SCENARIO_LABEL.get(s, s) for s in scenarios], fontsize=9)
    ax.set_ylabel("Session setup median (ms) ± stdev")
    ax.set_title("W1 Session Setup Latency — spawn → first shell prompt")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(title="Protocol", loc="upper left")
    fig.tight_layout()
    p = output_dir / "w1_cross_session_setup.png"
    fig.savefig(p, dpi=180)
    plt.close(fig)
    return p


def write_summary_csv(
    data: Dict[str, Dict[Tuple[str, str], List[float]]],
    commands: List[str],
    scenarios: List[str],
    output_dir: Path,
) -> Path:
    p = output_dir / "w1_cross_summary.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "scenario", "protocol", "command", "n",
            "min_ms", "mean_ms", "median_ms", "stdev_ms",
            "p95_ms", "p99_ms", "max_ms",
        ])
        for scen in scenarios:
            for proto in PROTOCOL_ORDER:
                for cmd in commands:
                    vals = data.get(scen, {}).get((proto, cmd), [])
                    if not vals:
                        continue
                    n = len(vals)
                    w.writerow([
                        scen, proto, cmd, n,
                        f"{min(vals):.3f}",
                        f"{statistics.mean(vals):.3f}",
                        f"{statistics.median(vals):.3f}",
                        f"{statistics.stdev(vals):.3f}" if n > 1 else "0.000",
                        f"{percentile(vals, 95):.3f}",
                        f"{percentile(vals, 99):.3f}",
                        f"{max(vals):.3f}",
                    ])
    return p


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_") or "x"


def main() -> int:
    ap = argparse.ArgumentParser(description="W1 cross-scenario visualization")
    ap.add_argument("--results-dir", default="w1_results",
                    help="Parent folder containing per-scenario subfolders")
    ap.add_argument("--scenarios", nargs="+", default=DEFAULT_SCENARIOS,
                    help="Scenario subfolder names (in severity order)")
    ap.add_argument("--output-dir", default="",
                    help="Output directory (default: <results-dir>/_cross_scenario)")
    args = ap.parse_args()

    results_root = Path(args.results_dir)
    out_dir = Path(args.output_dir) if args.output_dir else results_root / "_cross_scenario"
    out_dir.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Dict[Tuple[str, str], List[float]]] = {}
    setups: Dict[str, Dict[str, List[float]]] = {}
    scenarios_present: List[str] = []

    for scen in args.scenarios:
        scen_dir = results_root / scen
        if not scen_dir.is_dir():
            print(f"[skip] {scen_dir} not found")
            continue
        rows = load_line_log(scen_dir / "w1_line_log.csv")
        setup_rows = load_setup_log(scen_dir / "w1_session_setup.csv")
        if not rows:
            print(f"[skip] {scen}: empty or missing w1_line_log.csv")
            continue
        data[scen] = collect_latencies(rows)
        setups[scen] = collect_setups(setup_rows)
        scenarios_present.append(scen)
        n_samples = sum(len(v) for v in data[scen].values())
        print(f"[ok] {scen}: {n_samples} latency samples from {len(rows)} rows")

    if not scenarios_present:
        print("ERROR: no scenarios loaded")
        return 1

    all_cmds = set()
    for scen_data in data.values():
        for (_proto, cmd) in scen_data.keys():
            all_cmds.add(cmd)
    # preserve encounter order from first scenario
    ordered: List[str] = []
    first = data[scenarios_present[0]]
    for (_, cmd) in first.keys():
        if cmd not in ordered:
            ordered.append(cmd)
    for cmd in sorted(all_cmds):
        if cmd not in ordered:
            ordered.append(cmd)

    bar_paths = plot_bar_per_command(data, ordered, scenarios_present, out_dir)
    box_paths = plot_box_per_command(data, ordered, scenarios_present, out_dir)
    trend_path = plot_percentile_trend(data, ordered, scenarios_present, out_dir)
    setup_path = plot_session_setup(setups, scenarios_present, out_dir)
    csv_path = write_summary_csv(data, ordered, scenarios_present, out_dir)

    print(f"\nOutput: {out_dir}/")
    for p in bar_paths + box_paths + [trend_path, setup_path, csv_path]:
        print(f"  {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
