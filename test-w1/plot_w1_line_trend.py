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
    from matplotlib.lines import Line2D
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: matplotlib. Install with: pip install matplotlib"
    ) from exc


PROTOCOL_ORDER = {"ssh": 0, "ssh3": 1, "mosh": 2}
SCENARIO_ORDER = {"low": 0, "medium": 1, "high": 2}
COMMAND_ORDER = {
    "ls": 0,
    "df -h": 1,
    "ps aux": 2,
    "grep -n root /etc/passwd": 3,
    "cat /proc/meminfo": 4,
    "find /usr -maxdepth 3": 5,
}
PROTOCOL_COLORS = {
    "ssh": "#1f77b4",
    "ssh3": "#2ca02c",
    "mosh": "#d62728",
}
PROTOCOL_MARKERS = {
    "ssh": "o",
    "ssh3": "s",
    "mosh": "^",
}
COMMAND_LABELS = {
    "ls": "ls",
    "df -h": "df -h",
    "ps aux": "ps aux",
    "grep -n root /etc/passwd": "grep -n root /etc/passwd",
    "cat /proc/meminfo": "cat /proc/meminfo",
    "find /usr -maxdepth 3": "find /usr -maxdepth 3",
}
SCENARIO_LABELS = {
    "low": "Low\n(RTT 20ms)",
    "medium": "Medium\n(RTT 100ms, 1.5% loss)",
    "high": "High\n(RTT 200ms, 3% loss)",
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


def load_results(
    results_dir: Path,
    csv_name: str,
    scenarios: list[str],
) -> dict[str, dict[str, dict[str, list[float]]]]:
    """Returns: {scenario: {command: {protocol: [latency_ms, ...]}}}"""
    data: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for scenario in scenarios:
        csv_path = results_dir / scenario / csv_name
        if not csv_path.is_file():
            continue

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

                data[scenario][command][protocol].append(latency_ms)

    return dict(data)


def plot_line_charts(
    data: dict[str, dict[str, dict[str, list[float]]]],
    output_path: Path,
    dpi: int,
) -> None:
    scenarios = sorted(
        [s for s in data.keys() if s in SCENARIO_ORDER],
        key=lambda s: SCENARIO_ORDER.get(s, 99),
    )
    if not scenarios:
        raise SystemExit("No valid scenarios (low/medium/high) found.")

    all_commands: set[str] = set()
    for scenario in scenarios:
        all_commands.update(data[scenario].keys())
    commands = sorted(all_commands, key=lambda c: COMMAND_ORDER.get(c, 99))

    if not commands:
        raise SystemExit("No commands found in data.")

    protocols = sorted(PROTOCOL_ORDER.keys(), key=lambda p: PROTOCOL_ORDER[p])
    x_positions = list(range(len(scenarios)))
    x_labels = [SCENARIO_LABELS.get(s, s) for s in scenarios]

    ncols = 2
    nrows = math.ceil(len(commands) / ncols)
    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(14, 4.5 * nrows),
        squeeze=False,
    )

    for cmd_idx, command in enumerate(commands):
        row = cmd_idx // ncols
        col = cmd_idx % ncols
        ax = axes[row][col]

        for protocol in protocols:
            medians = []
            p95s = []
            valid_x = []

            for i, scenario in enumerate(scenarios):
                values = data.get(scenario, {}).get(command, {}).get(protocol, [])
                if values:
                    medians.append(statistics.median(values))
                    p95s.append(percentile(values, 95.0))
                    valid_x.append(i)

            if not valid_x:
                continue

            color = PROTOCOL_COLORS.get(protocol, "#999999")
            marker = PROTOCOL_MARKERS.get(protocol, "o")

            ax.plot(
                valid_x,
                medians,
                color=color,
                marker=marker,
                linewidth=2,
                markersize=7,
                label=f"{protocol} (median)",
            )
            ax.plot(
                valid_x,
                p95s,
                color=color,
                marker=marker,
                linewidth=1.2,
                markersize=5,
                linestyle="--",
                alpha=0.6,
                label=f"{protocol} (p95)",
            )

        cmd_label = COMMAND_LABELS.get(command, command)
        ax.set_title(f"{cmd_label}", fontsize=11, fontweight="bold")
        ax.set_xticks(x_positions)
        ax.set_xticklabels(x_labels, fontsize=9)
        ax.set_ylabel("Latency (ms)")
        ax.grid(axis="y", alpha=0.3)
        ax.grid(axis="x", alpha=0.15)

    # Hide unused subplots
    for idx in range(len(commands), nrows * ncols):
        row = idx // ncols
        col = idx % ncols
        axes[row][col].set_axis_off()

    # Shared legend
    legend_elements = []
    for protocol in protocols:
        color = PROTOCOL_COLORS.get(protocol, "#999999")
        marker = PROTOCOL_MARKERS.get(protocol, "o")
        legend_elements.append(
            Line2D([0], [0], color=color, marker=marker, linewidth=2,
                   markersize=7, label=f"{protocol} (median)")
        )
        legend_elements.append(
            Line2D([0], [0], color=color, marker=marker, linewidth=1.2,
                   markersize=5, linestyle="--", alpha=0.6,
                   label=f"{protocol} (p95)")
        )

    fig.legend(
        handles=legend_elements,
        loc="upper center",
        ncol=len(protocols) * 2,
        frameon=False,
        fontsize=9,
    )
    fig.suptitle(
        "W1 Command Latency Trend Across Network Scenarios",
        y=0.995,
        fontsize=14,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)


