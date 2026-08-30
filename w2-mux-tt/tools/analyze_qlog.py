#!/usr/bin/env python3
"""Rút cwnd, sự kiện mất gói và PTO từ qlog của quic-go.

Trả lời trực tiếp câu hỏi vì sao SSH3 chậm hơn dưới mất gói:
  - congestion_window có sập và ở lại mức thấp giữa các sample không?
  - mất gói được phát hiện bằng ngưỡng thứ tự, ngưỡng thời gian, hay PTO?
  - pto_count leo tới đâu?

Lưu ý ngữ nghĩa: trong W2 phía GỬI 100 KiB là server, nên cwnd cần nhìn là
qlog của ssh3-server. qlog phía client cho biết nó nhận và ACK ra sao.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from harness.results import write_rows  # noqa: E402
from harness.statistics import percentile  # noqa: E402

SUMMARY_FIELDS = (
    "source", "role", "events",
    "cwnd_min_bytes", "cwnd_median_bytes", "cwnd_max_bytes",
    "bytes_in_flight_max", "smoothed_rtt_median_ms", "pto_count_max",
    "packets_sent", "packets_received", "packets_lost",
    "lost_reordering_threshold", "lost_time_threshold", "lost_other",
)


# Đọc từng bản ghi JSON-SEQ; bỏ qua dòng hỏng thay vì làm hỏng cả tệp.
def read_events(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and "name" in record:
                yield record


# Gom số liệu của một tệp qlog.
def summarize(path: Path) -> dict:
    cwnd: list[float] = []
    in_flight: list[float] = []
    rtt: list[float] = []
    pto = 0
    sent = received = 0
    lost: dict[str, int] = {}
    events = 0
    for record in read_events(path):
        events += 1
        name = record.get("name", "")
        data = record.get("data") or {}
        if name.endswith("metrics_updated"):
            if "congestion_window" in data:
                cwnd.append(float(data["congestion_window"]))
            if "bytes_in_flight" in data:
                in_flight.append(float(data["bytes_in_flight"]))
            if "smoothed_rtt" in data:
                rtt.append(float(data["smoothed_rtt"]))
            if "pto_count" in data:
                pto = max(pto, int(data["pto_count"]))
        elif name.endswith("packet_lost"):
            trigger = str(data.get("trigger", "unknown"))
            lost[trigger] = lost.get(trigger, 0) + 1
        elif name.endswith("packet_sent"):
            sent += 1
        elif name.endswith("packet_received"):
            received += 1
    known = ("reordering_threshold", "time_threshold")
    return {
        "source": path.name,
        "role": "server" if "_server_" in path.name else "client",
        "events": events,
        "cwnd_min_bytes": int(min(cwnd)) if cwnd else "",
        "cwnd_median_bytes": int(percentile(cwnd, 0.50)) if cwnd else "",
        "cwnd_max_bytes": int(max(cwnd)) if cwnd else "",
        "bytes_in_flight_max": int(max(in_flight)) if in_flight else "",
        "smoothed_rtt_median_ms": round(percentile(rtt, 0.50), 3) if rtt else "",
        "pto_count_max": pto,
        "packets_sent": sent,
        "packets_received": received,
        "packets_lost": sum(lost.values()),
        "lost_reordering_threshold": lost.get("reordering_threshold", 0),
        "lost_time_threshold": lost.get("time_threshold", 0),
        "lost_other": sum(v for k, v in lost.items() if k not in known),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("dùng: analyze_qlog.py <thư-mục-qlog> [thư-mục-kết-quả]")
        return 2
    qlog_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else qlog_dir
    files = sorted(qlog_dir.glob("*.sqlog"))
    if not files:
        print(f"Không có tệp .sqlog trong {qlog_dir}")
        return 0
    rows = [summarize(path) for path in files]
    write_rows(out_dir / "qlog_summary.csv", SUMMARY_FIELDS, rows)

    print(f"\n[QLOG] {len(rows)} connection")
    print(
        f"{'role':<8}{'cwnd min':>10}{'cwnd p50':>10}{'cwnd max':>10}"
        f"{'srtt ms':>9}{'PTO':>6}{'mất':>7}{'reorder':>9}{'time':>7}"
    )
    for row in rows:
        print(
            f"{row['role']:<8}{str(row['cwnd_min_bytes']):>10}"
            f"{str(row['cwnd_median_bytes']):>10}{str(row['cwnd_max_bytes']):>10}"
            f"{str(row['smoothed_rtt_median_ms']):>9}{row['pto_count_max']:>6}"
            f"{row['packets_lost']:>7}{row['lost_reordering_threshold']:>9}"
            f"{row['lost_time_threshold']:>7}"
        )
    print(f"\nĐã lưu {out_dir / 'qlog_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
