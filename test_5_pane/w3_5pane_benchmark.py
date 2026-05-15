#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import random
import re
import shlex
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc


DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["tmux_pane0"]
DEFAULT_PROMPT = "__W3_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
DEFAULT_TMUX_SETUP_SCRIPT = "remote/w3_tmux_setup.sh"
DEFAULT_TMUX_SESSION = "w3bench5"
DEFAULT_TMUX_PANE = "0.0"
DEFAULT_TMUX_READY_MARKER = "__W3_5PANE_PANE0_READY__"
PROBE_CHAR_ALPHABET = "abcdegijkopvwxz0123456789"

_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z]|\x1b[\(\)][0-9A-Za-z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$")
_READY_CHECK_TAG = "__W3_TMUX_READY_CHECK__"


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    latency_ms: float


@dataclass
class FailureRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    error_type: str
    error_message: str


@dataclass
class SummaryRow:
    protocol: str
    workload: str
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


class W35PaneBenchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        if not args.probe_chars:
            raise ValueError("--probe-chars must contain at least one character")
        probe_chars = "".join(
            ch for ch in dict.fromkeys(args.probe_chars)
            if ch.isalnum() and ch.isprintable()
        )
        if not probe_chars:
            raise ValueError("--probe-chars must contain alphanumeric characters")
        if args.probe_search_window < 0:
            raise ValueError("--probe-search-window must be >= 0")
        if args.token_tail_len < 0:
            raise ValueError("--token-tail-len must be >= 0")
        self.probe_chars = probe_chars
        self.probe_search_window: Optional[int] = (
            None if args.probe_search_window == 0
            else max(8, args.probe_search_window)
        )
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.token_counter = 0

        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*$")

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _expect_prompt(
        self,
        child: pexpect.spawn,
        protocol: Optional[str] = None,
    ) -> None:
        try:
            child.expect(self.prompt_re, timeout=self.args.timeout)
            return
        except pexpect.TIMEOUT:
            if protocol != "mosh":
                raise

        last_exc: Optional[pexpect.TIMEOUT] = None
        for clear_screen in (False, True):
            self._drain_pending_output(child)
            if clear_screen:
                child.sendcontrol("l")
            child.sendline("")
            try:
                child.expect(self.prompt_re, timeout=self.args.timeout)
                return
            except pexpect.TIMEOUT as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc

    def _refresh_prompt(
        self,
        child: pexpect.spawn,
        protocol: Optional[str] = None,
    ) -> None:
        self._drain_pending_output(child)
        child.sendline("")
        self._expect_prompt(child, protocol=protocol)

    def _session_command(self, protocol: str) -> str:
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
        ssh_common += [self.target]

        if protocol == "ssh":
            return shlex.join(ssh_common)
        if protocol == "mosh":
            ssh_cmd = shlex.join(ssh_common[:-1])
            mosh_parts = ["mosh", f"--ssh={ssh_cmd}"]
            if self.args.mosh_predict and self.args.mosh_predict != "adaptive":
                mosh_parts += ["--predict", self.args.mosh_predict]
            mosh_parts += [self.target]
            return shlex.join(mosh_parts)
        if protocol == "ssh3":
            parts = ["ssh3"]
            if self.args.identity_file:
                parts += ["-privkey", self.args.identity_file]
            if self.args.ssh3_insecure:
                parts += ["-insecure"]
            parts.append(f"{self.target}{self.args.ssh3_path}")
            return shlex.join(parts)
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _open_session(self, protocol: str) -> tuple[pexpect.spawn, float]:
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
        )
        child.delaybeforesend = 0

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child, protocol=protocol)
        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF)
        except Exception:
            child.close(force=True)

    def _tmux_target(self) -> str:
        return f"{self.args.tmux_session}:{self.args.tmux_pane}"

    def _setup_tmux(self, child: pexpect.spawn, protocol: str) -> None:
        if self.args.tmux_no_setup:
            return
        cmd = (
            f"NO_ATTACH=1 bash {shlex.quote(self.args.tmux_setup_script)} "
            f"{shlex.quote(self.args.tmux_session)}"
        )
        child.sendline(cmd)
        self._expect_prompt(child, protocol=protocol)

    def _wait_tmux_ready(self, child: pexpect.spawn, protocol: str) -> None:
        deadline = time.monotonic() + self.args.tmux_ready_timeout
        target_q = shlex.quote(self._tmux_target())
        marker_q = shlex.quote(self.args.tmux_ready_marker)
        check_re = re.compile(re.escape(_READY_CHECK_TAG) + r"([01])")

        while True:
            cmd = (
                f"if tmux capture-pane -p -t {target_q} | tail -n 200 | "
                f"grep -F {marker_q} >/dev/null; "
                f"then echo {_READY_CHECK_TAG}1; else echo {_READY_CHECK_TAG}0; fi"
            )
            child.sendline(cmd)
            child.expect(check_re, timeout=self.args.timeout)
            ready = child.match.group(1) == "1"
            self._expect_prompt(child, protocol=protocol)
            if ready:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timeout waiting tmux ready marker: {self.args.tmux_ready_marker}"
                )
            time.sleep(self.args.tmux_ready_poll_interval)

    def _attach_tmux_pane(self, child: pexpect.spawn) -> None:
        target_q = shlex.quote(self._tmux_target())
        session_q = shlex.quote(self.args.tmux_session)
        child.sendline(f"tmux select-pane -t {target_q}; tmux attach -t {session_q}")
        # Give the attached pane a short moment to repaint.
        time.sleep(0.2)
        self._drain_pending_output(child)

    def _detach_tmux(self, child: pexpect.spawn, protocol: str) -> None:
        # tmux detach sequence: Prefix (Ctrl-b), then "d".
        child.sendcontrol("b")
        child.send("d")
        self._expect_prompt(child, protocol=protocol)

    def _make_probe_token(self) -> str:
        tail = "".join(
            random.choice(self.probe_chars) for _ in range(self.args.token_tail_len)
        )
        token = f"{self.args.token_prefix}{self.token_counter:04x}{tail}"
        self.token_counter += 1
        return token

    def _probe_token_once(
        self,
        child: pexpect.spawn,
        token: str,
    ) -> float:
        self._drain_pending_output(child)
        start_ns = time.perf_counter_ns()
        child.send(token)
        child.expect_exact(
            token,
            timeout=self.args.timeout,
            searchwindowsize=self.probe_search_window,
        )
        end_ns = time.perf_counter_ns()
        # Cleanup command line outside measured interval.
        child.sendcontrol("u")
        return (end_ns - start_ns) / 1_000_000.0

    def _recover_tmux_input_line(self, child: pexpect.spawn) -> None:
        child.sendcontrol("c")
        child.sendcontrol("u")

    def _reopen_and_reattach_tmux(
        self,
        child: pexpect.spawn,
        protocol: str,
    ) -> pexpect.spawn:
        self._close_session(child)
        reopened, _ = self._open_session(protocol)
        self._setup_tmux(reopened, protocol)
        self._wait_tmux_ready(reopened, protocol)
        self._attach_tmux_pane(reopened)
        return reopened

    def _measure_tmux_pane0(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
        fail_cb: Optional[Callable[[int, Exception], None]] = None,
    ) -> tuple[List[float], pexpect.spawn]:
        self._setup_tmux(child, protocol)
        self._wait_tmux_ready(child, protocol)
        self._attach_tmux_pane(child)

        try:
            for _ in range(warmup):
                token = self._make_probe_token()
                try:
                    self._probe_token_once(child, token)
                except pexpect.TIMEOUT:
                    if self.args.reopen_on_failure:
                        child = self._reopen_and_reattach_tmux(child, protocol)
                    else:
                        self._recover_tmux_input_line(child)

            latencies: List[float] = []
            for i in range(iterations):
                sample_id = i + 1
                token = self._make_probe_token()
                try:
                    lat = self._probe_token_once(child, token)
                except pexpect.TIMEOUT as exc:
                    if fail_cb:
                        fail_cb(sample_id, exc)
                    if self.args.reopen_on_failure:
                        child = self._reopen_and_reattach_tmux(child, protocol)
                    else:
                        self._recover_tmux_input_line(child)
                    continue
                latencies.append(lat)
                if report_cb:
                    report_cb(sample_id, lat)
        finally:
            self._detach_tmux(child, protocol=protocol)

        return latencies, child

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> tuple[List[float], pexpect.spawn]:
        def report_cb(sample_id: int, lat: float) -> None:
            self.results[protocol][workload].append(lat)
            self.records.append(
                SampleRecord(protocol, workload, trial_id, sample_id, lat)
            )
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {sample_id:>3}/{self.args.iterations}:"
                f" {lat:.2f} ms",
                flush=True,
            )

        def fail_cb(sample_id: int, exc: Exception) -> None:
            self.failures.append(
                FailureRecord(
                    protocol=protocol,
                    workload=workload,
                    round_id=trial_id,
                    sample_id=sample_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {sample_id:>3}/{self.args.iterations}:"
                f" FAIL ({type(exc).__name__})",
                flush=True,
            )

        latencies, child = self._measure_tmux_pane0(
            child=child,
            protocol=protocol,
            trial_id=trial_id,
            warmup=self.args.warmup_rounds,
            iterations=self.args.iterations,
            report_cb=report_cb,
            fail_cb=fail_cb,
        )
        return latencies, child

    def _run_session_group(self, protocol: str, workload: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][workload].append(setup_ms)
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms",
                    flush=True,
                )

                try:
                    _, child = self._run_trial(child, protocol, workload, trial_id)
                except (pexpect.TIMEOUT, pexpect.EOF, TimeoutError, ValueError) as exc:
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
                        f" ({type(exc).__name__}: {exc})",
                        flush=True,
                    )
            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
        pairs = [(p, w) for p in self.args.protocols for w in self.args.workloads]
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
        s = sorted(values)
        k = (len(s) - 1) * (p / 100.0)
        lower = math.floor(k)
        upper = math.ceil(k)
        if lower == upper:
            return s[int(k)]
        return s[lower] + (s[upper] - s[lower]) * (k - lower)

    def _summary_row(self, protocol: str, workload: str) -> SummaryRow:
        data = self.results[protocol][workload]
        expected_n = self.args.trials * self.args.iterations
        n = len(data)
        fail_n = max(0, expected_n - n)
        success_rate = (100.0 * n / expected_n) if expected_n else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None,
            )

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
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
            f" {'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'Max':>8}"
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

        line_csv = outdir / "w3_line_log.csv"
        setup_csv = outdir / "w3_session_setup.csv"

        with line_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "protocol",
                    "workload",
                    "round_id",
                    "sample_id",
                    "latency_ms",
                    "status",
                    "error_type",
                    "error_message",
                ]
            )
            for r in self.records:
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        f"{r.latency_ms:.6f}",
                        "ok",
                        "",
                        "",
                    ]
                )
            for r in self.failures:
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        "",
                        "fail",
                        r.error_type,
                        r.error_message,
                    ]
                )

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for w in self.args.workloads:
                    for trial_id, ms in enumerate(self.session_setups[p][w], start=1):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved line log CSV  : {line_csv}")
        print(f"Saved session setup : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="W3 5-pane benchmark (measure input-to-visible latency on tmux pane 0)."
    )
    parser.add_argument("--host", default="192.168.8.102")
    parser.add_argument("--user", default="trungnt")
    parser.add_argument("--source-ip", default="192.168.8.100")
    parser.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "id_rsa"),
    )
    parser.add_argument(
        "--protocols",
        nargs="+",
        default=DEFAULT_PROTOCOLS,
        choices=DEFAULT_PROTOCOLS,
    )
    parser.add_argument(
        "--workloads",
        nargs="+",
        default=DEFAULT_WORKLOADS,
        help="Logical labels for this benchmark. Default is tmux_pane0.",
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup-rounds", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="w3_results")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--probe-chars", default=PROBE_CHAR_ALPHABET)
    parser.add_argument("--probe-search-window", type=int, default=0)
    parser.add_argument("--token-prefix", default="ks")
    parser.add_argument("--token-tail-len", type=int, default=2)

    parser.add_argument("--tmux-setup-script", default=DEFAULT_TMUX_SETUP_SCRIPT)
    parser.add_argument("--tmux-session", default=DEFAULT_TMUX_SESSION)
    parser.add_argument("--tmux-pane", default=DEFAULT_TMUX_PANE)
    parser.add_argument("--tmux-ready-marker", default=DEFAULT_TMUX_READY_MARKER)
    parser.add_argument("--tmux-ready-timeout", type=float, default=60.0)
    parser.add_argument("--tmux-ready-poll-interval", type=float, default=0.5)
    parser.add_argument("--tmux-no-setup", action="store_true")

    parser.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH)
    parser.add_argument("--ssh3-insecure", action="store_true")
    parser.add_argument("--batch-mode", action="store_true")
    parser.add_argument("--strict-host-key-checking", action="store_true")
    parser.add_argument(
        "--mosh-predict",
        default="always",
        choices=["adaptive", "always", "never"],
    )
    parser.add_argument("--shuffle-pairs", action="store_true")
    parser.add_argument("--reopen-on-failure", action="store_true")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.warmup_rounds < 0:
        parser.error("--warmup-rounds must be >= 0")
    if args.probe_search_window < 0:
        parser.error("--probe-search-window must be >= 0")
    if args.token_tail_len < 0:
        parser.error("--token-tail-len must be >= 0")
    if args.tmux_ready_timeout <= 0:
        parser.error("--tmux-ready-timeout must be > 0")
    if args.tmux_ready_poll_interval <= 0:
        parser.error("--tmux-ready-poll-interval must be > 0")

    bench = W35PaneBenchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
