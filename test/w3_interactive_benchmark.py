#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import shlex
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect.  Install with:  pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["interactive_shell", "vim", "nano"]
DEFAULT_PROMPT    = "__W3_PROMPT__# "
DEFAULT_SSH3_PATH = "/ssh3-term"

PROBE_CHAR = "x"

_ANSI_SEQ   = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)

@dataclass
class SampleRecord:
    protocol:   str
    workload:   str
    round_id:   int
    sample_id:  int
    latency_ms: float


@dataclass
class FailureRecord:
    protocol:      str
    workload:      str
    round_id:      int
    sample_id:     int
    error_type:    str
    error_message: str


@dataclass
class SummaryRow:
    protocol:           str
    workload:           str
    n:                  int
    failures:           int
    success_rate_pct:   float
    min_ms:             Optional[float]
    mean_ms:            Optional[float]
    median_ms:          Optional[float]
    stdev_ms:           Optional[float]
    p95_ms:             Optional[float]
    p99_ms:             Optional[float]
    max_ms:             Optional[float]
    ci95_half_width_ms: Optional[float]

class W3Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args       = args
        self.target     = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records:  List[SampleRecord]  = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }

    def _session_command(self, protocol: str) -> str:
        target     = self.target
        ssh_common = ["ssh", "-tt"]
        if self.args.source_ip:
            ssh_common += ["-b", self.args.source_ip]
        if self.args.strict_host_key_checking:
            ssh_common += ["-o", "StrictHostKeyChecking=yes"]
        else:
            ssh_common += [
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ]
        if self.args.identity_file:
            ssh_common += ["-i", self.args.identity_file]
        if self.args.batch_mode:
            ssh_common += ["-o", "BatchMode=yes"]
        ssh_common += [target]

        if protocol == "ssh":
            return shlex.join(ssh_common)

        if protocol == "mosh":
            ssh_cmd    = shlex.join(ssh_common[:-1])
            mosh_parts = ["mosh", f"--ssh={ssh_cmd}"]
            if self.args.mosh_predict and self.args.mosh_predict != "adaptive":
                mosh_parts += ["--predict", self.args.mosh_predict]
            mosh_parts += [target]
            return shlex.join(mosh_parts)

        if protocol == "ssh3":
            parts = ["ssh3"]
            if self.args.identity_file:
                parts += ["-privkey", self.args.identity_file]
            if self.args.ssh3_insecure:
                parts += ["-insecure"]
            parts.append(f"{target}{self.args.ssh3_path}")
            return shlex.join(parts)

        raise ValueError(f"Unsupported protocol: {protocol}")

    def _open_session(self, protocol: str) -> tuple:
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
        )
        if self.args.log_pexpect:
            log_path = Path(self.args.output_dir) / f"pexpect_{protocol}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child.logfile_read = open(log_path, "a", encoding="utf-8")

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1='{self.args.prompt}'")
        child.expect_exact(self.args.prompt, timeout=self.args.timeout)

        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF)
        except Exception:
            child.close(force=True)
        finally:
            try:
                if getattr(child, "logfile_read", None) is not None:
                    child.logfile_read.close()
            except Exception:
                pass

    def _measure_interactive_shell(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        child.sendline("cat")
        child.expect_exact("\n", timeout=self.args.timeout)

        for _ in range(warmup):
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)

        latencies: List[float] = []
        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)
            end_ns = time.perf_counter_ns()
            lat = (end_ns - start_ns) / 1_000_000.0
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("c")
        child.sendcontrol("c")
        child.expect_exact(self.args.prompt, timeout=self.args.timeout)
        return latencies

    def _measure_vim(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        remote_file = self.args.remote_vim_file
        child.sendline(f"vim -Nu NONE -n {shlex.quote(remote_file)}")
        child.send("i")
        child.expect([r"-- INSERT --", r"INSERT"], timeout=self.args.timeout)

        for _ in range(warmup):
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)
            child.send("\x08")  

        latencies: List[float] = []
        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)
            end_ns = time.perf_counter_ns()
            child.send("\x08")
            lat = (end_ns - start_ns) / 1_000_000.0
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.send("\x1b")
        child.sendline(":q!")
        child.expect_exact(self.args.prompt, timeout=self.args.timeout)
        return latencies

    def _measure_nano(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        remote_file = self.args.remote_nano_file
        child.sendline(f"nano --ignorercfiles {shlex.quote(remote_file)}")
        child.expect([r"GNU nano", r"\^G Help"], timeout=self.args.timeout)

        for _ in range(warmup):
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)
            child.send("\x08")  

        latencies: List[float] = []
        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            child.send(PROBE_CHAR)
            child.expect_exact(PROBE_CHAR, timeout=self.args.timeout)
            end_ns = time.perf_counter_ns()
            child.send("\x08")
            lat = (end_ns - start_ns) / 1_000_000.0
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("x")
        child.send("n")
        child.expect_exact(self.args.prompt, timeout=self.args.timeout)
        return latencies

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> List[float]:
        def report_cb(s_idx: int, lat: float) -> None:
            self.results[protocol][workload].append(lat)
            self.records.append(
                SampleRecord(protocol, workload, trial_id, s_idx, lat)
            )
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {s_idx:>3}/{self.args.iterations}:"
                f" {lat:.2f} ms",
                flush=True
            )

        if workload == "interactive_shell":
            latencies = self._measure_interactive_shell(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        elif workload == "vim":
            latencies = self._measure_vim(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        elif workload == "nano":
            latencies = self._measure_nano(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        else:
            raise ValueError(f"Unsupported workload: {workload}")

        return latencies

    def _run_session_group(self, protocol: str, workload: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][workload].append(setup_ms)
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms"
                )

                try:
                    self._run_trial(child, protocol, workload, trial_id)
                except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                    self.failures.append(
                        FailureRecord(
                            protocol=protocol,
                            workload=workload,
                            round_id=trial_id,
                            sample_id=-1,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                    )
                    print(
                        f"[{protocol:>4}/{workload:<18}]"
                        f" trial {trial_id:>2}: FAIL"
                        f" ({type(exc).__name__}: {exc})"
                    )
                    if self.args.reopen_on_failure:
                        if child is not None:
                            self._close_session(child)
                        child, setup_ms = self._open_session(protocol)

            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
        pairs = [
            (p, w)
            for p in self.args.protocols
            for w in self.args.workloads
        ]
        if self.args.shuffle_pairs:
            random.shuffle(pairs)
        for protocol, workload in pairs:
            self._run_session_group(protocol, workload)

    @staticmethod
    def _percentile(values: List[float], p: float) -> Optional[float]:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        s     = sorted(values)
        k     = (len(s) - 1) * (p / 100.0)
        lower = math.floor(k)
        upper = math.ceil(k)
        if lower == upper:
            return s[int(k)]
        return s[lower] + (s[upper] - s[lower]) * (k - lower)

    def _summary_row(self, protocol: str, workload: str) -> SummaryRow:
        data   = self.results[protocol][workload]
        fail_n = sum(
            1 for f in self.failures
            if f.protocol == protocol and f.workload == workload
        )
        n     = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None,
            )

        mean_ms   = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms  = statistics.stdev(data) if n > 1 else 0.0
        ci95      = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol,
            workload=workload,
            n=n,
            failures=fail_n,
            success_rate_pct=success_rate,
            min_ms=min(data),
            mean_ms=mean_ms,
            median_ms=median_ms,
            stdev_ms=stdev_ms,
            p95_ms=self._percentile(data, 95),
            p99_ms=self._percentile(data, 99),
            max_ms=max(data),
            ci95_half_width_ms=ci95,
        )

    def summaries(self) -> List[SummaryRow]:
        return [
            self._summary_row(p, w)
            for p in self.args.protocols
            for w in self.args.workloads
        ]

    def _session_setup_stats(self, protocol: str, workload: str) -> dict:
        data = self.session_setups[protocol][workload]
        if not data:
            return dict(n=0, mean=None, median=None, stdev=None,
                        min=None, max=None)
        n = len(data)
        return dict(
            n=n,
            mean=statistics.mean(data),
            median=statistics.median(data),
            stdev=statistics.stdev(data) if n > 1 else 0.0,
            min=min(data),
            max=max(data),
        )

    def print_report(self) -> None:
        def fmt(v: Optional[float]) -> str:
            return f"{v:.2f}" if v is not None else "N/A"

        width = 146
        print("\n" + "=" * width)
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} |"
            f" {'Success%':>8} | {'Min':>8} | {'Mean':>8} | {'Median':>8} |"
            f" {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} |"
            f" {'CI95+/-':>9}"
        )
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<18} | {row.n:>4} |"
                f" {row.failures:>4} | {row.success_rate_pct:>8.1f} |"
                f" {fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} |"
                f" {fmt(row.median_ms):>8} | {fmt(row.stdev_ms):>8} |"
                f" {fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} |"
                f" {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
            )
        print("=" * width)

        ss_width = 96
        print("\n" + "-" * ss_width)
        print(
            "SESSION SETUP LATENCY (ms)  "
            "[spawn -> first shell prompt, PS1 export excluded]"
        )
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>3} |"
            f" {'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} |"
            f" {'Max':>8}"
        )
        print("-" * ss_width)
        for protocol in self.args.protocols:
            for workload in self.args.workloads:
                s = self._session_setup_stats(protocol, workload)
                print(
                    f"{protocol:<8} | {workload:<18} | {s['n']:>3} |"
                    f" {fmt(s['min']):>8} | {fmt(s['mean']):>8} |"
                    f" {fmt(s['median']):>8} | {fmt(s['stdev']):>8} |"
                    f" {fmt(s['max']):>8}"
                )
        print("-" * ss_width)

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        summary_json = outdir / "w3_summary.json"
        raw_csv      = outdir / "w3_raw_samples.csv"
        failures_csv = outdir / "w3_failures.csv"
        setup_csv    = outdir / "w3_session_setup.csv"

        payload = {
            "meta": {
                "started_at_utc":   self.started_at,
                "target":           self.target,
                "client_source_ip": self.args.source_ip,
                "protocols":        self.args.protocols,
                "workloads":        self.args.workloads,
                "trials":           self.args.trials,
                "iterations":       self.args.iterations,
                "warmup_rounds":    self.args.warmup_rounds,
                "timeout_sec":      self.args.timeout,
                "random_seed":      self.args.seed,
                "probe_char":       PROBE_CHAR,
                "topology": {
                    "client": "192.168.8.100",
                    "server": self.args.host,
                },
                "metric_name": "interactive_keystroke_latency_ms",
                "metric_note": (
                    "Single-character probe (PROBE_CHAR) injected via pexpect. "
                    "Latency = time from child.send(probe) to "
                    "child.expect_exact(probe). "
                    "Mirrors typometer methodology (Michel & Bonaventure 2023, "
                    "arXiv:2312.08396): one keystroke injected, wait for server "
                    "echo.  This is NOT physical keyboard-to-screen latency."
                ),
                "session_setup_note": (
                    "setup_ms = time from pexpect.spawn() to first shell prompt "
                    "(regex [#$>]\\s*$).  The 'export PS1' command is issued "
                    "after the clock stops and is excluded from this window."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {
                    w: self._session_setup_stats(p, w)
                    for w in self.args.workloads
                }
                for p in self.args.protocols
            },
        }
        summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with raw_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["protocol", "workload", "round_id", "sample_id", "latency_ms"]
            )
            for r in self.records:
                writer.writerow([
                    r.protocol, r.workload, r.round_id, r.sample_id,
                    f"{r.latency_ms:.6f}",
                ])

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "protocol", "workload", "round_id", "sample_id",
                "error_type", "error_message",
            ])
            for r in self.failures:
                writer.writerow([
                    r.protocol, r.workload, r.round_id, r.sample_id,
                    r.error_type, r.error_message,
                ])

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["protocol", "workload", "trial_id", "session_setup_ms"]
            )
            for p in self.args.protocols:
                for w in self.args.workloads:
                    for trial_id, ms in enumerate(
                        self.session_setups[p][w], start=1
                    ):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved summary JSON    : {summary_json}")
        print(f"Saved raw samples CSV : {raw_csv}")
        print(f"Saved failures CSV    : {failures_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W3 Interactive Editing benchmark")
    p.add_argument(
        "--host", default="192.168.8.102",
        help="Target host IP or hostname",
    )
    p.add_argument(
        "--user", default="trungnt",
        help="Remote username",
    )
    p.add_argument(
        "--source-ip", default="192.168.8.100",
        help="Client source IP for SSH / Mosh where supported",
    )
    p.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "id_rsa"),
        help="SSH private key path",
    )
    p.add_argument(
        "--protocols", nargs="+",
        default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS,
    )
    p.add_argument(
        "--workloads", nargs="+",
        default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS,
    )
    p.add_argument(
        "--trials", type=int, default=15,
        help="Independent sessions per protocol/workload pair",
    )
    p.add_argument(
        "--iterations", type=int, default=100,
        help="Recorded samples per trial",
    )
    p.add_argument(
        "--warmup-rounds", type=int, default=5,
        help="Warmup samples per trial (not recorded)",
    )
    p.add_argument(
        "--timeout", type=int, default=20,
        help="pexpect timeout in seconds",
    )
    p.add_argument(
        "--seed", type=int, default=42,
        help="Random seed",
    )
    p.add_argument(
        "--output-dir", default="w3_results",
        help="Directory for JSON/CSV outputs",
    )
    p.add_argument(
        "--prompt", default=DEFAULT_PROMPT,
        help="Unique shell prompt marker used after session is ready",
    )
    p.add_argument(
        "--ssh3-path", default=DEFAULT_SSH3_PATH,
        help="SSH3 terminal path suffix",
    )
    p.add_argument(
        "--ssh3-insecure", action="store_true",
        help="Pass -insecure to ssh3",
    )
    p.add_argument(
        "--batch-mode", action="store_true",
        help="Enable BatchMode for SSHv2 / Mosh bootstrap SSH",
    )
    p.add_argument(
        "--strict-host-key-checking", action="store_true",
        help="Keep strict host key checking enabled",
    )
    p.add_argument(
        "--mosh-predict", default="adaptive",
        choices=["adaptive", "always", "never"],
        help="Mosh prediction mode",
    )
    p.add_argument(
        "--remote-vim-file", default="/tmp/w3_vim_bench.txt",
        help="Remote file path used for the vim workload",
    )
    p.add_argument(
        "--remote-nano-file", default="/tmp/w3_nano_bench.txt",
        help="Remote file path used for the nano workload",
    )
    p.add_argument(
        "--shuffle-pairs", action="store_true",
        help="Shuffle protocol/workload execution order",
    )
    p.add_argument(
        "--reopen-on-failure", action="store_true",
        help="Reopen session after each failed measured sample",
    )
    p.add_argument(
        "--log-pexpect", action="store_true",
        help="Save raw pexpect terminal output per protocol",
    )
    return p

def main() -> int:
    parser = build_arg_parser()
    args   = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.warmup_rounds < 0:
        parser.error("--warmup-rounds must be >= 0")

    bench = W3Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())