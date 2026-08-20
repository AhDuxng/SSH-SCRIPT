#!/usr/bin/env python3
"""Ghi TCP_INFO của socket sshd phía server cho một SSH_CONNECTION."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


STOP = False


# Đánh dấu vòng lấy mẫu dừng ở lần lặp kế tiếp.
def stop_sampler(_signum, _frame) -> None:
    global STOP
    STOP = True


# Chuyển trường số nếu ss cung cấp được.
def number(raw: str):
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError:
        return None


# Chuyển tốc độ K/M/Gbps của ss sang bit/s.
def rate_bps(raw: str) -> float | None:
    match = re.fullmatch(r"([0-9.]+)([KMG]?bps)", raw)
    if not match:
        return None
    return float(match.group(1)) * {
        "bps": 1, "Kbps": 1e3, "Mbps": 1e6, "Gbps": 1e9,
    }[match.group(2)]


# Phân tích các trường congestion chính từ TCP_INFO dạng văn bản.
def parse_tcp_info(detail: str) -> dict:
    result = {"raw_tcp_info": detail.strip()}
    tokens = detail.split()
    if tokens and ":" not in tokens[0]:
        result["cc_algorithm"] = tokens[0]
    pairs = dict(re.findall(r"([A-Za-z_]+):([^\s]+)", detail))
    for source, target in {
        "mss": "mss_bytes", "cwnd": "cwnd_packets",
        "ssthresh": "ssthresh_packets", "unacked": "unacked_packets",
        "bytes_sent": "bytes_sent", "bytes_acked": "bytes_acked",
        "bytes_received": "bytes_received", "lost": "lost_packets",
        "segs_out": "segs_out", "segs_in": "segs_in",
        "data_segs_out": "data_segs_out", "data_segs_in": "data_segs_in",
    }.items():
        value = number(pairs.get(source, ""))
        if value is not None:
            result[target] = value
    if "rtt" in pairs:
        values = pairs["rtt"].split("/", 1)
        value = number(values[0])
        if value is not None:
            result["rtt_ms"] = value
        if len(values) == 2 and number(values[1]) is not None:
            result["rttvar_ms"] = number(values[1])
    if "minrtt" in pairs and number(pairs["minrtt"]) is not None:
        result["min_rtt_ms"] = number(pairs["minrtt"])
    if "retrans" in pairs:
        values = pairs["retrans"].split("/", 1)
        if number(values[0]) is not None:
            result["retrans_current"] = number(values[0])
        if len(values) == 2 and number(values[1]) is not None:
            result["retrans_total"] = number(values[1])
    for source, target in (
        ("send", "send_rate_bps"),
        ("pacing_rate", "pacing_rate_bps"),
        ("delivery_rate", "delivery_rate_bps"),
    ):
        match = re.search(
            rf"\b{source}(?::|\s+)([0-9.]+[KMG]?bps)\b", detail
        )
        value = rate_bps(match.group(1) if match else "")
        if value is not None:
            result[target] = value
    if "cwnd_packets" in result and "mss_bytes" in result:
        result["cwnd_bytes"] = int(result["cwnd_packets"] * result["mss_bytes"])
    if "unacked_packets" in result and "mss_bytes" in result:
        result["bytes_in_flight"] = int(
            result["unacked_packets"] * result["mss_bytes"]
        )
    return result


# Tìm socket có đúng bốn tuple của SSH connection hiện tại.
def find_socket(output: str, endpoints: tuple[str, str]) -> tuple[str, str] | None:
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if all(endpoint in line for endpoint in endpoints):
            detail = lines[index + 1].strip() if index + 1 < len(lines) else ""
            return line.strip(), detail
    return None


# Ghi một JSON event và flush ngay để không mất dữ liệu khi trial dừng.
def write_event(handle, event: dict) -> None:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()


# Chạy sampler tới khi nhận SIGTERM từ workload driver.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pid-file", required=True, type=Path)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) != 4:
        raise RuntimeError("SSH_CONNECTION must contain four fields")
    client_ip, client_port, server_ip, server_port = connection
    endpoints = (f"{client_ip}:{client_port}", f"{server_ip}:{server_port}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(str(os.getpid()) + "\n", encoding="ascii")
    signal.signal(signal.SIGTERM, stop_sampler)
    signal.signal(signal.SIGINT, stop_sampler)
    with args.output.open("w", encoding="utf-8") as handle:
        write_event(handle, {
            "event": "collector_start", "time_ns": time.time_ns(),
            "transport": "tcp", "source": "linux_ss_tcp_info",
            "endpoint": "server", "pid": os.getpid(),
            "ssh_connection": " ".join(connection),
            "interval_ms": max(0.02, args.interval) * 1000.0,
        })
        while not STOP:
            if args.parent_pid:
                try:
                    os.kill(args.parent_pid, 0)
                except ProcessLookupError:
                    break
            checked = subprocess.run(
                ["ss", "-H", "-t", "-i", "-n"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, check=False,
            )
            matched = find_socket(checked.stdout, endpoints)
            if checked.returncode != 0:
                write_event(handle, {
                    "event": "collector_error", "time_ns": time.time_ns(),
                    "message": checked.stderr.strip(),
                })
            elif matched is None:
                write_event(handle, {
                    "event": "socket_not_found", "time_ns": time.time_ns(),
                    "endpoints": endpoints,
                })
            else:
                socket_line, detail = matched
                write_event(handle, {
                    "event": "metrics", "time_ns": time.time_ns(),
                    "socket": socket_line, **parse_tcp_info(detail),
                })
            time.sleep(max(0.02, args.interval))
        write_event(handle, {
            "event": "collector_stop", "time_ns": time.time_ns(),
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
