#!/usr/bin/env python3
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
    from matplotlib.patches import Patch
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: matplotlib. Install with: pip install matplotlib"
    ) from exc


PROTOCOL_ORDER = {"ssh": 0, "ssh3": 1, "mosh": 2}
COMMAND_ORDER = {
    "cat /tmp/w4_paths_small.txt": 0,
    "cat /tmp/w4_paths_medium.txt": 1,
    "cat /tmp/w4_paths_large.txt": 2,
    "head -c 524288 /dev/zero | base64": 0,
    "head -c 2097152 /dev/zero | base64": 1,
    "head -c 8388608 /dev/zero | base64": 2,
}
SCENARIO_ORDER = {"default": 0, "low": 1, "medium": 2, "high": 3}
PROTOCOL_COLORS = {
    "ssh": "#1f77b4",
    "ssh3": "#2ca02c",
    "mosh": "#d62728",
}
COMMAND_LABELS = {
    "cat /tmp/w4_paths_small.txt": "small",
    "cat /tmp/w4_paths_medium.txt": "medium",
    "cat /tmp/w4_paths_large.txt": "large",
    "head -c 524288 /dev/zero | base64": "512K",
    "head -c 2097152 /dev/zero | base64": "2M",
    "head -c 8388608 /dev/zero | base64": "8M",
}


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("percentile() requires at least one value")
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_values[int(k)]
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def protocol_sort_key(protocol: str) -> tuple[int, str]:
    return (PROTOCOL_ORDER.get(protocol.lower(), 99), protocol.lower())


def command_sort_key(command: str) -> tuple[int, str]:
    return (COMMAND_ORDER.get(command, 99), command)


def scenario_sort_key(scenario: str) -> tuple[int, str]:
    return (SCENARIO_ORDER.get(scenario.lower(), 99), scenario.lower())


def combo_sort_key(combo: tuple[str, str]) -> tuple[tuple[int, str], tuple[int, str]]:
    protocol, command = combo
    return protocol_sort_key(protocol), command_sort_key(command)


def combo_label(protocol: str, command: str) -> str:
    cmd_display = COMMAND_LABELS.get(command, command)
    return f"{protocol}\n{cmd_display}"


