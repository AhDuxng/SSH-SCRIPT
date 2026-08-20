"""Ghi định kỳ TCP_INFO do Linux công bố qua lệnh ss."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path


_NUMBER_FIELDS = {
    "rto": "rto_ms",
    "ato": "ato_ms",
    "mss": "mss_bytes",
    "pmtu": "pmtu_bytes",
    "rcvmss": "rcvmss_bytes",
    "advmss": "advmss_bytes",
    "cwnd": "cwnd_packets",
    "ssthresh": "ssthresh_packets",
    "bytes_sent": "bytes_sent",
    "bytes_acked": "bytes_acked",
    "bytes_received": "bytes_received",
    "segs_out": "segs_out",
    "segs_in": "segs_in",
    "data_segs_out": "data_segs_out",
    "data_segs_in": "data_segs_in",
    "delivered": "delivered",
    "lost": "lost_packets",
    "unacked": "unacked_packets",
    "reord_seen": "reord_seen",
    "rcv_space": "rcv_space_bytes",
    "rcv_ssthresh": "rcv_ssthresh_bytes",
    "notsent": "notsent_bytes",
}


# Chuyển tốc độ do ss in thành bit/s.
def _rate_bps(raw: str) -> float | None:
    match = re.fullmatch(r"([0-9.]+)([KMG]?bps)", raw)
    if not match:
        return None
    multiplier = {"bps": 1, "Kbps": 1e3, "Mbps": 1e6, "Gbps": 1e9}
    return float(match.group(1)) * multiplier[match.group(2)]


# Phân tích dòng TCP_INFO của ss thành các trường ổn định và giữ cả bản thô.
def parse_tcp_info(detail: str) -> dict:
    result: dict[str, object] = {"raw_tcp_info": detail.strip()}
    tokens = detail.split()
    if tokens and ":" not in tokens[0]:
        result["cc_algorithm"] = tokens[0]
    pairs = dict(re.findall(r"([A-Za-z_]+):([^\s]+)", detail))
    for source, target in _NUMBER_FIELDS.items():
        raw = pairs.get(source)
        if raw is None:
            continue
        try:
            result[target] = float(raw) if "." in raw else int(raw)
        except ValueError:
            pass
    if "rtt" in pairs:
        values = pairs["rtt"].split("/", 1)
        try:
            result["rtt_ms"] = float(values[0])
            if len(values) == 2:
                result["rttvar_ms"] = float(values[1])
        except ValueError:
            pass
    if "minrtt" in pairs:
        try:
            result["min_rtt_ms"] = float(pairs["minrtt"])
        except ValueError:
            pass
    if "retrans" in pairs:
        values = pairs["retrans"].split("/", 1)
        try:
            result["retrans_current"] = int(values[0])
            if len(values) == 2:
                result["retrans_total"] = int(values[1])
        except ValueError:
            pass
    for source, target in (
        ("send", "send_rate_bps"),
        ("pacing_rate", "pacing_rate_bps"),
        ("delivery_rate", "delivery_rate_bps"),
    ):
        match = re.search(
            rf"\b{source}(?::|\s+)([0-9.]+[KMG]?bps)\b", detail
        )
        value = _rate_bps(match.group(1) if match else "")
        if value is not None:
            result[target] = value
    if "cwnd_packets" in result and "mss_bytes" in result:
        result["cwnd_bytes"] = (
            int(result["cwnd_packets"]) * int(result["mss_bytes"])
        )
    if "unacked_packets" in result and "mss_bytes" in result:
        result["bytes_in_flight"] = (
            int(result["unacked_packets"]) * int(result["mss_bytes"])
        )
    return result


# Trích đúng block socket thuộc PID ControlMaster khỏi output của ss.
def _matching_socket(output: str, pid: int) -> tuple[str, str] | None:
    lines = output.splitlines()
    marker = f"pid={pid},"
    for index, line in enumerate(lines):
        if marker not in line:
            continue
        detail = lines[index + 1].strip() if index + 1 < len(lines) else ""
        return line.strip(), detail
    return None


class TCPInfoSampler:
    """Lấy mẫu TCP congestion state của đúng một ControlMaster."""

    # Chuẩn bị sampler nhưng chưa tạo thread.
    def __init__(self, pid: int, path: Path, interval_seconds: float):
        self.pid = pid
        self.path = path
        self.interval_seconds = max(0.02, interval_seconds)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"tcp-info-{pid}", daemon=True
        )

    # Bắt đầu thu thập nền.
    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.thread.start()

    # Dừng và chờ ghi xong file JSONL.
    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=max(2.0, self.interval_seconds * 4))

    # Ghi một sự kiện JSON trên một dòng.
    def _write(self, handle, event: dict) -> None:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        handle.flush()

    # Gọi ss theo chu kỳ và lưu cả trường đã parse lẫn dữ liệu gốc.
    def _run(self) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            self._write(handle, {
                "event": "collector_start",
                "time_ns": time.time_ns(),
                "transport": "tcp",
                "source": "linux_ss_tcp_info",
                "endpoint": "client",
                "pid": self.pid,
                "interval_ms": self.interval_seconds * 1000.0,
            })
            while not self.stop_event.is_set():
                try:
                    checked = subprocess.run(
                        ["ss", "-H", "-t", "-i", "-n", "-p"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                    )
                except FileNotFoundError:
                    self._write(handle, {
                        "event": "collector_error",
                        "time_ns": time.time_ns(),
                        "message": "ss binary not found",
                    })
                    break
                matched = _matching_socket(checked.stdout, self.pid)
                if checked.returncode != 0:
                    self._write(handle, {
                        "event": "collector_error",
                        "time_ns": time.time_ns(),
                        "message": checked.stderr.strip(),
                    })
                elif matched is None:
                    self._write(handle, {
                        "event": "socket_not_found",
                        "time_ns": time.time_ns(),
                        "pid": self.pid,
                    })
                else:
                    socket_line, detail = matched
                    self._write(handle, {
                        "event": "metrics",
                        "time_ns": time.time_ns(),
                        "pid": self.pid,
                        "socket": socket_line,
                        **parse_tcp_info(detail),
                    })
                self.stop_event.wait(self.interval_seconds)
            self._write(handle, {
                "event": "collector_stop",
                "time_ns": time.time_ns(),
                "pid": self.pid,
            })
