#!/usr/bin/env python3
"""Vẽ hình W4 từ bảng tổng hợp đã xử lý.

W4 chỉ đánh giá SSH và SSH3: kịch bản tải nền cần workload chạy song song với
editor, mà một terminal session không làm được điều đó khi vẫn phải đo độ trễ
phím. Mosh được đánh giá ở W1/W2/W3 với kịch bản một workload.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

import matplotlib.pyplot as plt  # noqa: E402

from harness.plotting import (  # noqa: E402
    WIDE,
    Series,
    clear_figures,
    deduplicated_legend,
    grouped_bars,
    save_figure,
    use_paper_style,
    value_or_none,
)

SCENARIOS = ("W4-CMD", "W4-OUTPUT", "W4-MIX")
EDITORS = ("vim", "nano")
# W4 không đánh giá Mosh: xem constants.PROTOCOLS.
PROTOCOL_ORDER = ("ssh", "ssh3")


def load(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def plot_by_editor(lookup, output_dir, column, title, ylabel, stem, *, percent=False):
    figure, axes = plt.subplots(1, len(EDITORS), figsize=WIDE, sharey=True)
    for axis, editor in zip(axes, EDITORS):
        series = [
            Series(
                protocol,
                [
                    value_or_none(lookup, (editor, protocol, s), column)
                    for s in SCENARIOS
                ],
            )
            for protocol in PROTOCOL_ORDER
        ]
        grouped_bars(axis, SCENARIOS, series, annotate=not percent)
        if percent:
            axis.set_ylim(0, 108)
        axis.set_title(editor.capitalize())
    axes[0].set_ylabel(ylabel)
    deduplicated_legend(axes[0], ncol=3, loc="upper left")
    figure.suptitle(title, y=1.02)
    save_figure(figure, output_dir, stem)


# Tải nền: tỷ lệ hoàn thành và tính đầy đủ output, cột sau chỉ xác thực
# được với giao thức truyền luồng byte nguyên bản.
def plot_background(rows, output_dir, network):
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        key = (row["protocol"], row["scenario"], row["stream_role"])
        grouped.setdefault(key, []).append(row)

    roles = sorted({key[2] for key in grouped})
    figure, axes = plt.subplots(
        1, len(roles), figsize=WIDE, sharey=True, squeeze=False,
    )
    for axis, role in zip(axes[0], roles):
        completion = []
        completeness = []
        for protocol in PROTOCOL_ORDER:
            done = total = complete = 0
            for scenario in SCENARIOS:
                for row in grouped.get((protocol, scenario, role), []):
                    total += int(row["samples"])
                    done += int(row["completed_samples"])
                    complete += int(row["complete_outputs"])
            if not total:
                completion.append(None)
                completeness.append(None)
                continue
            completion.append(100.0 * done / total)
            completeness.append(100.0 * complete / total)
        grouped_bars(
            axis, ["Hoàn thành", "Output đủ"],
            [
                Series(protocol, [completion[index], completeness[index]])
                for index, protocol in enumerate(PROTOCOL_ORDER)
            ],
            annotate=False,
        )
        axis.set_ylim(0, 108)
        axis.set_title(role)
    axes[0][0].set_ylabel("Tỷ lệ (%)")
    deduplicated_legend(axes[0][0], ncol=3, loc="lower left", fontsize=6)
    figure.suptitle(f"W4 — tải nền ({network})", y=1.02)
    save_figure(figure, output_dir, "figure_3_background_reliability")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--network", default="unspecified")
    args = parser.parse_args()

    use_paper_style()
    clear_figures(args.output_dir)
    scenarios = load(args.result_dir / "scenario_summary.csv")
    background = load(args.result_dir / "background_summary.csv")
    lookup = {
        (row["editor"], row["protocol"], row["scenario"]): row for row in scenarios
    }
    suffix = f" ({args.network})"

    for metric in ("mean", "median", "p95", "p99"):
        plot_by_editor(
            lookup, args.output_dir, f"{metric}_ms",
            f"W4 — độ trễ tương tác dưới tải nền, {metric.upper()}{suffix}",
            "Độ trễ (ms)", f"figure_1_interactive_latency_{metric}",
        )
    plot_by_editor(
        lookup, args.output_dir, "keystroke_completion_rate_pct",
        f"W4 — tỷ lệ phím render kịp{suffix}",
        "Tỷ lệ (%)", "figure_2_interactive_reliability", percent=True,
    )
    plot_background(background, args.output_dir, args.network)
    print(f"Đã lưu hình W4 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
