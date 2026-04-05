import subprocess
import time
import statistics
import json

TARGET_USER = "pi"
TARGET_HOST = "100.106.17.78"
ITERATIONS = 10  
COMMANDS = [
    "ls",
    "df -h",
    "ps aux",
    "ps aux | grep root"
]
PROTOCOLS = ["ssh", "ssh3", "mosh"]

class LatencyBenchmarker:
    def __init__(self, user, host):
        self.target = f"{user}@{host}"
        self.results = {p: {c: [] for c in COMMANDS} for p in PROTOCOLS}

    def run_cmd(self, protocol, command):
        """Thực thi lệnh dựa trên giao thức và trả về thời gian tính bằng ms"""
        full_cmd = []
        if protocol == "ssh":
            full_cmd = ["ssh", "-o", "BatchMode=yes", self.target, command]
        elif protocol == "ssh3":
            full_cmd = ["ssh3", self.target, command]
        elif protocol == "mosh":
            full_cmd = ["mosh", "--ssh=ssh -o BatchMode=yes", self.target, "--", command]

        start = time.perf_counter()

        try:
            subprocess.run(full_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            end = time.perf_counter()
            return (end - start) * 1000 
        except Exception:
            return None

    def execute_workload(self):
        for p in PROTOCOLS:
            print(f"--- Đang test Giao thức: {p.upper()} ---")
            for cmd in COMMANDS:
                print(f" Đang chạy lệnh: {cmd}", end="...", flush=True)
                for _ in range(ITERATIONS):
                    latency = self.run_cmd(p, cmd)
                    if latency:
                        self.results[p][cmd].append(latency)
                print(" Xong.")

    def show_report(self):
        print("\n" + "="*60)
        print(f"{'Protocol':<10} | {'Command':<20} | {'Avg (ms)':<10} | {'StdDev':<8}")
        print("-" * 60)
        for p in PROTOCOLS:
            for cmd in COMMANDS:
                data = self.results[p][cmd]
                if data:
                    avg = statistics.mean(data)
                    stdev = statistics.stdev(data) if len(data) > 1 else 0
                    print(f"{p:<10} | {cmd:<20} | {avg:<10.2f} | {stdev:<8.2f}")
            print("-" * 60)

if __name__ == "__main__":
    bench = LatencyBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()