import subprocess
import time
import statistics
import random
import math
import csv
import json
from datetime import datetime

TARGET_USER = "trungnt"
TARGET_HOST = "100.106.17.78"
ITERATIONS = 10
WARMUP_ROUNDS = 2
COMMAND_TIMEOUT_SEC = 30
RANDOM_SEED = 42
OUTPUT_JSON = "w1_results.json"
OUTPUT_CSV = "w1_raw_samples.csv"
COMMANDS = [
    "ls",
    "df -h",
    "ps aux",
    "ps aux | grep root"
]
PROTOCOLS = ["ssh", "ssh3", "mosh"]


def percentile(values, p):
    """Return percentile using linear interpolation."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]

    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * (p / 100.0)
    lower = math.floor(k)
    upper = math.ceil(k)
    if lower == upper:
        return sorted_values[int(k)]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (k - lower)


class LatencyBenchmarker:
    def __init__(self, user, host):
        self.target = f"{user}@{host}"
        self.started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.results = {p: {c: [] for c in COMMANDS} for p in PROTOCOLS}
        self.failures = {p: {c: 0 for c in COMMANDS} for p in PROTOCOLS}

    def run_cmd(self, protocol, command):
        """Run one command and return latency in milliseconds."""
        full_cmd = []
        if protocol == "ssh":
            full_cmd = ["ssh", "-o", "BatchMode=yes", self.target, command]
        elif protocol == "ssh3":
            full_cmd = ["ssh3", "-privkey", "/home/trungnt/.ssh/id_rsa", "-insecure", f"{self.target}/ssh3-term", command]
        elif protocol == "mosh":
            full_cmd = ["mosh", "--ssh=ssh -o BatchMode=yes", self.target, "--", command]
        else:
            raise ValueError(f"Unsupported protocol: {protocol}")

        start_ns = time.perf_counter_ns()
        try:
            subprocess.run(
                full_cmd, 
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.PIPE, 
                text=True,              
                check=True,
                timeout=COMMAND_TIMEOUT_SEC,
            )
            end_ns = time.perf_counter_ns()
            return (end_ns - start_ns) / 1_000_000.0
            
        except subprocess.CalledProcessError as e:
            print(f"\n[SERVER ERROR] Protocol {protocol.upper()} failed: {e.stderr.strip()}")
            return None
        except subprocess.TimeoutExpired:
            print(f"\n[TIMEOUT] Protocol {protocol.upper()} exceeded {COMMAND_TIMEOUT_SEC}s for command: {command}")
            return None
        except FileNotFoundError:
            print(f"\n[SYSTEM ERROR] Command not found: '{full_cmd[0]}'.")
            return None
        except Exception as e:
            print(f"\n[UNKNOWN ERROR]: {e}")
            return None

    def _run_one_sample(self, protocol, command, record_result):
        latency = self.run_cmd(protocol, command)
        if latency is not None:
            if record_result:
                self.results[protocol][command].append(latency)
            return True
        self.failures[protocol][command] += 1
        return False

    def execute_workload(self):
        random.seed(RANDOM_SEED)
        pairs = [(p, cmd) for p in PROTOCOLS for cmd in COMMANDS]

        if WARMUP_ROUNDS > 0:
            print(f"--- Warmup: {WARMUP_ROUNDS} rounds (not recorded) ---")
            for i in range(WARMUP_ROUNDS):
                random.shuffle(pairs)
                for protocol, cmd in pairs:
                    self._run_one_sample(protocol, cmd, record_result=False)
                print(f" Warmup round {i + 1}/{WARMUP_ROUNDS} done.")

        print(f"--- Measurement: {ITERATIONS} rounds ---")
        for i in range(ITERATIONS):
            random.shuffle(pairs)
            print(f" Round {i + 1}/{ITERATIONS}")
            for protocol, cmd in pairs:
                print(f"  Running [{protocol}] {cmd}", end="...", flush=True)
                ok = self._run_one_sample(protocol, cmd, record_result=True)
                print(" OK" if ok else " FAIL")

    def _summary_row(self, protocol, command):
        data = self.results[protocol][command]
        n = len(data)
        fail_n = self.failures[protocol][command]
        total = n + fail_n
        success_rate = (n / total * 100.0) if total > 0 else 0.0
        if n == 0:
            return {
                "protocol": protocol,
                "command": command,
                "n": 0,
                "failures": fail_n,
                "success_rate_pct": success_rate,
                "mean_ms": None,
                "median_ms": None,
                "stdev_ms": None,
                "p95_ms": None,
                "ci95_half_width_ms": None,
            }

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        p95_ms = percentile(data, 95)
        ci95_half = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        return {
            "protocol": protocol,
            "command": command,
            "n": n,
            "failures": fail_n,
            "success_rate_pct": success_rate,
            "mean_ms": mean_ms,
            "median_ms": median_ms,
            "stdev_ms": stdev_ms,
            "p95_ms": p95_ms,
            "ci95_half_width_ms": ci95_half,
        }

    def show_report(self):
        print("\n" + "=" * 118)
        print(
            f"{'Protocol':<8} | {'Command':<20} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Mean(ms)':>9} | {'Median':>9} | {'StdDev':>9} | {'P95':>9} | {'CI95+/-':>9}"
        )
        print("-" * 118)
        for p in PROTOCOLS:
            for cmd in COMMANDS:
                s = self._summary_row(p, cmd)
                mean_text = f"{s['mean_ms']:.2f}" if s["mean_ms"] is not None else "N/A"
                median_text = f"{s['median_ms']:.2f}" if s["median_ms"] is not None else "N/A"
                stdev_text = f"{s['stdev_ms']:.2f}" if s["stdev_ms"] is not None else "N/A"
                p95_text = f"{s['p95_ms']:.2f}" if s["p95_ms"] is not None else "N/A"
                ci95_text = f"{s['ci95_half_width_ms']:.2f}" if s["ci95_half_width_ms"] is not None else "N/A"

                print(
                    f"{p:<8} | {cmd:<20} | {s['n']:>4} | {s['failures']:>4} | {s['success_rate_pct']:>8.1f} | "
                    f"{mean_text:>9} | {median_text:>9} | {stdev_text:>9} | {p95_text:>9} | {ci95_text:>9}"
                )
            print("-" * 118)

    def export_results(self, json_path=OUTPUT_JSON, csv_path=OUTPUT_CSV):
        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "iterations": ITERATIONS,
                "warmup_rounds": WARMUP_ROUNDS,
                "timeout_sec": COMMAND_TIMEOUT_SEC,
                "random_seed": RANDOM_SEED,
                "protocols": PROTOCOLS,
                "commands": COMMANDS,
            },
            "summary": [],
            "raw_samples_ms": self.results,
            "failures": self.failures,
        }

        for p in PROTOCOLS:
            for cmd in COMMANDS:
                payload["summary"].append(self._summary_row(p, cmd))

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "command", "sample_index", "latency_ms"])
            for p in PROTOCOLS:
                for cmd in COMMANDS:
                    for idx, value in enumerate(self.results[p][cmd], start=1):
                        writer.writerow([p, cmd, idx, f"{value:.6f}"])

        print(f"\nSaved JSON report: {json_path}")
        print(f"Saved raw CSV: {csv_path}")

if __name__ == "__main__":
    bench = LatencyBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()
    bench.export_results()