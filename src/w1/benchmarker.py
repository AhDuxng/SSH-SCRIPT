import random
import statistics
import subprocess
import time
from datetime import datetime

from config import (
    COMMANDS,
    COMMAND_TIMEOUT_SEC,
    ITERATIONS,
    OUTPUT_CSV,
    OUTPUT_JSON,
    PROTOCOLS,
    RANDOM_SEED,
    WARMUP_ROUNDS,
)
from exporter import export_results
from reporter import print_report
from stats_utils import percentile


class LatencyBenchmarker:
    def __init__(self, user, host):
        self.target = f"{user}@{host}"
        self.started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.results = {protocol: {command: [] for command in COMMANDS} for protocol in PROTOCOLS}
        self.failures = {protocol: {command: 0 for command in COMMANDS} for protocol in PROTOCOLS}

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

        except subprocess.CalledProcessError as error:
            print(f"\n[SERVER ERROR] Protocol {protocol.upper()} failed: {error.stderr.strip()}")
            return None
        except subprocess.TimeoutExpired:
            print(f"\n[TIMEOUT] Protocol {protocol.upper()} exceeded {COMMAND_TIMEOUT_SEC}s for command: {command}")
            return None
        except FileNotFoundError:
            print(f"\n[SYSTEM ERROR] Command not found: '{full_cmd[0]}'.")
            return None
        except Exception as error:
            print(f"\n[UNKNOWN ERROR]: {error}")
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
        pairs = [(protocol, command) for protocol in PROTOCOLS for command in COMMANDS]

        if WARMUP_ROUNDS > 0:
            print(f"--- Warmup: {WARMUP_ROUNDS} rounds (not recorded) ---")
            for index in range(WARMUP_ROUNDS):
                random.shuffle(pairs)
                for protocol, command in pairs:
                    self._run_one_sample(protocol, command, record_result=False)
                print(f" Warmup round {index + 1}/{WARMUP_ROUNDS} done.")

        print(f"--- Measurement: {ITERATIONS} rounds ---")
        for index in range(ITERATIONS):
            random.shuffle(pairs)
            print(f" Round {index + 1}/{ITERATIONS}")
            for protocol, command in pairs:
                print(f"  Running [{protocol}] {command}", end="...", flush=True)
                ok = self._run_one_sample(protocol, command, record_result=True)
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
        ci95_half = (1.96 * stdev_ms / (n ** 0.5)) if n > 1 else 0.0
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
        print_report(PROTOCOLS, COMMANDS, self._summary_row)

    def export_results(self, json_path=OUTPUT_JSON, csv_path=OUTPUT_CSV):
        export_results(
            json_path=json_path,
            csv_path=csv_path,
            started_at=self.started_at,
            target=self.target,
            iterations=ITERATIONS,
            warmup_rounds=WARMUP_ROUNDS,
            timeout_sec=COMMAND_TIMEOUT_SEC,
            random_seed=RANDOM_SEED,
            protocols=PROTOCOLS,
            commands=COMMANDS,
            results=self.results,
            failures=self.failures,
            build_summary_row=self._summary_row,
        )
