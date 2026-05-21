#!/usr/bin/env python3
"""Cross-scenario comparison charts for W2 benchmark.

Generates publication-ready charts showing protocol differences across
network scenarios (default, low, medium, high).

Usage:
    python plot_cross_scenario.py
    python plot_cross_scenario.py --results-dir w2_results --dpi 300
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pip install matplotlib numpy"
    ) from exc


SCENARIOS = ["default", "low", "medium", "high"]
SCENARIO_LABELS = {
    "default": "Default\n(no impairment)",
    "low": "Low\n(20ms RTT)",
    "medium": "Medium\n(100ms RTT\n1.5% loss)",
    "high": "High\n(200ms RTT\n3% loss)",
}
PROTOCOLS = ["ssh", "ssh3", "mosh-adaptive"]
PROTOCOL_COLORS = {
    "ssh": "#2196F3",
    "ssh3": "#d98714",
    "mosh-adaptive": "#9C27B0",
}
PROTOCOL_LABELS = {
    "ssh": "SSH",
    "ssh3": "SSH3",
    "mosh-adaptive": "Mosh-adaptive",
}
WORKLOADS = ["top", "tail", "ping"]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_all_session_setup(results_dir: Path) -> dict[str, dict[str, list[float]]]:
    """Returns {scenario: {protocol: [setup_ms values]}}"""
    data: dict[str, dict[str, list[float]]] = {}
    for scenario in SCENARIOS:
        csv_path = results_dir / scenario / "w2_session_setup.csv"
        rows = load_csv(csv_path)
        if not rows:
            continue
        proto_values: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            proto = row.get("protocol", "").strip()
            val = row.get("session_setup_ms", "").strip()
            if proto and val:
                try:
                    proto_values[proto].append(float(val))
                except ValueError:
                    pass
        data[scenario] = dict(proto_values)
    return data


def load_all_line_log(results_dir: Path) -> dict[str, list[dict[str, str]]]:
    """Returns {scenario: [rows]}"""
    data: dict[str, list[dict[str, str]]] = {}
    for scenario in SCENARIOS:
        csv_path = results_dir / scenario / "w2_line_log.csv"
        rows = load_csv(csv_path)
        if rows:
            data[scenario] = rows
    return data


def compute_latency_stats(
    rows: list[dict[str, str]], workload_filter: str
) -> dict[str, dict[str, float]]:
    """Returns {protocol: {mean, p95, std}}"""
    proto_values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("status", "").strip().lower() != "ok":
            continue
        if row.get("workload", "").strip() != workload_filter:
            continue
        proto = row.get("protocol", "").strip()
        val = row.get("latency_ms", "").strip()
        if proto and val:
            try:
                proto_values[proto].append(float(val))
            except ValueError:
                pass

    stats: dict[str, dict[str, float]] = {}
    for proto, values in proto_values.items():
        if values:
            stats[proto] = {
                "mean": statistics.mean(values),
                "p95": percentile(values, 95.0),
                "std": statistics.stdev(values) if len(values) > 1 else 0.0,
            }
    return stats


def get_ping_time_series(
    rows: list[dict[str, str]], round_id: int = 1
) -> dict[str, tuple[list[int], list[float]]]:
    """Returns {protocol: (sample_ids, latencies)} for a specific round."""
    proto_data: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        if row.get("status", "").strip().lower() != "ok":
            continue
        if row.get("workload", "").strip() != "ping":
            continue
        try:
            rid = int(row.get("round_id", "0"))
        except ValueError:
            continue
        if rid != round_id:
            continue
        proto = row.get("protocol", "").strip()
        try:
            sid = int(row.get("sample_id", "0"))
            lat = float(row.get("latency_ms", "0"))
        except ValueError:
            continue
        proto_data[proto][sid] = lat

    result: dict[str, tuple[list[int], list[float]]] = {}
    for proto, samples in proto_data.items():
        ids = sorted(samples.keys())
        vals = [samples[i] for i in ids]
        result[proto] = (ids, vals)
    return result


def plot_session_setup(
    setup_data: dict[str, dict[str, list[float]]],
    output_path: Path,
    dpi: int,
) -> None:
    """Bar chart: session setup time grouped by scenario."""
    fig, ax = plt.subplots(figsize=(12, 6))

    available_scenarios = [s for s in SCENARIOS if s in setup_data]
    n_scenarios = len(available_scenarios)
    n_protocols = len(PROTOCOLS)
    bar_width = 0.18
    x = np.arange(n_scenarios)

    for i, proto in enumerate(PROTOCOLS):
        means = []
        stds = []
        for scenario in available_scenarios:
            values = setup_data.get(scenario, {}).get(proto, [])
            if values:
                means.append(statistics.mean(values))
                stds.append(statistics.stdev(values) if len(values) > 1 else 0)
            else:
                means.append(0)
                stds.append(0)

        offset = (i - n_protocols / 2 + 0.5) * bar_width
        bars = ax.bar(
            x + offset,
            means,
            bar_width,
            label=PROTOCOL_LABELS[proto],
            color=PROTOCOL_COLORS[proto],
            alpha=0.85,
            edgecolor="white",
            linewidth=0.5,
        )
        for bar, mean in zip(bars, means):
            if mean > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 50,
                    f"{mean:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    fontweight="bold",
                )

    ax.set_xlabel("Network Scenario", fontsize=11)
    ax.set_ylabel("Session Setup Time (ms)", fontsize=11)
    ax.set_title("W2 — Session Setup Time by Protocol and Scenario", fontsize=13, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels([SCENARIO_LABELS.get(s, s) for s in available_scenarios], fontsize=9)
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    print(f"Saved: {output_path}")


def plot_latency_bars(
    line_log_data: dict[str, list[dict[str, str]]],
    workload: str,
    output_path: Path,
    dpi: int,
) -> None:
    """Grouped bar chart: mean + P95 latency for a workload across scenarios."""
    fig, axes = plt.subplots(1, 2, figsize=(16, 6), sharey=True)

    available_scenarios = [s for s in SCENARIOS if s in line_log_data]
    n_scenarios = len(available_scenarios)
    n_protocols = len(PROTOCOLS)
    bar_width = 0.18
    x = np.arange(n_scenarios)

    for ax_idx, (metric_name, metric_key) in enumerate([("Mean", "mean"), ("P95", "p95")]):
        ax = axes[ax_idx]
        for i, proto in enumerate(PROTOCOLS):
            values_per_scenario = []
            for scenario in available_scenarios:
                rows = line_log_data.get(scenario, [])
                stats = compute_latency_stats(rows, workload)
                val = stats.get(proto, {}).get(metric_key, 0)
                values_per_scenario.append(val)

            offset = (i - n_protocols / 2 + 0.5) * bar_width
            bars = ax.bar(
                x + offset,
                values_per_scenario,
                bar_width,
                label=PROTOCOL_LABELS[proto],
                color=PROTOCOL_COLORS[proto],
                alpha=0.85,
                edgecolor="white",
                linewidth=0.5,
            )
            for bar, val in zip(bars, values_per_scenario):
                if val > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 5,
                        f"{val:.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=6.5,
                        fontweight="bold",
                    )

        ax.set_xlabel("Network Scenario", fontsize=10)
        ax.set_ylabel("Latency (ms)", fontsize=10)
        ax.set_title(f"{metric_name} Latency", fontsize=11, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABELS.get(s, s) for s in available_scenarios], fontsize=8)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, axis="y", alpha=0.3)
        ax.set_axisbelow(True)

    fig.suptitle(
        f"W2 — {workload.capitalize()} Workload: Latency by Protocol and Scenario",
        fontsize=13,
        fontweight="bold",
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    print(f"Saved: {output_path}")


def plot_ping_accumulation(
    line_log_data: dict[str, list[dict[str, str]]],
    scenario: str,
    output_path: Path,
    dpi: int,
) -> None:
    """Line plot: ping latency vs sample_id showing Mosh accumulation effect."""
    rows = line_log_data.get(scenario, [])
    if not rows:
        return

    series = get_ping_time_series(rows, round_id=1)
    if not series:
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    for proto in PROTOCOLS:
        if proto not in series:
            continue
        ids, vals = series[proto]
        ax.plot(
            ids,
            vals,
            marker="o",
            linewidth=2.5,
            markersize=7,
            label=PROTOCOL_LABELS[proto],
            color=PROTOCOL_COLORS[proto],
        )

    ax.set_xlabel("Sample ID (output line sequence)", fontsize=11)
    ax.set_ylabel("Latency (ms)", fontsize=11)
    ax.set_title(
        f"W2 — Ping Latency Accumulation Effect ({scenario} scenario, round 1)\n"
        f"SSH/SSH3: flat line | Mosh: linearly increasing due to frame coalescing",
        fontsize=11,
        fontweight="bold",
    )
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_xticks(range(1, 11))
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    print(f"Saved: {output_path}")


def plot_tail_pattern(
    line_log_data: dict[str, list[dict[str, str]]],
    scenario: str,
    output_path: Path,
    dpi: int,
) -> None:
    """Line plot: tail latency vs sample_id showing Mosh bimodal pattern."""
    rows = line_log_data.get(scenario, [])
    if not rows:
        return

    proto_data: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        if row.get("status", "").strip().lower() != "ok":
            continue
        if row.get("workload", "").strip() != "tail":
            continue
        try:
            rid = int(row.get("round_id", "0"))
        except ValueError:
            continue
        if rid != 1:
            continue
        proto = row.get("protocol", "").strip()
        try:
            sid = int(row.get("sample_id", "0"))
            lat = float(row.get("latency_ms", "0"))
        except ValueError:
            continue
        proto_data[proto][sid] = lat

    if not proto_data:
        return

    fig, ax = plt.subplots(figsize=(10, 6))

    for proto in PROTOCOLS:
        if proto not in proto_data:
            continue
        samples = proto_data[proto]
        ids = sorted(samples.keys())
        vals = [samples[i] for i in ids]
        ax.plot(
            ids,
            vals,
            marker="o",
            linewidth=2.5,
            markersize=7,
            label=PROTOCOL_LABELS[proto],
            color=PROTOCOL_COLORS[proto],
        )

    ax.set_xlabel("Sample ID (output line sequence)", fontsize=11)
    ax.set_ylabel("Latency (ms)", fontsize=11)
    ax.set_title(
        f"W2 — Tail Latency Pattern ({scenario} scenario, round 1)\n"
        f"Mosh shows bimodal pattern due to frame coalescing",
        fontsize=11,
        fontweight="bold",
    )
    ax.legend(loc="upper left", fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_xticks(range(1, 11))
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    print(f"Saved: {output_path}")


def plot_summary_heatmap(
    line_log_data: dict[str, list[dict[str, str]]],
    setup_data: dict[str, dict[str, list[float]]],
    output_path: Path,
    dpi: int,
) -> None:
    """Summary heatmap showing mean latency for all protocol × workload × scenario."""
    fig, axes = plt.subplots(1, 4, figsize=(18, 5))

    available_scenarios = [s for s in SCENARIOS if s in line_log_data]
    all_workloads = ["setup"] + WORKLOADS

    for ax_idx, scenario in enumerate(available_scenarios):
        ax = axes[ax_idx]
        matrix = []
        for proto in PROTOCOLS:
            row_vals = []
            setup_vals = setup_data.get(scenario, {}).get(proto, [])
            row_vals.append(statistics.mean(setup_vals) if setup_vals else 0)

            rows = line_log_data.get(scenario, [])
            stats_by_wl = {}
            for wl in WORKLOADS:
                stats_by_wl[wl] = compute_latency_stats(rows, wl)

            for wl in WORKLOADS:
                val = stats_by_wl[wl].get(proto, {}).get("mean", 0)
                row_vals.append(val)
            matrix.append(row_vals)

        matrix_np = np.array(matrix)
        im = ax.imshow(matrix_np, cmap="YlOrRd", aspect="auto")

        ax.set_xticks(range(len(all_workloads)))
        ax.set_xticklabels(all_workloads, fontsize=8)
        ax.set_yticks(range(len(PROTOCOLS)))
        ax.set_yticklabels([PROTOCOL_LABELS[p] for p in PROTOCOLS], fontsize=8)
        ax.set_title(SCENARIO_LABELS.get(scenario, scenario).replace("\n", " "), fontsize=9, fontweight="bold")

        for i in range(len(PROTOCOLS)):
            for j in range(len(all_workloads)):
                val = matrix_np[i, j]
                color = "white" if val > matrix_np.max() * 0.6 else "black"
                ax.text(j, i, f"{val:.0f}", ha="center", va="center", fontsize=7, color=color)

    fig.suptitle(
        "W2 — Mean Latency Heatmap (ms): Protocol × Workload × Scenario",
        fontsize=13,
        fontweight="bold",
    )
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)
    print(f"Saved: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate cross-scenario comparison charts for W2 benchmark."
    )
    parser.add_argument(
        "--results-dir",
        default="w2_results",
        help="Path to w2_results directory (default: w2_results)",
    )
    parser.add_argument("--dpi", type=int, default=200, help="PNG DPI (default: 200)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        raise SystemExit(f"Results directory not found: {results_dir}")

    output_dir = results_dir
    setup_data = load_all_session_setup(results_dir)
    line_log_data = load_all_line_log(results_dir)

    if not setup_data and not line_log_data:
        raise SystemExit("No data found in results directory.")

    # 1. Session setup bar chart
    if setup_data:
        plot_session_setup(setup_data, output_dir / "w2_cross_session_setup.png", args.dpi)

    # 2. Latency bar charts per workload (mean + P95)
    if line_log_data:
        for wl in WORKLOADS:
            plot_latency_bars(line_log_data, wl, output_dir / f"w2_cross_{wl}_latency.png", args.dpi)

    # 3. Ping accumulation line plot (use low scenario for clearest effect)
    ping_scenario = "low" if "low" in line_log_data else next(iter(line_log_data), None)
    if ping_scenario:
        plot_ping_accumulation(
            line_log_data, ping_scenario, output_dir / "w2_ping_accumulation.png", args.dpi
        )

    # 4. Tail bimodal pattern (use medium for visible effect)
    tail_scenario = "medium" if "medium" in line_log_data else next(iter(line_log_data), None)
    if tail_scenario:
        plot_tail_pattern(
            line_log_data, tail_scenario, output_dir / "w2_tail_bimodal_pattern.png", args.dpi
        )

    # 5. Summary heatmap
    if setup_data and line_log_data:
        plot_summary_heatmap(line_log_data, setup_data, output_dir / "w2_summary_heatmap.png", args.dpi)

    print("\nDone. All cross-scenario charts saved to:", output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
