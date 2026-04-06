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
ITERATIONS = 100
WARMUP_ROUNDS = 5
SESSION_TIMEOUT_SEC = 20
RANDOM_SEED = 42
OUTPUT_JSON = "w3_results_revised.json"
OUTPUT_CSV = "w3_raw_samples_revised.csv"
PROTOCOLS = ["ssh", "ssh3", "mosh"]
WORKLOADS = ["interactive_shell", "vim", "nano"]
SHELL_PROMPT = "__W3_PROMPT__# "
SHELL_READY_MARKER = "__W3_READY__"
FAILURE_DETAILS_LIMIT = 200


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


class W3InteractiveBenchmarker:
    """
    This script measures interactive terminal response latency observed by pexpect.
    It is more robust than a single-character matcher, but it is still not a
    physical keyboard-to-screen measurement.
    """

    def __init__(self, user, host):
        self.target = f"{user}@{host}"
        self.started_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.results = {p: {w: [] for w in WORKLOADS} for p in PROTOCOLS}
        self.failures = {p: {w: 0 for w in WORKLOADS} for p in PROTOCOLS}
        self.failure_details = []
        self.sample_counter = 0

    def _session_command(self, protocol):
        if protocol == "ssh":
            return f"ssh -tt -o BatchMode=yes {self.target}"
        if protocol == "ssh3":
            return f"ssh3 -privkey /home/trungnt/.ssh/id_rsa -insecure {self.target}/ssh3-term"
        if protocol == "mosh":
            return f'mosh --ssh="ssh -o BatchMode=yes" {self.target}'
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _open_session(self, protocol):
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=SESSION_TIMEOUT_SEC,
        )
        child.sendline("export TERM=xterm")
        child.sendline(f"export PS1='{SHELL_PROMPT}'")
        child.sendline(f"printf '{SHELL_READY_MARKER}\\n'")
        child.expect_exact(SHELL_READY_MARKER)
        child.expect_exact(SHELL_PROMPT)
        return child

    def _close_session(self, child):
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF)
        except Exception:
            child.close(force=True)

    def _next_token(self, protocol, workload):
        self.sample_counter += 1
        return f"__W3TOK__{protocol}__{workload}__{self.sample_counter:06d}__"

    def _record_failure(self, protocol, workload, err):
        self.failures[protocol][workload] += 1
        if len(self.failure_details) < FAILURE_DETAILS_LIMIT:
            self.failure_details.append(
                {
                    "protocol": protocol,
                    "workload": workload,
                    "error_type": type(err).__name__,
                    "error": str(err),
                    "sample_counter": self.sample_counter,
                }
            )

    def _expect_prompt(self, child):
        child.expect_exact(SHELL_PROMPT)

    def _flush_pending_output(self, child):
        while True:
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except Exception:
                break

    def _measure_interactive_shell(self, child, token):
        # Remote shell waits for a single line, then prints a unique ACK token.
        cmd = (
            "python3 -c '"
            "import sys; "
            "s=sys.stdin.readline().rstrip(\"\\n\"); "
            f"print(\"__ACK__{token}\", flush=True)"
            "'"
        )
        child.sendline(cmd)
        self._flush_pending_output(child)
        start_ns = time.perf_counter_ns()
        child.sendline(token)
        child.expect_exact(f"__ACK__{token}")
        end_ns = time.perf_counter_ns()
        self._expect_prompt(child)
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_vim(self, child, token):
        tmpfile = f"/tmp/w3_vim_{token}.txt"
        child.sendline(f"rm -f {tmpfile}")
        self._expect_prompt(child)

        child.sendline(f"vim -Nu NONE -n {tmpfile}")
        child.send("i")
        child.expect(r"INSERT|-- INSERT --")
        self._flush_pending_output(child)

        start_ns = time.perf_counter_ns()
        child.send(token)
        child.expect_exact(token)
        end_ns = time.perf_counter_ns()

        child.send("\x1b")
        child.sendline(":q!")
        self._expect_prompt(child)
        child.sendline(f"rm -f {tmpfile}")
        self._expect_prompt(child)
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_nano(self, child, token):
        tmpfile = f"/tmp/w3_nano_{token}.txt"
        child.sendline(f"rm -f {tmpfile}")
        self._expect_prompt(child)

        child.sendline(f"nano --ignorercfiles {tmpfile}")
        child.expect(r"GNU nano|\^G Help")
        self._flush_pending_output(child)

        start_ns = time.perf_counter_ns()
        child.send(token)
        child.expect_exact(token)
        end_ns = time.perf_counter_ns()

        child.sendcontrol("x")
        child.send("n")
        self._expect_prompt(child)
        child.sendline(f"rm -f {tmpfile}")
        self._expect_prompt(child)
        return (end_ns - start_ns) / 1_000_000.0

    def _run_sample_in_session(self, child, protocol, workload, record=True):
        token = self._next_token(protocol, workload)
        try:
            if workload == "interactive_shell":
                latency = self._measure_interactive_shell(child, token)
            elif workload == "vim":
                latency = self._measure_vim(child, token)
            elif workload == "nano":
                latency = self._measure_nano(child, token)
            else:
                raise ValueError(f"Unsupported workload: {workload}")

            if record:
                self.results[protocol][workload].append(latency)
            return True, latency
        except (pexpect.TIMEOUT, pexpect.EOF, FileNotFoundError, ValueError) as err:
            self._record_failure(protocol, workload, err)
            return False, None

    def _run_protocol_workload(self, protocol, workload):
        child = None
        try:
            child = self._open_session(protocol)

            for _ in range(WARMUP_ROUNDS):
                self._run_sample_in_session(child, protocol, workload, record=False)

            for i in range(ITERATIONS):
                ok, latency = self._run_sample_in_session(child, protocol, workload, record=True)
                status = "OK" if ok else "FAIL"
                latency_text = f"{latency:.2f} ms" if latency is not None else "N/A"
                print(
                    f"[{protocol:<4}] {workload:<18} sample {i + 1:>3}/{ITERATIONS} -> {status:<4} {latency_text}"
                )
        finally:
            if child is not None:
                self._close_session(child)

    def execute_workload(self):
        random.seed(RANDOM_SEED)
        pairs = [(p, w) for p in PROTOCOLS for w in WORKLOADS]
        random.shuffle(pairs)

        print(
            "This benchmark measures pexpect-observed interactive response latency, "
            "not physical keyboard-to-screen latency."
        )
        print(
            f"Configuration: protocols={PROTOCOLS}, workloads={WORKLOADS}, "
            f"warmup={WARMUP_ROUNDS}, iterations={ITERATIONS}"
        )

        for protocol, workload in pairs:
            print("\n" + "=" * 90)
            print(f"Running protocol={protocol}, workload={workload}")
            print("=" * 90)
            self._run_protocol_workload(protocol, workload)

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
                "metric_name": "interactive_response_latency_ms",
                "n": 0,
                "failures": fail_n,
                "success_rate_pct": success_rate,
                "mean_ms": None,
                "median_ms": None,
                "stdev_ms": None,
                "p95_ms": None,
                "p99_ms": None,
                "min_ms": None,
                "max_ms": None,
                "ci95_half_width_ms": None,
            }

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        p95_ms = percentile(data, 95)
        p99_ms = percentile(data, 99)
        ci95_half = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        return {
            "protocol": protocol,
            "workload": workload,
            "metric_name": "interactive_response_latency_ms",
            "n": n,
            "failures": fail_n,
            "success_rate_pct": success_rate,
            "mean_ms": mean_ms,
            "median_ms": median_ms,
            "stdev_ms": stdev_ms,
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
            "min_ms": min(data),
            "max_ms": max(data),
            "ci95_half_width_ms": ci95_half,
        }

    def show_report(self):
        print("\n" + "=" * 150)
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Mean(ms)':>9} | {'Median':>9} | {'StdDev':>9} | {'P95':>9} | {'P99':>9} | "
            f"{'Min':>9} | {'Max':>9} | {'CI95+/-':>9}"
        )
        print("-" * 150)
        for protocol in PROTOCOLS:
            for workload in WORKLOADS:
                s = self._summary_row(protocol, workload)

                def fmt(value):
                    return f"{value:.2f}" if value is not None else "N/A"

                print(
                    f"{protocol:<8} | {workload:<18} | {s['n']:>4} | {s['failures']:>4} | {s['success_rate_pct']:>8.1f} | "
                    f"{fmt(s['mean_ms']):>9} | {fmt(s['median_ms']):>9} | {fmt(s['stdev_ms']):>9} | "
                    f"{fmt(s['p95_ms']):>9} | {fmt(s['p99_ms']):>9} | {fmt(s['min_ms']):>9} | "
                    f"{fmt(s['max_ms']):>9} | {fmt(s['ci95_half_width_ms']):>9}"
                )
            print("-" * 150)

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
                "metric_name": "interactive_response_latency_ms",
                "note": (
                    "Measured via pexpect-observed response tokens. This is suitable "
                    "for relative comparison of terminal responsiveness, but it is not "
                    "a physical keyboard-to-screen latency measurement."
                ),
            },
            "summary": [],
            "raw_samples_ms": self.results,
            "failures": self.failures,
            "failure_details": self.failure_details,
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
    bench = W3InteractiveBenchmarker(TARGET_USER, TARGET_HOST)
    bench.execute_workload()
    bench.show_report()
    bench.export_results()
