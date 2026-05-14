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
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: matplotlib. Install with: pip install matplotlib"
    ) from exc


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("percentile() requires at least one value")
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sanitize_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    cleaned = cleaned.strip("_")
    return cleaned or "unknown"


def protocol_sort_key(protocol: str) -> tuple[int, str]:
    order = {"ssh": 0, "mosh": 1, "ssh3": 2}
    return (order.get(protocol.lower(), 99), protocol.lower())


def build_metric_maps(
    rows: list[dict[str, str]],
    workload_field: str,
    protocol_field: str,
    facet_fields: list[str],
    metric: str,
) -> dict[tuple[str, tuple[str, ...]], dict[str, tuple[list[int], list[float]]]]:
    grouped: dict[tuple[str, tuple[str, ...], str], dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for row in rows:
        status = row.get("status", "").strip().lower()
        if status and status != "ok":
            continue

        # Drop warmup samples from trend aggregation (present when --warmup > 0
        # was passed to the benchmark). Column is optional for backward compat.
        if row.get("warmup", "").strip() == "1":
            continue

        latency_raw = row.get("latency_ms", "").strip()
        trial_raw = row.get("round_id", "").strip()
        if not latency_raw or not trial_raw:
            continue

        try:
            latency = float(latency_raw)
            trial_id = int(trial_raw)
        except ValueError:
            continue

        workload = row.get(workload_field, "").strip() or "unknown_workload"
        protocol = row.get(protocol_field, "").strip() or "unknown_protocol"
        facet_key = tuple(row.get(field, "").strip() or "unknown" for field in facet_fields)
        key = (workload, facet_key, protocol)
        grouped[key][trial_id].append(latency)

    metrics: dict[tuple[str, tuple[str, ...]], dict[str, tuple[list[int], list[float]]]] = defaultdict(dict)
    for (workload, facet_key, protocol), trial_map in grouped.items():
        xs = sorted(trial_map.keys())
        ys: list[float] = []
        for x in xs:
            values = trial_map[x]
            if metric == "mean":
                ys.append(statistics.mean(values))
            else:
                ys.append(percentile(values, 95.0))
        if xs and ys:
            metrics[(workload, facet_key)][protocol] = (xs, ys)

    return dict(metrics)


def plot_metric(
    protocol_series: dict[str, tuple[list[int], list[float]]],
    workload: str,
    facet_fields: list[str],
    facet_key: tuple[str, ...],
    metric: str,
    output_path: Path,
    title_prefix: str,
    dpi: int,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 6))

    for protocol in sorted(protocol_series, key=protocol_sort_key):
        xs, ys = protocol_series[protocol]
        ax.plot(
            xs,
            ys,
            marker="o",
            linewidth=2.0,
            markersize=4,
            label=protocol,
        )

    facet_title = ""
    if facet_fields:
        facet_title = " | " + ", ".join(
            f"{name}={value}" for name, value in zip(facet_fields, facet_key)
        )
    ax.set_title(
        f"{title_prefix} | workload={workload}{facet_title} | {metric.upper()} by trial"
    )
    ax.set_xlabel("trial_id (round_id)")
    ax.set_ylabel("latency_ms")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=dpi)
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot trial-level latency trends (mean and p95) from line log CSV."
    )
    parser.add_argument("--output-dir", required=True, help="Benchmark output directory")
    parser.add_argument("--prefix", required=True, help="Output file prefix, e.g. w3")
    parser.add_argument(
        "--line-log",
        default="",
        help="Optional line log CSV path (default: <output-dir>/<prefix>_line_log.csv)",
    )
    parser.add_argument(
        "--group-fields",
        nargs="+",
        default=["protocol", "workload"],
        help=(
            "Fields used for chart grouping. Must include protocol + workload. "
            "Any extra fields become per-chart facets (e.g. command)."
        ),
    )
    parser.add_argument("--dpi", type=int, default=200, help="PNG DPI")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    line_log_path = (
        Path(args.line_log)
        if args.line_log
        else output_dir / f"{args.prefix}_line_log.csv"
    )
    if not line_log_path.exists():
        raise SystemExit(f"Line log CSV not found: {line_log_path}")

    if "protocol" not in args.group_fields or "workload" not in args.group_fields:
        raise SystemExit("--group-fields must include both 'protocol' and 'workload'")

    facet_fields = [
        field for field in args.group_fields if field not in ("protocol", "workload")
    ]
    rows = load_rows(line_log_path)
    mean_maps = build_metric_maps(rows, "workload", "protocol", facet_fields, "mean")
    p95_maps = build_metric_maps(rows, "workload", "protocol", facet_fields, "p95")
    if not mean_maps or not p95_maps:
        raise SystemExit(
            "No valid successful samples found in line log CSV (status=ok with latency_ms)."
        )

    created: list[Path] = []
    chart_keys = sorted(
        set(mean_maps.keys()) | set(p95_maps.keys()),
        key=lambda item: (
            item[0].lower(),
            tuple(part.lower() for part in item[1]),
        ),
    )
    for workload, facet_key in chart_keys:
        workload_token = sanitize_token(workload)
        facet_suffix = ""
        if facet_fields:
            facet_parts = [
                f"{name}-{sanitize_token(value)}"
                for name, value in zip(facet_fields, facet_key)
            ]
            facet_suffix = "_" + "_".join(facet_parts)

        mean_path = output_dir / f"{args.prefix}_{workload_token}{facet_suffix}_trend_mean.png"
        p95_path = output_dir / f"{args.prefix}_{workload_token}{facet_suffix}_trend_p95.png"

        mean_series = mean_maps.get((workload, facet_key), {})
        p95_series = p95_maps.get((workload, facet_key), {})
        if mean_series:
            plot_metric(
                mean_series,
                workload,
                facet_fields,
                facet_key,
                "mean",
                mean_path,
                args.prefix.upper(),
                args.dpi,
            )
            created.append(mean_path)
        if p95_series:
            plot_metric(
                p95_series,
                workload,
                facet_fields,
                facet_key,
                "p95",
                p95_path,
                args.prefix.upper(),
                args.dpi,
            )
            created.append(p95_path)

    for path in created:
        print(f"Saved trend chart: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