def load_results(
    results_dir: Path,
    csv_name: str,
) -> tuple[
    dict[str, dict[tuple[str, str], list[float]]],
    dict[str, dict[tuple[str, str], list[float]]],
    dict[str, dict[tuple[str, str], list[float]]],
    list[Path],
]:
    latency_map: dict[str, dict[tuple[str, str], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    ttfb_map: dict[str, dict[tuple[str, str], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    throughput_map: dict[str, dict[tuple[str, str], list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    sources: list[Path] = []

    for csv_path in sorted(results_dir.rglob(csv_name)):
        if not csv_path.is_file():
            continue
        rel_path = csv_path.relative_to(results_dir)
        dir_scenario = rel_path.parts[0] if rel_path.parts else "unknown"
        if dir_scenario.startswith("_"):
            continue
        sources.append(csv_path)

        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                warmup = (row.get("warmup") or "").strip()
                if warmup == "1":
                    continue

                status = (row.get("status") or "").strip().lower()
                if status != "ok":
                    continue

                protocol = (row.get("protocol") or "").strip().lower()
                command = (row.get("command") or "").strip()
                latency_raw = (row.get("latency_ms") or "").strip()
                if not protocol or not command or not latency_raw:
                    continue

                try:
                    latency_ms = float(latency_raw)
                except ValueError:
                    continue
                if not math.isfinite(latency_ms):
                    continue

                combo = (protocol, command)
                latency_map[dir_scenario][combo].append(latency_ms)

                ttfb_raw = (row.get("ttfb_ms") or "").strip()
                if ttfb_raw:
                    try:
                        ttfb = float(ttfb_raw)
                        if math.isfinite(ttfb):
                            ttfb_map[dir_scenario][combo].append(ttfb)
                    except ValueError:
                        pass

                tp_raw = (row.get("throughput_kib_s") or "").strip()
                if tp_raw:
                    try:
                        tp = float(tp_raw)
                        if math.isfinite(tp) and tp > 0:
                            throughput_map[dir_scenario][combo].append(tp)
                    except ValueError:
                        pass

    return dict(latency_map), dict(ttfb_map), dict(throughput_map), sources


def build_stats(
    data_map: dict[str, dict[tuple[str, str], list[float]]]
) -> dict[str, dict[tuple[str, str], dict[str, float]]]:
    stats_map: dict[str, dict[tuple[str, str], dict[str, float]]] = {}
    for scenario, combo_map in data_map.items():
        stats_map[scenario] = {}
        for combo, values in combo_map.items():
            if not values:
                continue
            stats_map[scenario][combo] = {
                "median": statistics.median(values),
                "p95": percentile(values, 95.0),
                "p99": percentile(values, 99.0),
            }
    return stats_map


def plot_boxplot(
    data_map: dict[str, dict[tuple[str, str], list[float]]],
    output_path: Path,
    dpi: int,
    show_fliers: bool,
    title: str,
    ylabel: str,
) -> None:
    scenarios = sorted(data_map.keys(), key=scenario_sort_key)
    if not scenarios:
        raise SystemExit("No scenarios to plot.")

    fig, axes = plt.subplots(
        nrows=len(scenarios),
        ncols=1,
        figsize=(16, 5.0 * len(scenarios)),
        squeeze=False,
    )
    legend_items: list[Patch] = []

    for index, scenario in enumerate(scenarios):
        ax = axes[index][0]
        combo_map = data_map[scenario]
        combos = sorted(combo_map.keys(), key=combo_sort_key)
        if not combos:
            ax.set_axis_off()
            continue

        data = [combo_map[combo] for combo in combos]
        labels = [combo_label(protocol, command) for protocol, command in combos]

        box = ax.boxplot(
            data,
            tick_labels=labels,
            patch_artist=True,
            showfliers=show_fliers,
            medianprops={"color": "black", "linewidth": 1.3},
            whiskerprops={"color": "#666666", "linewidth": 1.0},
            capprops={"color": "#666666", "linewidth": 1.0},
        )

        for patch, combo in zip(box["boxes"], combos):
            protocol = combo[0]
            color = PROTOCOL_COLORS.get(protocol, "#999999")
            patch.set_facecolor(color)
            patch.set_alpha(0.55)
            patch.set_edgecolor("#333333")

        if not legend_items:
            seen = set()
            for protocol, _ in combos:
                if protocol in seen:
                    continue
                seen.add(protocol)
                legend_items.append(
                    Patch(
                        facecolor=PROTOCOL_COLORS.get(protocol, "#999999"),
                        edgecolor="#333333",
                        alpha=0.55,
                        label=protocol,
                    )
                )

        sample_count = sum(len(values) for values in data)
        ax.set_title(f"Box plot | scenario={scenario} | samples={sample_count}")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", labelsize=9)

    if legend_items:
        fig.legend(
            handles=legend_items,
            loc="upper center",
            ncol=max(1, len(legend_items)),
            frameon=False,
        )
    fig.suptitle(title, y=0.995, fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)


def plot_bar_metrics_for_scenario(
    scenario: str,
    stats_map: dict[tuple[str, str], dict[str, float]],
    output_path: Path,
    dpi: int,
    ylabel: str,
    suptitle_prefix: str,
) -> None:
    combos = sorted(stats_map.keys(), key=combo_sort_key)
    if not combos:
        return

    metrics = [("median", "Median"), ("p95", "P95"), ("p99", "P99")]
    labels = [combo_label(protocol, command) for protocol, command in combos]
    colors = [PROTOCOL_COLORS.get(protocol, "#999999") for protocol, _ in combos]

    fig, axes = plt.subplots(
        nrows=3,
        ncols=1,
        figsize=(16, 12),
        sharex=True,
        squeeze=False,
    )

    x_positions = list(range(len(combos)))
    for idx, (metric_key, metric_title) in enumerate(metrics):
        ax = axes[idx][0]
        y_values = [stats_map[combo][metric_key] for combo in combos]
        bars = ax.bar(x_positions, y_values, color=colors, alpha=0.85)
        for bar, value in zip(bars, y_values):
            ax.text(
                bar.get_x() + bar.get_width() / 2.0,
                bar.get_height(),
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
        ax.set_ylabel(ylabel)
        ax.set_title(f"{metric_title} by protocol/command")
        ax.grid(axis="y", alpha=0.25)

    axes[-1][0].set_xticks(x_positions)
    axes[-1][0].set_xticklabels(labels, fontsize=9)
    axes[-1][0].set_xlabel("Protocol / Command")

    legend_items = [
        Patch(facecolor=PROTOCOL_COLORS[p], edgecolor="#333333", alpha=0.85, label=p)
        for p in sorted(PROTOCOL_COLORS.keys(), key=protocol_sort_key)
    ]
    fig.legend(
        handles=legend_items,
        loc="upper center",
        ncol=len(legend_items),
        frameon=False,
    )
    fig.suptitle(
        f"{suptitle_prefix} | scenario={scenario}", y=0.995, fontsize=14
    )
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot W4 large-output latency/throughput charts from w4_results/*/w4_line_log.csv"
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--results-dir",
        default=str(script_dir / "w4_results"),
        help="Root directory containing scenario folders (default/low/medium/high).",
    )
    parser.add_argument(
        "--csv-name",
        default="w4_line_log.csv",
        help="CSV file name to search under --results-dir.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(script_dir / "w4_results" / "_trucquan"),
        help="Output directory for generated charts.",
    )
    parser.add_argument("--dpi", type=int, default=200, help="PNG DPI.")
    parser.add_argument(
        "--show-fliers",
        action="store_true",
        help="Show outlier points on box plots (disabled by default).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not results_dir.exists():
        raise SystemExit(f"Results directory not found: {results_dir}")

    latency_map, ttfb_map, throughput_map, sources = load_results(
        results_dir, args.csv_name
    )
    if not sources:
        raise SystemExit(
            f"No CSV files named '{args.csv_name}' found under: {results_dir}"
        )
    if not latency_map:
        raise SystemExit(
            "No valid successful samples found (status=ok, warmup=0, numeric latency_ms)."
        )

    # Latency box plot
    boxplot_path = out_dir / "w4_trucquan_latency_boxplot.png"
    plot_boxplot(
        data_map=latency_map,
        output_path=boxplot_path,
        dpi=args.dpi,
        show_fliers=args.show_fliers,
        title="W4 Large Output - Latency Box Plot",
        ylabel="Latency (ms)",
    )
    print(f"Saved: {boxplot_path}")

    # Throughput box plot
    if throughput_map:
        tp_boxplot_path = out_dir / "w4_trucquan_throughput_boxplot.png"
        plot_boxplot(
            data_map=throughput_map,
            output_path=tp_boxplot_path,
            dpi=args.dpi,
            show_fliers=args.show_fliers,
            title="W4 Large Output - Throughput Box Plot",
            ylabel="Throughput (KiB/s)",
        )
        print(f"Saved: {tp_boxplot_path}")

    # TTFB box plot
    if ttfb_map:
        ttfb_boxplot_path = out_dir / "w4_trucquan_ttfb_boxplot.png"
        plot_boxplot(
            data_map=ttfb_map,
            output_path=ttfb_boxplot_path,
            dpi=args.dpi,
            show_fliers=args.show_fliers,
            title="W4 Large Output - Time to First Byte Box Plot",
            ylabel="TTFB (ms)",
        )
        print(f"Saved: {ttfb_boxplot_path}")

    # Per-scenario bar charts for latency
    latency_stats = build_stats(latency_map)
    for scenario in sorted(latency_stats.keys(), key=scenario_sort_key):
        scenario_stats = latency_stats[scenario]
        if not scenario_stats:
            continue
        bar_path = out_dir / f"w4_trucquan_{scenario}_latency_bar.png"
        plot_bar_metrics_for_scenario(
            scenario=scenario,
            stats_map=scenario_stats,
            output_path=bar_path,
            dpi=args.dpi,
            ylabel="ms",
            suptitle_prefix="W4 Latency Metrics",
        )
        print(f"Saved: {bar_path}")

    # Per-scenario bar charts for throughput
    if throughput_map:
        tp_stats = build_stats(throughput_map)
        for scenario in sorted(tp_stats.keys(), key=scenario_sort_key):
            scenario_stats = tp_stats[scenario]
            if not scenario_stats:
                continue
            bar_path = out_dir / f"w4_trucquan_{scenario}_throughput_bar.png"
            plot_bar_metrics_for_scenario(
                scenario=scenario,
                stats_map=scenario_stats,
                output_path=bar_path,
                dpi=args.dpi,
                ylabel="KiB/s",
                suptitle_prefix="W4 Throughput Metrics",
            )
            print(f"Saved: {bar_path}")

    for source in sources:
        print(f"Loaded source: {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
