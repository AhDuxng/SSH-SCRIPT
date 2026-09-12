#!/usr/bin/env python3
"""Vẽ hình W4 từ bảng tổng hợp đã xử lý.

W4 chỉ đánh giá SSH và SSH3: kịch bản tải nền cần workload chạy song song với
editor, mà một terminal session không làm được điều đó khi vẫn phải đo độ trễ
phím. Mosh được đánh giá ở W1/W2/W3 với kịch bản một workload.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import textwrap
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
    per_stream_panels,
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
        # Hình tỷ lệ vẫn phải hiện số: đây là con số công bố, và mắt không đọc
        # được chênh lệch 98,7 % với 99,4 % trên một trục 0–100 %.
        grouped_bars(axis, SCENARIOS, series, percent=percent)
        axis.set_title(editor.capitalize())
    axes[0].set_ylabel(ylabel)
    if percent:
        # Trục tỷ lệ luôn kín tới 100 %, không còn góc trống cho chú giải.
        deduplicated_legend(
            axes[0], sources=axes, ncol=3, loc="upper center",
            bbox_to_anchor=(1.03, -0.10),
        )
    else:
        deduplicated_legend(axes[0], sources=axes, ncol=3, loc="upper left")
    figure.suptitle(title, y=1.02)
    save_figure(figure, output_dir, stem)


# Tải nền: tỷ lệ hoàn thành và tỷ lệ đầy đủ output của workload chạy song song
# với editor.
#
# Giữ nguyên ba chiều mà background_summary.csv đã tách — editor, kịch bản và
# vai trò — thay vì cộng dồn chúng lại: một tải nền hỏng chỉ trong W4-MIX với
# vim là kết quả có ý nghĩa, gộp vào trung bình chung thì mất hẳn. Tỷ lệ được
# đọc thẳng từ cột trong CSV, không tính lại, để hình và bảng không bao giờ
# lệch nhau.
def background_index(rows):
    return {
        (row["editor"], row["protocol"], row["scenario"], row["stream_role"]): row
        for row in rows
    }


def plot_background_rate(rows, output_dir, column, title, ylabel, stem):
    lookup = background_index(rows)
    # Chỉ vẽ tổ hợp kịch bản × vai trò thực sự có trong ma trận: W4-CMD không
    # có tải nền output, W4-OUTPUT không có tải nền command.
    columns = sorted({(key[2], key[3]) for key in lookup})
    names = [f"{scenario}\n{role}" for scenario, role in columns]

    figure, axes = plt.subplots(
        1, len(EDITORS), figsize=WIDE, sharey=True, squeeze=False,
    )
    for axis, editor in zip(axes[0], EDITORS):
        series = [
            Series(
                protocol,
                [
                    value_or_none(
                        lookup, (editor, protocol, scenario, role), column,
                    )
                    for scenario, role in columns
                ],
            )
            for protocol in PROTOCOL_ORDER
        ]
        grouped_bars(axis, names, series, percent=True)
        axis.set_title(editor.capitalize())
        axis.tick_params(axis="x", labelsize=6)
    axes[0][0].set_ylabel(ylabel)
    # Cột chạm 100 % nên không còn chỗ trống trong khung; chú giải phải nằm
    # dưới trục thay vì đè lên dữ liệu.
    deduplicated_legend(
        axes[0][0], sources=axes[0], ncol=3, loc="upper center",
        bbox_to_anchor=(1.03, -0.14), fontsize=7,
    )
    figure.suptitle(title, y=1.02)
    save_figure(figure, output_dir, stem)


# W4 không có stream_summary.csv: streams.csv là một dòng cho mỗi trial × vai
# trò, nên phải gộp ở đây (README mô tả streams.csv là nguồn per-role).
ROLE_ORDER = ("interactive", "command", "output")
ROLE_LABEL = {
    "interactive": "Interactive\n(render phím)",
    "command": "Command\n(hoàn thành lệnh)",
    "output": "Output\n(hoàn thành luồng)",
}
# Quá ngưỡng này thì cột nhỏ nhất biến mất trên trục thẳng, phải chuyển sang log.
LOG_SPREAD_THRESHOLD = 25.0


def plot_scenario_streams(streams, output_dir, column, title, stem_template):
    """Mỗi kịch bản một hình, hiện đủ mọi stream của kịch bản đó."""
    for editor in EDITORS:
        for scenario in SCENARIOS:
            samples = {}
            for row in streams:
                if row["editor"] != editor or row["scenario"] != scenario:
                    continue
                if row[column] in ("", "nan"):
                    continue
                key = (row["protocol"], row["stream_role"], row["workload_type"])
                samples.setdefault(key, []).append(float(row[column]))
            if not samples:
                continue

            roles = sorted(
                {(k[1], k[2]) for k in samples},
                key=lambda rw: (ROLE_ORDER.index(rw[1]) if rw[1] in ROLE_ORDER
                                else len(ROLE_ORDER), rw[0]),
            )
            names = [
                f"{ROLE_LABEL.get(workload, workload)}\n{role}"
                for role, workload in roles
            ]
            series = [
                Series(protocol, [
                    statistics.median(samples[(protocol, role, workload)])
                    if (protocol, role, workload) in samples else None
                    for role, workload in roles
                ])
                for protocol in PROTOCOL_ORDER
                if any(k[0] == protocol for k in samples)
            ]

            observed = [v for item in series for v in item.values if v]
            spread = (max(observed) / min(observed)) if observed else 1.0
            log = spread > LOG_SPREAD_THRESHOLD

            figure, axis = plt.subplots(figsize=(max(6.0, 1.9 * len(roles) + 2.2), 4.0))
            grouped_bars(axis, names, series, log=log)
            axis.set_ylabel("Độ trễ (ms), trục log" if log else "Độ trễ (ms)")
            axis.set_title(f"{title} — {scenario} — {editor.capitalize()}")
            deduplicated_legend(axis, ncol=len(series), loc="upper left")
            note = (
                "Interactive đo theo remote_terminal_render (gõ phím → render); "
                "Command/Output đo theo client_send_to_client_completion "
                "(gửi lệnh → quan sát hết output). Hai thang đo khác nhau, "
                "so chiều cao giữa các nhóm không có ý nghĩa — chỉ so SSH với "
                "SSH3 trong cùng một nhóm."
                + (f" Trục log vì các stream chênh nhau {spread:.0f} lần."
                   if log else "")
            )
            # Không bọc dòng thì bbox_inches="tight" nới hình rộng gấp đôi.
            width = figure.get_size_inches()[0]
            figure.text(
                0.5, -0.09,
                textwrap.fill(note, width=max(60, int(width * 15))),
                ha="center", va="top", fontsize=6.5,
            )
            save_figure(
                figure, output_dir,
                stem_template.format(editor=editor, scenario=scenario.lower()),
            )


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
    streams = load(args.result_dir / "streams.csv")
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
    plot_by_editor(
        lookup, args.output_dir, "stream_completion_rate_pct",
        f"W4 — tỷ lệ stream hoàn thành{suffix}",
        "Tỷ lệ (%)", "figure_2b_stream_completion_rate", percent=True,
    )
    plot_background_rate(
        background, args.output_dir, "completion_rate_pct",
        f"W4 — tỷ lệ hoàn thành của tải nền{suffix}",
        "Tỷ lệ (%)", "figure_3_background_completion_rate",
    )
    plot_background_rate(
        background, args.output_dir, "output_completeness_pct",
        f"W4 — tỷ lệ đầy đủ output của tải nền{suffix}",
        "Tỷ lệ (%)", "figure_3b_background_output_completeness",
    )
    for metric in ("mean", "median", "p95", "p99"):
        plot_scenario_streams(
            streams, args.output_dir, f"{metric}_ms",
            f"W4 — độ trễ từng stream, {metric.upper()}{suffix}",
            "figure_4_{editor}_{scenario}_streams_" + metric,
        )
    print(f"Đã lưu hình W4 vào {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
