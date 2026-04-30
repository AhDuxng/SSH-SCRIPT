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
from typing import Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["command_loop"]
DEFAULT_PROMPT = "__W1_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
MARKER_TAIL_LEN = 12
_TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_COMMANDS = [
    "ls",
    "df -h",
    "ps aux",
    "grep -n root /etc/passwd",
]
_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    command_id: int
    command: str
    latency_ms: float


@dataclass
class FailureRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    command_id: int
    command: str
    error_type: str
    error_message: str


@dataclass
class SummaryRow:
    protocol: str
    workload: str
    command: str
    n: int
    failures: int
    success_rate_pct: float
    min_ms: Optional[float]
    mean_ms: Optional[float]
    median_ms: Optional[float]
    stdev_ms: Optional[float]
    p95_ms: Optional[float]
    p99_ms: Optional[float]
    max_ms: Optional[float]
    ci95_half_width_ms: Optional[float]


class W1Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        # Prompt bytes can be split by ANSI redraw sequences (esp. over mosh).
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.prev_marker_tail: Optional[str] = None

        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        child.expect(self.prompt_re, timeout=self.args.timeout)

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    @staticmethod
    def _build_token_re(token: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in token]
        return re.compile("".join(parts))

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _next_marker_tail(self) -> str:
        # Mosh can send screen deltas only. Ensure each position changes so
        # this tail is fully emitted and matchable in the pty stream.
        if self.prev_marker_tail is None:
            tail = "".join(random.choice(_TAIL_ALPHABET) for _ in range(MARKER_TAIL_LEN))
            self.prev_marker_tail = tail
            return tail

        chars: List[str] = []
        for i, prev_ch in enumerate(self.prev_marker_tail):
            candidates = [c for c in _TAIL_ALPHABET if c != prev_ch]
            chars.append(random.choice(candidates))
        tail = "".join(chars)
        self.prev_marker_tail = tail
        return tail

    def _session_command(self, protocol: str) -> str:
        target = self.target
        ssh_common = ["ssh", "-tt"]
        if self.args.source_ip:
            ssh_common += ["-b", self.args.source_ip]
        if self.args.strict_host_key_checking:
            ssh_common += ["-o", "StrictHostKeyChecking=yes"]
        else:
            ssh_common += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        if self.args.identity_file:
            ssh_common += ["-i", self.args.identity_file]
        if self.args.batch_mode:
            ssh_common += ["-o", "BatchMode=yes"]
        ssh_common += [target]

        if protocol == "ssh":
            return shlex.join(ssh_common)

        if protocol == "mosh":
            ssh_cmd = shlex.join(ssh_common[:-1])
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

    def _open_session(self, protocol: str) -> tuple[pexpect.spawn, float]:
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

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child)
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

    def _measure_command_completion(
        self,
        child: pexpect.spawn,
        command: str,
        marker: str,
        marker_tail: str,
    ) -> float:
        marker_re = self._build_token_re(marker)
        marker_tail_re = self._build_token_re(marker_tail)
        wrapped = f"{{ {command}; }}; echo {marker}"
        self._drain_pending_output(child)
        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        child.expect([marker_re, marker_tail_re], timeout=self.args.timeout)
        end_ns = time.perf_counter_ns()
        return (end_ns - start_ns) / 1_000_000.0

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        command: str,
        command_id: int,
        trial_id: int,
    ) -> None:
        for sample_id in range(1, self.args.iterations + 1):
            marker_tail = self._next_marker_tail()
            marker = f"__W1_DONE_{trial_id}_{sample_id}_{command_id}_{marker_tail}__"
            try:
                lat = self._measure_command_completion(
                    child,
                    command,
                    marker,
                    marker_tail,
                )
                self.results[protocol][command].append(lat)
                self.records.append(
                    SampleRecord(protocol, workload, trial_id, sample_id, command_id, command, lat)
                )
                print(
                    f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
                    f" measure {sample_id:>3}/{self.args.iterations}: {lat:.2f} ms",
                    flush=True,
                )
            except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                self.failures.append(
                    FailureRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=sample_id,
                        command_id=command_id,
                        command=command,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
                )
                print(
                    f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}"
                    f" measure {sample_id:>3}: FAIL ({type(exc).__name__}: {exc})",
                    flush=True,
                )
                if self.args.reopen_on_failure:
                    raise

    def _run_session_group(
        self,
        protocol: str,
        workload: str,
        command: str,
        command_id: int,
    ) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][command].append(setup_ms)
                print(
                    f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms",
                    flush=True,
                )
                try:
                    self._run_trial(
                        child,
                        protocol,
                        workload,
                        command,
                        command_id,
                        trial_id,
                    )
                except (pexpect.TIMEOUT, pexpect.EOF):
                    if self.args.reopen_on_failure:
                        continue
            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
        sequence = [
            (p, w, c, command_id)
            for p in self.args.protocols
            for w in self.args.workloads
            for command_id, c in enumerate(self.args.commands, start=1)
        ]
        if self.args.shuffle_pairs:
            random.shuffle(sequence)
        for protocol, workload, command, command_id in sequence:
            self._run_session_group(protocol, workload, command, command_id)

    @staticmethod
    def _percentile(values: List[float], p: float) -> Optional[float]:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        s = sorted(values)
        k = (len(s) - 1) * (p / 100.0)
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return s[int(k)]
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    def _summary_row(self, protocol: str, workload: str, command: str) -> SummaryRow:
        data = self.results[protocol][command]
        fail_n = sum(
            1
            for f in self.failures
            if f.protocol == protocol and f.workload == workload and f.command == command
        )
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(protocol, workload, command, 0, fail_n, success_rate, None, None, None, None, None, None, None, None)

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol,
            workload=workload,
            command=command,
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
            self._summary_row(p, w, c)
            for p in self.args.protocols
            for w in self.args.workloads
            for c in self.args.commands
        ]

    def _session_setup_stats(self, protocol: str, workload: str) -> dict:
        data = self.session_setups[protocol][workload]
        if not data:
            return dict(n=0, mean=None, median=None, stdev=None, min=None, max=None)
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

        width = 168
        print("\n" + "=" * width)
        print(
            f"{'Protocol':<8} | {'Workload':<12} | {'Command':<26} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+/-':>9}"
        )
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<12} | {row.command:<26} | {row.n:>4} | {row.failures:>4} | "
                f"{row.success_rate_pct:>8.1f} | {fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | "
                f"{fmt(row.stdev_ms):>8} | {fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} | {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
            )
        print("=" * width)

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        summary_json = outdir / "w1_summary.json"
        raw_csv = outdir / "w1_raw_samples.csv"
        failures_csv = outdir / "w1_failures.csv"
        setup_csv = outdir / "w1_session_setup.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "workloads": self.args.workloads,
                "commands": self.args.commands,
                "trials": self.args.trials,
                "iterations": self.args.iterations,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "topology": {
                    "client": "192.168.8.100",
                    "server": self.args.host,
                },
                "metric_name": "command_completion_latency_ms",
                "metric_note": (
                    "For each command, latency = time from sendline(command) to "
                    "unique completion marker visibility. Marker matching "
                    "tolerates ANSI insertion and mosh partial redraw."
                ),
                "session_setup_note": (
                    "setup_ms = time from pexpect.spawn() to first shell prompt ([#$>] regex). "
                    "The 'export PS1' command is excluded from setup window."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {c: self._session_setup_stats(p, c) for c in self.args.commands}
                for p in self.args.protocols
            },
        }
        summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with raw_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "command_id", "command", "latency_ms"])
            for r in self.records:
                writer.writerow([r.protocol, r.workload, r.round_id, r.sample_id, r.command_id, r.command, f"{r.latency_ms:.6f}"])

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "command_id", "command", "error_type", "error_message"])
            for r in self.failures:
                writer.writerow([
                    r.protocol,
                    r.workload,
                    r.round_id,
                    r.sample_id,
                    r.command_id,
                    r.command,
                    r.error_type,
                    r.error_message,
                ])

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for w in self.args.commands:
                    for trial_id, ms in enumerate(self.session_setups[p][w], start=1):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved summary JSON    : {summary_json}")
        print(f"Saved raw samples CSV : {raw_csv}")
        print(f"Saved failures CSV    : {failures_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W1 Command Loop benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default="192.168.8.100", help="Client source IP for SSH / Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--commands", nargs="+", default=DEFAULT_COMMANDS, help="Commands executed sequentially in each sample")
    p.add_argument("--trials", type=int, default=15, help="Independent sessions per protocol/workload pair")
    p.add_argument("--iterations", type=int, default=100, help="Recorded command-loop samples per trial")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="w1_results", help="Directory for JSON/CSV outputs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Unique shell prompt marker used after session is ready")
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH, help="SSH3 terminal path suffix")
    p.add_argument("--ssh3-insecure", action="store_true", help="Pass -insecure to ssh3")
    p.add_argument("--batch-mode", action="store_true", help="Enable BatchMode for SSHv2 / Mosh bootstrap SSH")
    p.add_argument("--strict-host-key-checking", action="store_true", help="Keep strict host key checking enabled")
    p.add_argument("--mosh-predict", default="adaptive", choices=["adaptive", "always", "never"], help="Mosh prediction mode")
    p.add_argument("--shuffle-pairs", action="store_true", help="Shuffle protocol/workload execution order")
    p.add_argument("--reopen-on-failure", action="store_true", help="Reopen session after failure")
    p.add_argument("--log-pexpect", action="store_true", help="Save raw pexpect terminal output per protocol")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")

    bench = W1Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