def plot_line_per_command(
    data: dict[str, dict[str, dict[str, list[float]]]],
    out_dir: Path,
    dpi: int,
) -> list[Path]:
    """One PNG per command for detailed view."""
    scenarios = sorted(
        [s for s in data.keys() if s in SCENARIO_ORDER],
        key=lambda s: SCENARIO_ORDER.get(s, 99),
    )
    if not scenarios:
        return []

    all_commands: set[str] = set()
    for scenario in scenarios:
        all_commands.update(data[scenario].keys())
    commands = sorted(all_commands, key=lambda c: COMMAND_ORDER.get(c, 99))

    protocols = sorted(PROTOCOL_ORDER.keys(), key=lambda p: PROTOCOL_ORDER[p])
    x_positions = list(range(len(scenarios)))
    x_labels = [SCENARIO_LABELS.get(s, s) for s in scenarios]
    created: list[Path] = []

    for command in commands:
        fig, ax = plt.subplots(figsize=(10, 6))

        for protocol in protocols:
            medians = []
            p95s = []
            valid_x = []

            for i, scenario in enumerate(scenarios):
                values = data.get(scenario, {}).get(command, {}).get(protocol, [])
                if values:
                    medians.append(statistics.median(values))
                    p95s.append(percentile(values, 95.0))
                    valid_x.append(i)

            if not valid_x:
                continue

            color = PROTOCOL_COLORS.get(protocol, "#999999")
            marker = PROTOCOL_MARKERS.get(protocol, "o")

            ax.plot(
                valid_x,
                medians,
                color=color,
                marker=marker,
                linewidth=2.5,
                markersize=9,
                label=f"{protocol} median",
            )
            ax.plot(
                valid_x,
                p95s,
                color=color,
                marker=marker,
                linewidth=1.5,
                markersize=6,
                linestyle="--",
                alpha=0.6,
                label=f"{protocol} p95",
            )

            # Annotate values
            for xi, med, p95 in zip(valid_x, medians, p95s):
                ax.annotate(
                    f"{med:.0f}",
                    (xi, med),
                    textcoords="offset points",
                    xytext=(0, 8),
                    ha="center",
                    fontsize=8,
                    color=color,
                )

        cmd_label = COMMAND_LABELS.get(command, command)
        ax.set_title(f"W1 Latency Trend: {cmd_label}", fontsize=12, fontweight="bold")
        ax.set_xticks(x_positions)
        ax.set_xticklabels(x_labels, fontsize=10)
        ax.set_xlabel("Network Scenario")
        ax.set_ylabel("Latency (ms)")
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(axis="y", alpha=0.3)
        ax.grid(axis="x", alpha=0.15)

        fig.tight_layout()
        # Safe filename from command
        safe_name = command.replace(" ", "_").replace("/", "_").replace("-", "_")
        path = out_dir / f"w1_trend_{safe_name}.png"
        fig.savefig(path, dpi=dpi)
        plt.close(fig)
        created.append(path)

    return created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot W1 latency line charts showing protocol trends across network scenarios"
    )
    script_dir = Path(__file__).resolve().parent
    parser.add_argument(
        "--results-dir",
        default=str(script_dir / "w1_results"),
        help="Root directory containing scenario folders.",
    )
    parser.add_argument(
        "--csv-name",
        default="w1_line_log.csv",
        help="CSV file name in each scenario folder.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(script_dir / "w1_results" / "_trucquan"),
        help="Output directory for generated charts.",
    )
    parser.add_argument("--dpi", type=int, default=200, help="PNG DPI.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results_dir = Path(args.results_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not results_dir.exists():
        raise SystemExit(f"Results directory not found: {results_dir}")

    scenarios = ["low", "medium", "high"]
    data = load_results(results_dir, args.csv_name, scenarios)
    if not data:
        raise SystemExit("No valid data found in scenario folders.")

    # Combined grid chart
    combined_path = out_dir / "w1_trucquan_line_trend.png"
    plot_line_charts(data, combined_path, args.dpi)
    print(f"Saved: {combined_path}")

    # Per-command individual charts
    per_cmd_paths = plot_line_per_command(data, out_dir, args.dpi)
    for p in per_cmd_paths:
        print(f"Saved: {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
