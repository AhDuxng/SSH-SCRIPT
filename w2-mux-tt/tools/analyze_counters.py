#!/usr/bin/env python3
"""Quy đổi bộ đếm mạng thô thành chỉ số giải thích chênh lệch SSH vs SSH3.

Ba câu hỏi mà bảng này trả lời trực tiếp:
  1. Mỗi giao thức đẩy bao nhiêu gói lên dây cho cùng một khối payload?
     (kiểm định giả thuyết kích thước gói QUIC 1252 so với TCP MSS ~1448)
  2. Tỉ lệ byte trên dây so với payload là bao nhiêu?
     (chi phí phát lại thực tế, không phải suy đoán)
  3. TCP nhận ra và hoàn tác bao nhiêu lần mất gói giả?
     (TCPDSACKRecv, TCPSpuriousRTOs — quic-go không có cơ chế tương đương)

Giới hạn: bộ đếm /proc/net là của TOÀN MÁY, không tách theo connection. Cột
TCP ở hàng ssh3 vì thế là nhiễu nền (SSH điều khiển, Tailscale), không phải
lưu lượng đo. Chỉ so cột TCP giữa các hàng ssh với nhau.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from harness.results import write_rows  # noqa: E402

SUMMARY_FIELDS = (
    "protocol", "scenario", "stream_count", "side", "trials",
    "payload_bytes", "wire_bytes", "wire_packets", "wire_bytes_per_payload_byte",
    "mean_wire_packet_bytes", "netem_dropped", "netem_drop_pct",
    "tcp_retrans_segs", "tcp_retrans_pct", "tcp_dsack_recv",
    "tcp_spurious_rto", "tcp_timeouts", "tcp_loss_probes",
    "udp_out_datagrams", "udp_in_errors",
)


# Cộng dồn hiệu số bộ đếm theo (protocol, scenario, side).
def aggregate(counter_rows):
    totals: dict[tuple, dict[str, int]] = {}
    trials: dict[tuple, set] = {}
    for row in counter_rows:
        key = (
            row["protocol"], row["scenario"],
            int(row["stream_count"]), row["side"],
        )
        totals.setdefault(key, {})
        trials.setdefault(key, set()).add(row["trial_id"])
        try:
            totals[key][row["counter"]] = (
                totals[key].get(row["counter"], 0) + int(row["delta"])
            )
        except ValueError:
            continue
    return totals, trials


# Số byte payload thật sự đã đo được trong mỗi tổ hợp.
def payload_totals(transfer_rows):
    out: dict[tuple, int] = {}
    for row in transfer_rows:
        key = (row["protocol"], row["scenario"], int(row["stream_count"]))
        try:
            out[key] = out.get(key, 0) + int(row.get("verified_bytes") or 0)
        except ValueError:
            continue
    return out


def ratio(numerator, denominator, digits=4):
    if not denominator:
        return ""
    return round(numerator / denominator, digits)


def build_summary(counter_rows, transfer_rows):
    totals, trials = aggregate(counter_rows)
    payloads = payload_totals(transfer_rows)
    summary = []
    for key in sorted(totals):
        protocol, scenario, streams, side = key
        counts = totals[key]
        # netem là nguồn chính xác nhất khi có; nếu không thì dùng qdisc gốc.
        wire_bytes = counts.get("tc.netem.bytes") or counts.get("tc.root.bytes", 0)
        wire_packets = (
            counts.get("tc.netem.packets") or counts.get("tc.root.packets", 0)
        )
        dropped = counts.get("tc.netem.dropped", 0)
        payload = payloads.get((protocol, scenario, streams), 0)
        out_segs = counts.get("tcp.OutSegs", 0)
        retrans = counts.get("tcp.RetransSegs", 0)
        summary.append({
            "protocol": protocol,
            "scenario": scenario,
            "stream_count": streams,
            "side": side,
            "trials": len(trials[key]),
            "payload_bytes": payload,
            "wire_bytes": wire_bytes,
            "wire_packets": wire_packets,
            "wire_bytes_per_payload_byte": ratio(wire_bytes, payload),
            "mean_wire_packet_bytes": ratio(wire_bytes, wire_packets, 1),
            "netem_dropped": dropped,
            "netem_drop_pct": ratio(100.0 * dropped, wire_packets + dropped, 3),
            "tcp_retrans_segs": retrans,
            "tcp_retrans_pct": ratio(100.0 * retrans, out_segs, 3),
            "tcp_dsack_recv": counts.get("tcp.TCPDSACKRecv", 0),
            "tcp_spurious_rto": counts.get("tcp.TCPSpuriousRTOs", 0),
            "tcp_timeouts": counts.get("tcp.TCPTimeouts", 0),
            "tcp_loss_probes": counts.get("tcp.TCPLossProbes", 0),
            "udp_out_datagrams": counts.get("udp.OutDatagrams", 0),
            "udp_in_errors": counts.get("udp.InErrors", 0),
        })
    return summary


def main() -> int:
    result_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/results")
    counters_path = result_dir / "network_counters.csv"
    if not counters_path.exists():
        print(f"Không có {counters_path}; bỏ qua phân tích bộ đếm mạng.")
        return 0
    counter_rows = list(csv.DictReader(counters_path.open(encoding="utf-8")))
    transfer_rows = list(
        csv.DictReader((result_dir / "transfers.csv").open(encoding="utf-8"))
    )
    summary = build_summary(counter_rows, transfer_rows)
    if not summary:
        print("Bộ đếm mạng rỗng; không tạo được bảng tổng hợp.")
        return 0
    write_rows(result_dir / "network_summary.csv", SUMMARY_FIELDS, summary)

    # tc chỉ đếm chiều RA. Trong W2 bên gửi payload là server, nên hàng
    # side=server mới trả lời được câu hỏi về số gói và chi phí phát lại;
    # hàng side=client chỉ là chiều ACK.
    print("\n[MẠNG] chiều gửi payload (side=server) — gói và chi phí phát lại:")
    header = (
        f"{'proto':<6}{'kịch bản':<9}{'side':<8}{'gói':>9}{'byte/gói':>10}"
        f"{'dây/payload':>13}{'netem drop%':>13}{'retrans%':>10}{'DSACK':>8}"
    )
    print(header)
    for row in sorted(summary, key=lambda r: (r["side"] != "server", r["protocol"], r["scenario"])):
        print(
            f"{row['protocol']:<6}{row['scenario']:<9}{row['side']:<8}"
            f"{row['wire_packets']:>9}"
            f"{str(row['mean_wire_packet_bytes']):>10}"
            f"{str(row['wire_bytes_per_payload_byte']):>13}"
            f"{str(row['netem_drop_pct']):>13}"
            f"{str(row['tcp_retrans_pct']):>10}{row['tcp_dsack_recv']:>8}"
        )
    print(
        "  (cột TCP là bộ đếm toàn máy; ở hàng ssh3 chúng là nhiễu nền)"
    )
    print(f"\nĐã lưu {result_dir / 'network_summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
