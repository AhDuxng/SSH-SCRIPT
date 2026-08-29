#!/usr/bin/env python3
"""So sánh nhiều lần chạy W2 để cô lập nguyên nhân chênh lệch SSH3 vs SSH."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


SCENARIOS = ("W2-S1", "W2-S2", "W2-S4")


# Đọc scenario_summary.csv của một lần chạy.
def load_summary(result_dir: Path) -> dict:
    path = result_dir / "scenario_summary.csv"
    if not path.exists():
        raise FileNotFoundError(f"không có {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (row["protocol"], row["scenario"]): row
            for row in csv.DictReader(handle)
        }


# Chuyển một trường CSV sang số hoặc None.
def number(row, field):
    value = row.get(field, "") if row else ""
    try:
        return float(value) if value else None
    except ValueError:
        return None


# Chia hai giá trị và giữ None khi thiếu dữ liệu.
def ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return numerator / denominator


# Định dạng số hoặc N/A cho bảng văn bản.
def fmt(value, digits=3, suffix=""):
    return "N/A" if value is None else f"{value:.{digits}f}{suffix}"


# Tính các chỉ số so sánh SSH3/SSH của một lần chạy.
def compare_run(summary: dict, metric: str) -> list[dict]:
    rows = []
    for scenario in SCENARIOS:
        ssh = summary.get(("ssh", scenario))
        ssh3 = summary.get(("ssh3", scenario))
        if ssh is None or ssh3 is None:
            continue
        ssh_latency = number(ssh, metric)
        ssh3_latency = number(ssh3, metric)
        rows.append({
            "scenario": scenario,
            "ssh_ms": ssh_latency,
            "ssh3_ms": ssh3_latency,
            "latency_ratio": ratio(ssh3_latency, ssh_latency),
            "throughput_ratio": ratio(
                number(ssh3, "throughput_mean_mib_s"),
                number(ssh, "throughput_mean_mib_s"),
            ),
            "ssh_completion_pct": number(
                ssh, "attempted_transfer_completion_rate_pct"
            ),
            "ssh3_completion_pct": number(
                ssh3, "attempted_transfer_completion_rate_pct"
            ),
        })
    return rows


# In bảng chính và mức thay đổi so với lần chạy tham chiếu.
def report(runs: list[tuple[str, list[dict]]], metric: str) -> None:
    baseline_label, baseline_rows = runs[0]
    baseline = {row["scenario"]: row for row in baseline_rows}

    print(f"metric = {metric}\n")
    header = (
        f"{'run':<22} {'scenario':<8} {'ssh':>10} {'ssh3':>10} "
        f"{'ssh3/ssh':>9} {'thr ratio':>10} {'vs base':>9}"
    )
    print(header)
    print("-" * len(header))
    for label, rows in runs:
        for row in rows:
            reference = baseline.get(row["scenario"])
            change = None
            if label != baseline_label and reference is not None:
                change = ratio(row["latency_ratio"], reference["latency_ratio"])
            print(
                f"{label:<22} {row['scenario']:<8} "
                f"{fmt(row['ssh_ms'], 1):>10} {fmt(row['ssh3_ms'], 1):>10} "
                f"{fmt(row['latency_ratio']):>9} "
                f"{fmt(row['throughput_ratio']):>10} "
                f"{fmt(change):>9}"
            )
        print()

    print("Chú thích:")
    print("  ssh3/ssh  : tỷ số latency SSH3 trên SSH trong cùng lần chạy.")
    print("  thr ratio : tỷ số throughput trung bình SSH3 trên SSH.")
    print(
        "  vs base   : tỷ số ssh3/ssh của lần chạy này chia cho lần chạy "
        f"tham chiếu ({baseline_label});"
    )
    print(
        "              < 1.0 nghĩa là biến được gỡ bỏ đã giải thích một phần "
        "chênh lệch."
    )

    incomplete = [
        (label, row["scenario"], row["ssh_completion_pct"], row["ssh3_completion_pct"])
        for label, rows in runs
        for row in rows
        if (row["ssh_completion_pct"] or 0) < 99.0
        or (row["ssh3_completion_pct"] or 0) < 99.0
    ]
    if incomplete:
        print("\n[CHECK] latency chỉ tính trên các transfer completed; các nhóm sau")
        print("        có completion < 99% nên tỷ số có thể bị lệch do survivorship:")
        for label, scenario, ssh_pct, ssh3_pct in incomplete:
            print(
                f"  - {label} {scenario}: ssh={fmt(ssh_pct, 1, '%')} "
                f"ssh3={fmt(ssh3_pct, 1, '%')}"
            )


# Đọc tham số và in bảng so sánh các lần chạy.
def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "So sánh SSH3/SSH giữa nhiều lần chạy W2. Lần chạy đầu tiên được "
            "dùng làm tham chiếu."
        ),
    )
    parser.add_argument(
        "runs", nargs="+", metavar="LABEL=RESULT_DIR",
        help="ví dụ: baseline=pi_runs_2/medium/results mss1215=artifacts/results",
    )
    parser.add_argument(
        "--metric", default="completion_median_ms",
        help="cột latency dùng để so sánh (mặc định completion_median_ms)",
    )
    args = parser.parse_args()

    runs = []
    for item in args.runs:
        if "=" not in item:
            parser.error(f"thiếu nhãn cho {item!r}; dùng dạng LABEL=RESULT_DIR")
        label, path = item.split("=", 1)
        rows = compare_run(load_summary(Path(path)), args.metric)
        if not rows:
            print(f"[WARN] {label}: không có cặp ssh/ssh3 nào", file=sys.stderr)
        runs.append((label, rows))

    report(runs, args.metric)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
