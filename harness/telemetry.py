"""Đếm số liệu tầng mạng quanh mỗi trial để giải thích chênh lệch độ trễ.

Mọi số ở đây là bộ đếm của kernel và của tc, lấy NGOÀI khoảng đo (trước và
sau mỗi trial) nên không làm nhiễu phép đo. Hiệu số giữa hai lần chụp cho
biết thực tế đã có bao nhiêu gói lên dây, bao nhiêu gói bị netem loại, và
TCP đã phải phát lại bao nhiêu lần.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

# Bộ đếm TCP/UDP cần theo dõi. Nhóm DSACK/Spurious là bằng chứng trực tiếp
# cho việc Linux TCP nhận ra và hoàn tác những lần mất gói giả do đảo thứ tự.
TCP_KEYS = (
    "OutSegs", "InSegs", "RetransSegs",
    "TCPFastRetrans", "TCPLostRetransmit", "TCPSlowStartRetrans",
    "TCPTimeouts", "TCPSpuriousRTOs", "TCPSpuriousRtxHostQueues",
    "TCPDSACKRecv", "TCPDSACKOfoRecv", "TCPLossProbes", "TCPLossProbeRecovery",
)
UDP_KEYS = (
    "OutDatagrams", "InDatagrams", "InErrors", "RcvbufErrors", "SndbufErrors",
)

COUNTER_FIELDS = (
    "trial_id", "protocol", "scenario", "stream_count", "side", "counter", "delta",
)

_TC_STAT = re.compile(
    r"Sent (\d+) bytes (\d+) pkt \(dropped (\d+), overlimits (\d+)"
)
_QDISC = re.compile(r"^qdisc (\S+) (\S+)(.*)$")


# Tách các bộ đếm dạng "Header: k1 k2" / "Header: v1 v2" của /proc/net.
def parse_proc_net(text: str, wanted: tuple[str, ...], prefix: str) -> dict[str, int]:
    out: dict[str, int] = {}
    lines = text.splitlines()
    for index in range(0, len(lines) - 1, 2):
        names = lines[index].split()
        values = lines[index + 1].split()
        if not names or names[0] != values[0]:
            continue
        for name, value in zip(names[1:], values[1:]):
            if name in wanted:
                try:
                    out[f"{prefix}.{name}"] = int(value)
                except ValueError:
                    pass
    return out


# Tách "Sent … bytes … pkt (dropped …)" cho từng qdisc trên một interface.
# qdisc gốc được ghi thêm dưới tên "root" để bàn đo không có netem (mq,
# fq_codel, pfifo_fast) vẫn cho ra tổng lưu lượng của interface.
def parse_tc(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    names: list[str] = []
    for line in text.splitlines():
        match = _QDISC.match(line.strip())
        if match:
            kind, rest = match.group(1), match.group(3)
            names = [kind] + (["root"] if " root" in f" {rest}" else [])
            continue
        stat = _TC_STAT.search(line)
        if stat and names:
            for name in names:
                out[f"tc.{name}.bytes"] = int(stat.group(1))
                out[f"tc.{name}.packets"] = int(stat.group(2))
                out[f"tc.{name}.dropped"] = int(stat.group(3))
                out[f"tc.{name}.overlimits"] = int(stat.group(4))
            names = []
    return out


# Chọn interface: ưu tiên giá trị cấu hình, nếu không thì suy từ tuyến tới
# peer. Hai đầu thường có tên khác nhau (eth0 trên Pi, enp1s0 trên PC) nên
# một hằng số chung sẽ sai ở một phía.
def iface_selector(iface: str, peer_shell: str) -> str:
    if iface:
        return f"IFACE={shlex.quote(iface)}"
    return (
        f"PEER={peer_shell}; "
        'IFACE=$(ip route get "$PEER" 2>/dev/null | '
        "sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -1); "
        '[ -n "$IFACE" ] || IFACE=$(ip route show default 2>/dev/null | '
        "sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -1)"
    )


# Lệnh shell chụp một lần toàn bộ bộ đếm; dùng chung cho local và remote.
# tc nằm ở /usr/sbin và KHÔNG có trong PATH của phiên SSH không tương tác,
# nên phải thêm tường minh.
def snapshot_command(iface: str = "", peer_shell: str = '""') -> str:
    return (
        'PATH="$PATH:/usr/sbin:/sbin"; '
        + iface_selector(iface, peer_shell) + "; "
        'echo "#IFACE $IFACE"; '
        "echo '#TC'; tc -s qdisc show dev \"$IFACE\" 2>/dev/null; "
        "echo '#SNMP'; cat /proc/net/snmp 2>/dev/null; "
        "echo '#NETSTAT'; cat /proc/net/netstat 2>/dev/null"
    )


# Chuyển output của snapshot_command thành một dict phẳng.
def parse_snapshot(text: str) -> dict[str, int]:
    sections: dict[str, list[str]] = {"#TC": [], "#SNMP": [], "#NETSTAT": []}
    current = ""
    for line in text.splitlines():
        if line.startswith("#IFACE"):
            current = ""
            continue
        if line.strip() in sections:
            current = line.strip()
            continue
        if current:
            sections[current].append(line)
    out = parse_tc("\n".join(sections["#TC"]))
    snmp = "\n".join(sections["#SNMP"])
    out.update(parse_proc_net(snmp, TCP_KEYS, "tcp"))
    out.update(parse_proc_net(snmp, UDP_KEYS, "udp"))
    out.update(parse_proc_net("\n".join(sections["#NETSTAT"]), TCP_KEYS, "tcp"))
    return out


# Chụp bộ đếm ở máy đang chạy driver.
def local_snapshot(iface: str, peer: str = "") -> dict[str, int]:
    command = snapshot_command(iface, shlex.quote(peer) if peer else '""')
    try:
        done = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_snapshot(done.stdout)


# Chụp bộ đếm ở máy đích qua một phiên SSH ngoài khoảng đo. Phía server tự
# suy interface từ địa chỉ nguồn của chính phiên SSH này.
def remote_snapshot(cfg: dict, iface: str = "") -> dict[str, int]:
    user = cfg.get("SERVER_USER", "")
    host = cfg.get("SERVER_HOST", "")
    if not user or not host:
        return {}
    command = [cfg.get("SSH_BIN", "ssh")]
    port = (cfg.get("SERVER_PORT") or "").strip()
    if port:
        command += ["-p", port]
    identity = (cfg.get("SSH_IDENTITY_FILE") or "").strip()
    if identity:
        command += ["-i", str(Path(identity).expanduser())]
    if (cfg.get("SSH_STRICT_HOST_KEY_CHECKING") or "0") != "1":
        command += ["-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null"]
    command += [
        "-o", "BatchMode=yes", f"{user}@{host}",
        snapshot_command(iface, '"${SSH_CLIENT%% *}"'),
    ]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_snapshot(done.stdout)


# Hiệu số giữa hai lần chụp; chỉ giữ bộ đếm có mặt ở cả hai và không lùi.
def delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    out = {}
    for key, end in after.items():
        start = before.get(key)
        if start is None or end < start:
            continue
        out[key] = end - start
    return out


# Biến hiệu số của một trial thành các hàng cho network_counters.csv.
def rows(trial: dict, side: str, values: dict[str, int]) -> list[dict]:
    return [
        {
            "trial_id": trial["trial_id"],
            "protocol": trial["protocol"],
            "scenario": trial["scenario"],
            "stream_count": trial["stream_count"],
            "side": side,
            "counter": key,
            "delta": value,
        }
        for key, value in sorted(values.items())
    ]
