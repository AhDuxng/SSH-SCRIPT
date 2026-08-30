"""Kiểm tra bộ đếm mạng: parser phải không âm thầm trả rỗng."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from harness import telemetry

TC_OUTPUT = """qdisc tbf 1: root refcnt 2 rate 40Mbit burst 4Kb lat 400ms
 Sent 128736452 bytes 118293 pkt (dropped 0, overlimits 4412 requeues 0)
 backlog 0b 0p requeues 0
qdisc netem 10: parent 1:1 limit 1000 delay 20ms  16ms loss 3%
 Sent 128736452 bytes 118293 pkt (dropped 3571, overlimits 0 requeues 0)
 backlog 0b 0p requeues 0
"""

SNAPSHOT = f"""#TC
{TC_OUTPUT}#SNMP
Tcp: RtoAlgorithm RtoMin InSegs OutSegs RetransSegs
Tcp: 1 200 501233 498112 8841
Udp: InDatagrams NoPorts InErrors OutDatagrams
Udp: 220145 3 11 231998
#NETSTAT
TcpExt: TCPFastRetrans TCPDSACKRecv TCPSpuriousRTOs
TcpExt: 6120 2913 97
"""


class TelemetryTests(unittest.TestCase):
    # Cả hai qdisc phải được tách riêng, kể cả khi cùng số byte.
    def test_tc_separates_qdiscs(self):
        parsed = telemetry.parse_tc(TC_OUTPUT)
        self.assertEqual(parsed["tc.netem.dropped"], 3571)
        self.assertEqual(parsed["tc.tbf.dropped"], 0)
        self.assertEqual(parsed["tc.netem.packets"], 118293)

    # Bộ đếm TCP nằm ở cả /proc/net/snmp và /proc/net/netstat.
    def test_snapshot_merges_both_proc_files(self):
        parsed = telemetry.parse_snapshot(SNAPSHOT)
        self.assertEqual(parsed["tcp.RetransSegs"], 8841)
        self.assertEqual(parsed["tcp.TCPDSACKRecv"], 2913)
        self.assertEqual(parsed["udp.OutDatagrams"], 231998)

    # Snapshot rỗng không được ném lỗi, chỉ trả về rỗng.
    def test_empty_snapshot_is_safe(self):
        self.assertEqual(telemetry.parse_snapshot(""), {})

    # Bộ đếm bị reset (kernel/qdisc khởi động lại) phải bị loại, không âm.
    def test_delta_drops_counter_resets(self):
        result = telemetry.delta({"a": 100, "b": 5}, {"a": 40, "b": 9})
        self.assertNotIn("a", result)
        self.assertEqual(result["b"], 4)

    # Bộ đếm chỉ có ở lần chụp sau thì không tính được hiệu.
    def test_delta_ignores_unknown_baseline(self):
        self.assertEqual(telemetry.delta({}, {"a": 7}), {})

    # Hàng ghi ra phải mang đủ khoá định danh của trial.
    def test_rows_carry_trial_identity(self):
        trial = {
            "trial_id": "ssh3_w2-s4_r01", "protocol": "ssh3",
            "scenario": "W2-S4", "stream_count": 4,
        }
        rows = telemetry.rows(trial, "client", {"tc.netem.dropped": 12})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["side"], "client")
        self.assertEqual(rows[0]["delta"], 12)
        self.assertEqual(rows[0]["protocol"], "ssh3")
        self.assertEqual(set(rows[0]), set(telemetry.COUNTER_FIELDS))

    # tc nằm ở /usr/sbin, không có trong PATH của phiên SSH không tương tác.
    def test_snapshot_command_extends_path_for_tc(self):
        command = telemetry.snapshot_command("eth0")
        self.assertIn("/usr/sbin", command)
        self.assertIn("IFACE=eth0", command)
        self.assertIn('tc -s qdisc show dev "$IFACE"', command)

    # Bàn đo không có netem (mq/fq_codel) vẫn phải cho ra tổng của interface.
    def test_root_qdisc_without_netem(self):
        parsed = telemetry.parse_tc(
            "qdisc mq 0: root \n"
            " Sent 27462801 bytes 198108 pkt (dropped 0, overlimits 0 requeues 1119) \n"
            "qdisc fq_codel 0: parent :5 limit 10240p\n"
            " Sent 5000 bytes 40 pkt (dropped 1, overlimits 0 requeues 0) \n"
        )
        self.assertEqual(parsed["tc.root.packets"], 198108)
        self.assertEqual(parsed["tc.fq_codel.packets"], 40)
        self.assertNotIn("tc.netem.packets", parsed)

    # Khi có netem, root vẫn được ghi và trỏ tới qdisc gốc là tbf.
    def test_root_alias_matches_tbf_when_netem_present(self):
        parsed = telemetry.parse_tc(TC_OUTPUT)
        self.assertEqual(parsed["tc.root.packets"], parsed["tc.tbf.packets"])
        self.assertEqual(parsed["tc.netem.dropped"], 3571)

    # Không cấu hình interface thì lệnh phải tự dò theo tuyến tới peer.
    def test_snapshot_command_autodetects_interface(self):
        command = telemetry.snapshot_command("", '"192.168.1.202"')
        self.assertIn("ip route get", command)
        self.assertIn("192.168.1.202", command)
        self.assertNotIn("IFACE=eth0", command)

    # Có cấu hình thì dùng đúng interface đó, không dò.
    def test_snapshot_command_honours_explicit_interface(self):
        command = telemetry.snapshot_command("enp1s0")
        self.assertIn("IFACE=enp1s0", command)
        self.assertNotIn("ip route get", command)

    # Dòng #IFACE không được lọt vào phần đếm.
    def test_iface_line_not_parsed_as_counter(self):
        parsed = telemetry.parse_snapshot("#IFACE eth0\n" + SNAPSHOT)
        self.assertEqual(parsed["tcp.RetransSegs"], 8841)
