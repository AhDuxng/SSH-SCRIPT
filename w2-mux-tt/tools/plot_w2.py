#!/usr/bin/env python3
"""Vẽ các metric chính từ scenario_summary.csv của W2."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
from pathlib import Path


cache = str(Path(tempfile.gettempdir()) / "w2_mux_tt_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", cache)
os.environ.setdefault("XDG_CACHE_HOME", cache)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


PROTOCOLS = ("ssh", "ssh3", "mosh")
SCENARIOS = ("W2-S1", "W2-S2", "W2-S4")
LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1696D2", "ssh3": "#E69F00", "mosh": "#009E73"}


# Đọc bảng tổng hợp W2.
def load_rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# Chuyển một trường CSV sang số hoặc None.
def number(row, field):
    value = row.get(field, "") if row else ""
    return float(value) if value else None


# Vẽ một metric theo ba giao thức và ba kịch bản.
def plot_metric(rows, output_dir, field, title, ylabel, stem):
    lookup = {(row["protocol"], row["scenario"]): row for row in rows}
    x = np.arange(len(SCENARIOS))
    width = 0.24
    fig, axis = plt.subplots(figsize=(10, 5.8))
    for protocol_index, protocol in enumerate(PROTOCOLS):
        values = [
            number(lookup.get((protocol, scenario)), field)
            for scenario in SCENARIOS
        ]
        heights = [value if value is not None else 0.0 for value in values]
        bars = axis.bar(
            x + (protocol_index - 1) * width,
            heights,
            width,
            label=LABELS[protocol],
            color=COLORS[protocol],
            edgecolor="black",
            linewidth=0.5,
        )
        for bar, value in zip(bars, values):
            shown = "N/A" if value is None else f"{value:.1f}"
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                (value or 0) + max(max(heights or [1]) * 0.02, 0.1),
                shown,
                ha="center",
                va="bottom",
                fontsize=7,
            )
    axis.set_title(title)
    axis.set_ylabel(ylabel)
    axis.set_xticks(x, SCENARIOS)
    if field.endswith("_pct"):
        axis.set_ylim(0, 105)
    axis.grid(axis="y", alpha=0.25)
    axis.legend(ncol=3)
    fig.tight_layout()
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / f"{stem}.png", dpi=180, bbox_inches="tight")
    fig.savefig(output_dir / f"{stem}.pdf", bbox_inches="tight")
    plt.close(fig)


# Đọc tham số và vẽ bộ hình chuẩn W2.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.result_dir / "scenario_summary.csv")
    plots = (
        ("completion_median_ms", "W2 — độ trễ byte cuối", "Mili giây", "completion_median"),
        ("completion_p95_ms", "W2 — P95 độ trễ byte cuối", "Mili giây", "completion_p95"),
        ("first_byte_median_ms", "W2 — độ trễ byte đầu", "Mili giây", "first_byte_median"),
        ("throughput_mean_mib_s", "W2 — thông lượng trung bình", "MiB/s", "throughput_mean"),
        ("transfer_completion_rate_pct", "W2 — tỷ lệ truyền hoàn tất", "Phần trăm", "transfer_completion"),
        ("mean_content_coverage_pct", "W2 — độ bao phủ output hợp lệ", "Phần trăm", "content_coverage"),
        ("setup_median_ms", "W2 — thời gian setup", "Mili giây", "setup_median"),
    )
    for field, title, ylabel, stem in plots:
        plot_metric(rows, args.output_dir, field, title, ylabel, stem)
    print(f"Saved W2 figures to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
