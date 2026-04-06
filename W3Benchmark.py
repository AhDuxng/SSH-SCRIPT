import csv
import json
import math
import random
import statistics
import time
from datetime import datetime

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with 'pip install pexpect'."
    ) from exc

TARGET_USER = "trungnt"
TARGET_HOST = "100.106.17.78"
ITERATIONS = 20
WARMUP_ROUNDS = 2
SESSION_TIMEOUT_SEC = 20
RANDOM_SEED = 42
OUTPUT_JSON = "w3_results.json"
OUTPUT_CSV = "w3_raw_samples.csv"
PROTOCOLS = ["ssh", "ssh3", "mosh"]
WORKLOADS = ["interactive_shell", "vim", "nano"]
KEY_POOL = list("asdfjklqwertyuiopzxcvbnm")
SHELL_PROMPT = "__W3_PROMPT__# "


def percentile(values, p):
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


class W3KeystrokeBenchmarker:
    def __init__(self, user, host):
        self.target = f"{user}@{host}"
        self.started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.results = {p: {w: [] for w in WORKLOADS} for p in PROTOCOLS}
        self.failures = {p: {w: 0 for w in WORKLOADS} for p in PROTOCOLS}

    def _session_command(self, protocol):
        if protocol == "ssh":
            return f"ssh -tt -o BatchMode=yes {self.target}"
        if protocol == "ssh3":
            return f"ssh3 -privkey /home/trungnt/.ssh/id_rsa -insecure {self.target}/ssh3-term"
        if protocol == "mosh":
            return f"mosh --ssh=\"ssh -o BatchMode=yes\" {self.target}"
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _open_session(self, protocol):
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            timeout=SESSION_TIMEOUT_SEC,
        )
        child.sendline(f"export PS1='{SHELL_PROMPT}'")
        child.expect_exact(SHELL_PROMPT)
        return child

    def _close_session(self, child):
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF)
        except Exception:
            child.close(force=True)

    def _measure_interactive_shell(self, child, key):
        cmd = "bash -lc 'IFS= read -r -n1 c; printf \"__ACK__%s\\n\" \"$c\"'"
        child.sendline(cmd)
        start_ns = time.perf_counter_ns()
        child.send(key)
        child.expect_exact(f"__ACK__{key}")
        end_ns = time.perf_counter_ns()
        child.expect_exact(SHELL_PROMPT)
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_vim(self, child, key):
        child.sendline("vim -Nu NONE -n /tmp/w3_vim_bench.txt")
        child.send("i")
        child.expect("INSERT|-- INSERT --")

        start_ns = time.perf_counter_ns()
        child.send(key)
        child.expect_exact(key)
        end_ns = time.perf_counter_ns()

        child.send("\x1b")
        child.sendline(":q!")
        child.expect_exact(SHELL_PROMPT)
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_nano(self, child, key):
        child.sendline("nano --ignorercfiles /tmp/w3_nano_bench.txt")
        child.expect("GNU nano|\^G Help")

        start_ns = time.perf_counter_ns()
        child.send(key)
        child.expect_exact(key)
        end_ns = time.perf_counter_ns()

        child.sendcontrol("x")
        child.send("n")
        child.expect_exact(SHELL_PROMPT)
        return (end_ns - start_ns) / 1_000_000.0

    def _run_one_sample(self, protocol, workload, key):
        child = None
        try:
            child = self._open_session(protocol)
            if workload == "interactive_shell":
                latency = self._measure_interactive_shell(child, key)
            elif workload == "vim":
                latency = self._measure_vim(child, key)
            elif workload == "nano":
                latency = self._measure_nano(child, key)
            else:
                raise ValueError(f"Unsupported workload: {workload}")

            self.results[protocol][workload].append(latency)
            return True
        except (pexpect.TIMEOUT, pexpect.EOF, FileNotFoundError, ValueError) as err:
            print(f"\n[FAIL] {protocol}/{workload}: {err}")
            self.failures[protocol][workload] += 1
            return False
        finally:
            if child is not None:
                self._close_session(child)

    def execute_workload(self):
        random.seed(RANDOM_SEED)
        pairs = [(p, w) for p in PROTOCOLS for w in WORKLOADS]

        if WARMUP_ROUNDS > 0:
            print(f"--- Warmup: {WARMUP_ROUNDS} rounds (not recorded) ---")
            for i in range(WARMUP_ROUNDS):
                random.shuffle(pairs)
                for protocol, workload in pairs:
                    key = random.choice(KEY_POOL)
                    self._run_one_sample(protocol, workload, key)
                print(f" Warmup round {i + 1}/{WARMUP_ROUNDS} done.")

        print(f"--- Measurement: {ITERATIONS} rounds ---")
        for i in range(ITERATIONS):
            random.shuffle(pairs)
            print(f" Round {i + 1}/{ITERATIONS}")
            for protocol, workload in pairs:
                key = random.choice(KEY_POOL)
                print(f"  Running [{protocol}] {workload} key='{key}'", end="...", flush=True)
                ok = self._run_one_sample(protocol, workload, key)
                print(" OK" if ok else " FAIL")

    def _summary_row(self, protocol, workload):
        data = self.results[protocol][workload]
        n = len(data)
        fail_n = self.failures[protocol][workload]
        total = n + fail_n
        success_rate = (n / total * 100.0) if total > 0 else 0.0

        if n == 0:
            return {
                "protocol": protocol,
                "workload": workload,
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
            "workload": workload,
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
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Mean(ms)':>9} | {'Median':>9} | {'StdDev':>9} | {'P95':>9} | {'CI95+/-':>9}"
        )
        print("-" * 118)
        for protocol in PROTOCOLS:
            for workload in WORKLOADS:
                s = self._summary_row(protocol, workload)
                mean_text = f"{s['mean_ms']:.2f}" if s["mean_ms"] is not None else "N/A"
                median_text = f"{s['median_ms']:.2f}" if s["median_ms"] is not None else "N/A"
                stdev_text = f"{s['stdev_ms']:.2f}" if s["stdev_ms"] is not None else "N/A"
                p95_text = f"{s['p95_ms']:.2f}" if s["p95_ms"] is not None else "N/A"
                ci95_text = f"{s['ci95_half_width_ms']:.2f}" if s["ci95_half_width_ms"] is not None else "N/A"

                print(
                    f"{protocol:<8} | {workload:<18} | {s['n']:>4} | {s['failures']:>4} | {s['success_rate_pct']:>8.1f} | "
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
                "timeout_sec": SESSION_TIMEOUT_SEC,
                "random_seed": RANDOM_SEED,
                "protocols": PROTOCOLS,
                "workloads": WORKLOADS,
            },
            "summary": [],
            "raw_samples_ms": self.results,
            "failures": self.failures,
        }

        for protocol in PROTOCOLS:
            for workload in WORKLOADS:
                payload["summary"].append(self._summary_row(protocol, workload))

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "sample_index", "latency_ms"])
            for protocol in PROTOCOLS:
                for workload in WORKLOADS:
                    for idx, value in enumerate(self.results[protocol][workload], start=1):
                        writer.writerow([protocol, workload, idx, f"{value:.6f}"])

        print(f"\nSaved JSON report: {json_path}")
        print(f"Saved raw CSV: {csv_path}")


if __name__ == "__main__":
    bench = W3KeystrokeBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()
    bench.export_results()
