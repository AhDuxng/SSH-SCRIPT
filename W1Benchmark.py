"""
=============================================================
  BENCHMARK: SSH vs SSH3 vs MOSH
=============================================================
"""

import subprocess
import time
import statistics
import json
import sys
import shutil
from datetime import datetime

TARGET_USER = "trungnt"
TARGET_HOST = "100.106.17.78"
SSH3_KEY    = "/home/trungnt/.ssh/id_rsa"
ITERATIONS  = 10
TIMEOUT     = 15  

COMMANDS = {
    "ls"           : "ls",
    "df -h"        : "df -h",
    "ps aux"       : "ps aux",
    "ps|grep root" : "ps aux | grep root",
}

PROTOCOLS = ["ssh", "ssh3", "mosh"]

def check_dependencies():
    """Kiểm tra các công cụ cần thiết đã được cài chưa."""
    missing = []
    for tool in ["ssh", "ssh3", "mosh", "expect"]:
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        print(f"[CẢNH BÁO] Các công cụ sau chưa được cài: {', '.join(missing)}")
        print("           Giao thức tương ứng sẽ bị bỏ qua khi chạy.\n")
    return missing


def build_command(protocol: str, target: str, remote_cmd: str) -> list:
    """Tạo lệnh subprocess tương ứng với từng giao thức."""
    if protocol == "ssh":
        return ["ssh", "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                target, remote_cmd]

    elif protocol == "ssh3":
        return ["ssh3",
                "-privkey", SSH3_KEY,
                "-insecure",
                target, remote_cmd]

    elif protocol == "mosh":
        
        expect_script = (
            f'set timeout {TIMEOUT}\n'
            f'spawn mosh {target}\n'
            f'expect -re "\\\\$\\\\s"\n'
            f'send "{remote_cmd}\\n"\n'
            f'expect -re "\\\\$\\\\s"\n'
            f'send "exit\\n"\n'
            f'expect eof\n'
        )
        return ["expect", "-c", expect_script]

    return []


class LatencyBenchmarker:
    def __init__(self, user: str, host: str):
        self.target  = f"{user}@{host}"
        self.missing = check_dependencies()
    
        self.results = {
            p: {label: [] for label in COMMANDS}
            for p in PROTOCOLS
        }

    def run_once(self, protocol: str, label: str) -> float | None:
        remote_cmd = COMMANDS[label]
        full_cmd   = build_command(protocol, self.target, remote_cmd)

        if not full_cmd:
            return None

        start = time.perf_counter()
        try:
            subprocess.run(
                full_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=TIMEOUT,
            )
            return (time.perf_counter() - start) * 1000

        except subprocess.TimeoutExpired:
            print(f"\n  [TIMEOUT] {protocol.upper()} '{label}' > {TIMEOUT}s")
            return None
        except subprocess.CalledProcessError as e:
            err = e.stderr.strip().splitlines()[-1] if e.stderr.strip() else "unknown"
            print(f"\n  [LỖI] {protocol.upper()} '{label}': {err}")
            return None
        except FileNotFoundError:
            return None  

    def execute_workload(self):
        print(f"\n{'='*55}")
        print(f"  BẮT ĐẦU BENCHMARK  —  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Target : {self.target}")
        print(f"  Số lần lặp: {ITERATIONS} / lệnh / giao thức")
        print(f"{'='*55}\n")

        for p in PROTOCOLS:
            tool_bin = "expect" if p == "mosh" else p
            if tool_bin in self.missing:
                print(f"[BỎ QUA] {p.upper()} — công cụ chưa cài.")
                continue

            print(f"Giao thức: {p.upper()}")
            for label in COMMANDS:
                print(f"   Lệnh: {label:<18}", end="", flush=True)
                ok = 0
                for i in range(ITERATIONS):
                    ms = self.run_once(p, label)
                    if ms is not None:
                        self.results[p][label].append(ms)
                        ok += 1
                    print(".", end="", flush=True)
                print(f" ({ok}/{ITERATIONS} thành công)")
            print()

    def show_report(self):
        print(f"\n{'='*70}")
        print(f"  KẾT QUẢ BENCHMARK")
        print(f"{'='*70}")
        hdr = f"{'Giao thức':<10}  {'Lệnh':<18}  {'TB (ms)':>9}  {'Min':>7}  {'Max':>7}  {'StdDev':>7}  {'Mẫu':>5}"
        print(hdr)
        print("-" * 70)

        for p in PROTOCOLS:
            for label in COMMANDS:
                data = self.results[p][label]
                if data:
                    avg   = statistics.mean(data)
                    mn    = min(data)
                    mx    = max(data)
                    stdev = statistics.stdev(data) if len(data) > 1 else 0.0
                    print(
                        f"{p:<10}  {label:<18}  {avg:>9.2f}  "
                        f"{mn:>7.2f}  {mx:>7.2f}  {stdev:>7.2f}  "
                        f"{len(data):>3}/{ITERATIONS}"
                    )
                else:
                    print(f"{p:<10}  {label:<18}  {'—':>9}  {'—':>7}  {'—':>7}  {'—':>7}  {'0':>3}/{ITERATIONS}")
            print("-" * 70)

        self._show_winner()

    def _show_winner(self):
        print("\n  TỔNG HỢP — Trung bình toàn bộ lệnh / giao thức\n")
        summary = {}
        for p in PROTOCOLS:
            all_data = []
            for label in COMMANDS:
                all_data.extend(self.results[p][label])
            if all_data:
                summary[p] = statistics.mean(all_data)

        if not summary:
            print("  Không có dữ liệu.\n")
            return

        ranked = sorted(summary.items(), key=lambda x: x[1])
        medals = ["1", "2", "3"]
        for i, (p, avg) in enumerate(ranked):
            medal = medals[i] if i < 3 else "  "
            print(f"  {medal}  {p.upper():<6}  {avg:>8.2f} ms trung bình")
        print()

    def save_json(self, path: str = "benchmark_results.json"):
        output = {
            "meta": {
                "target"    : self.target,
                "iterations": ITERATIONS,
                "timestamp" : datetime.now().isoformat(),
            },
            "results": self.results,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"  [JSON] Kết quả đã lưu → {path}\n")


if __name__ == "__main__":
    bench = LatencyBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()
    bench.save_json("benchmark_results.json")