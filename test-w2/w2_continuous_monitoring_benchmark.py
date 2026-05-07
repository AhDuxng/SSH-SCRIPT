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
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["top", "tail", "ping"]
DEFAULT_PROMPT = "__W2_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"

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

class W2Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)

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
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        child.expect(self.prompt_re, timeout=self.args.timeout)

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

    def _open_session(self, protocol: str, max_retries: int = 3) -> tuple[pexpect.spawn, float]:
        last_exc: Exception | None = None
        base_timeout = self.args.timeout
        if protocol == "mosh":
            base_timeout = max(base_timeout, 30)

        for attempt in range(1, max_retries + 1):
            child: pexpect.spawn | None = None
            try:
                child = pexpect.spawn(
                    self._session_command(protocol),
                    encoding="utf-8",
                    codec_errors="ignore",
                    timeout=base_timeout,
                )

                if self.args.log_pexpect:
                    log_path = Path(self.args.output_dir) / f"pexpect_{protocol}.log"
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    child.logfile_read = open(log_path, "a", encoding="utf-8")

                connect_timeout = base_timeout * attempt

                start_ns = time.perf_counter_ns()
                child.expect(_INITIAL_PROMPT_RE, timeout=connect_timeout)
                setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

                child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
                self._expect_prompt(child)
                return child, setup_ms

            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
                last_exc = exc
                print(
                    f"  [WARN] _open_session({protocol}) attempt {attempt}/{max_retries} failed: {type(exc).__name__}",
                    flush=True,
                )
                if child is not None:
                    self._close_session(child, protocol)
                time.sleep(2 * attempt)

        raise last_exc  

    def _close_session(self, child: pexpect.spawn, protocol: str = "") -> None:
        """Close the pexpect session, forcefully killing any running processes first."""
        try:
            for _ in range(3):
                child.sendcontrol("c")
                time.sleep(0.3)

            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=5)
        except Exception:
            pass
        finally:
            try:
                child.close(force=True)
            except Exception:
                pass
            try:
                if getattr(child, "logfile_read", None) is not None:
                    child.logfile_read.close()
            except Exception:
                pass

        if protocol == "mosh" and hasattr(child, "pid") and child.pid:
            try:
                import os, signal
                os.kill(child.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    def _measure_top(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        interval = max(0.1, float(self.args.top_interval))
        refreshes_per_sample = max(1, int(self.args.top_refreshes_per_sample))
        warmup = 3
        total_frames = (iterations * refreshes_per_sample) + warmup

        marker = f"W2_TOP_{random.randrange(1_000_000_000):09d}"
        frame_re = re.compile(rf"{re.escape(marker)}_(\d{{6}})")
        expect_timeout = max(
            self.args.timeout,
            int(interval * refreshes_per_sample * 5 + 10),
        )

        awk_script = (
            "BEGIN { frame=0; line=0 } "
            f'/^top -/ {{ frame++; line=0; printf("{marker}_%06d\\n", frame); fflush(); }} '
            "frame > 0 && line < 20 { print; fflush(); line++; }"
        )
        TOP_CMD = (
            f"COLUMNS=120 LINES=40 "
            f"top -b -d {shlex.quote(str(interval))} -n {total_frames} 2>/dev/null "
            f"| awk {shlex.quote(awk_script)}"
        )
        child.sendline(TOP_CMD)

        for _ in range(warmup):
            child.expect(frame_re, timeout=expect_timeout)

        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            for _ in range(refreshes_per_sample):
                child.expect(frame_re, timeout=expect_timeout)
            end_ns = time.perf_counter_ns()
            lat = (end_ns - start_ns) / 1_000_000.0
            report_cb(i + 1, lat)

        self._expect_prompt(child)

    def _measure_tail(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        remote_log = "/tmp/w2_test.log"
        child.sendline(f"rm -f {remote_log} && touch {remote_log}")
        self._expect_prompt(child)

        child.sendline(
            f"W2_SEQ=0; while true; do "
            f"W2_SEQ=$((W2_SEQ+1)); "
            f"echo \"W2_TAIL_$W2_SEQ\" >> {remote_log}; "
            f"sleep 0.05; "
            f"done &"
        )
        self._expect_prompt(child)

        child.sendline("W2_WRITER_PID=$!; echo W2_WRITER_PID=$W2_WRITER_PID")
        child.expect(r"W2_WRITER_PID=(\d+)", timeout=self.args.timeout)
        bg_pid = child.match.group(1)
        self._expect_prompt(child)

        child.sendline(f"tail -f {remote_log}")

        for _ in range(10):
            child.expect(r"W2_TAIL_\d+", timeout=self.args.timeout)

        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            child.expect(r"W2_TAIL_\d+", timeout=self.args.timeout)
            end_ns = time.perf_counter_ns()
            lat = (end_ns - start_ns) / 1_000_000.0
            report_cb(i + 1, lat)

        child.sendcontrol("c")
        self._expect_prompt(child)

        child.sendline(f"kill {bg_pid} 2>/dev/null; rm -f {remote_log}")
        self._expect_prompt(child)

    def _measure_ping(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        ping_target = self.args.ping_target or "127.0.0.1"
        child.sendline(f"ping -i 0.1 {ping_target}")
        child.expect(r"PING ", timeout=self.args.timeout)

        for _ in range(5):
            child.expect(r"bytes from", timeout=self.args.timeout)

        for i in range(iterations):
            start_ns = time.perf_counter_ns()
            child.expect(r"bytes from", timeout=self.args.timeout)
            end_ns = time.perf_counter_ns()
            lat = (end_ns - start_ns) / 1_000_000.0
            report_cb(i + 1, lat)

        child.sendcontrol("c")
        self._expect_prompt(child)

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> None:
        def report_cb(s_idx: int, lat: float) -> None:
            self.results[protocol][workload].append(lat)
            self.records.append(
                SampleRecord(protocol, workload, trial_id, s_idx, lat)
            )
            print(
                f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}/{self.args.trials}"
                f" measure {s_idx:>3}/{self.args.iterations}: {lat:.2f} ms",
                flush=True,
            )

        if workload == "top":
            self._measure_top(child, self.args.iterations, report_cb)
        elif workload == "tail":
            self._measure_tail(child, self.args.iterations, report_cb)
        elif workload == "ping":
            self._measure_ping(child, self.args.iterations, report_cb)
        else:
            raise ValueError(f"Unsupported workload: {workload}")

    def _run_session_group(
        self,
        protocol: str,
        workload: str,
    ) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][workload].append(setup_ms)
                print(
                    f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms",
                    flush=True,
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
                            error_message=str(exc)[:500],
                        )
                    )
                    print(
                        f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}: FAIL ({type(exc).__name__})",
                        flush=True,
                    )
                    if self.args.reopen_on_failure:
                        # Force-close the broken session before reopening
                        if child is not None:
                            self._close_session(child, protocol)
                            child = None
                        # Cooldown: let the network / remote side settle
                        time.sleep(3)
                        continue
            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
                # _open_session itself failed after all retries
                self.failures.append(
                    FailureRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=-1,
                        error_type=type(exc).__name__,
                        error_message=f"session_open_failed: {str(exc)[:300]}",
                    )
                )
                print(
                    f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}: FAIL (session open failed: {type(exc).__name__})",
                    flush=True,
                )
                time.sleep(3)
                continue
            finally:
                if child is not None:
                    self._close_session(child, protocol)
                # Brief pause between trials for Mosh to release UDP ports
                if protocol == "mosh":
                    time.sleep(2)

    def run(self) -> None:
        random.seed(self.args.seed)
        sequence = [
            (p, w)
            for p in self.args.protocols
            for w in self.args.workloads
        ]
        if self.args.shuffle_pairs:
            random.shuffle(sequence)
        for protocol, workload in sequence:
            self._run_session_group(protocol, workload)

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

    def _summary_row(self, protocol: str, workload: str) -> SummaryRow:
        data = self.results[protocol][workload]
        fail_n = sum(
            1
            for f in self.failures
            if f.protocol == protocol and f.workload == workload
        )
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(protocol, workload, 0, fail_n, success_rate, None, None, None, None, None, None, None, None)

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
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+/-':>9}"
        )
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<18} | {row.n:>4} | {row.failures:>4} | "
                f"{row.success_rate_pct:>8.1f} | {fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | "
                f"{fmt(row.stdev_ms):>8} | {fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} | {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
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

        summary_json = outdir / "w2_summary.json"
        raw_csv = outdir / "w2_raw_samples.csv"
        failures_csv = outdir / "w2_failures.csv"
        setup_csv = outdir / "w2_session_setup.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "workloads": self.args.workloads,
                "trials": self.args.trials,
                "iterations": self.args.iterations,
                "top_refreshes_per_sample": self.args.top_refreshes_per_sample,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "topology": {
                    "client": self.args.source_ip or "unknown",
                    "server": self.args.host,
                },
                "metric_name": "screen_update_latency_ms",
                "metric_note": (
                    f"All workloads measure inter-arrival time of streaming output (screen update latency). "
                    f"top: real continuous 'top -b -d {self.args.top_interval}' with a unique marker per frame "
                    f"Each recorded top sample groups {self.args.top_refreshes_per_sample} consecutive refreshes "
                    f"(expected grouped latency ~ {self.args.top_interval * self.args.top_refreshes_per_sample * 1000:.0f} ms + network/protocol jitter). "
                    f"tail: background writer at 50 ms intervals with tail -f "
                    f"(expected inter-arrival ~ 50 ms + network delay). "
                    f"ping: 'ping -i 0.1 {self.args.ping_target or '127.0.0.1'}' "
                    f"(expected inter-arrival ~ 100 ms + network delay). "
                    f"Deviation from expected interval reflects protocol overhead + network-induced screen update delay."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {w: self._session_setup_stats(p, w) for w in self.args.workloads}
                for p in self.args.protocols
            },
        }
        summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with raw_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "latency_ms"])
            for r in self.records:
                writer.writerow([r.protocol, r.workload, r.round_id, r.sample_id, f"{r.latency_ms:.6f}"])

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "error_type", "error_message"])
            for r in self.failures:
                writer.writerow([
                    r.protocol,
                    r.workload,
                    r.round_id,
                    r.sample_id,
                    r.error_type,
                    r.error_message,
                ])

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for w in self.args.workloads:
                    for trial_id, ms in enumerate(self.session_setups[p][w], start=1):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved summary JSON    : {summary_json}")
        print(f"Saved raw samples CSV : {raw_csv}")
        print(f"Saved failures CSV    : {failures_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W2 Continuous Monitoring benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default="192.168.8.100", help="Client source IP for SSH / Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=["top", "tail", "ping"])
    p.add_argument("--ping-target", default="", help="IP to ping inside the remote session (default: 127.0.0.1 loopback)")
    p.add_argument("--top-interval", type=float, default=1.0, help="Interval in seconds between top refreshes (default: 1.0)")
    p.add_argument(
        "--top-refreshes-per-sample",
        type=int,
        default=5,
        help="Number of consecutive top refreshes grouped into one latency sample (default: 5)",
    )
    p.add_argument("--trials", type=int, default=15, help="Independent sessions per protocol/workload pair")
    p.add_argument("--iterations", type=int, default=100, help="Recorded samples per trial")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="w2_results", help="Directory for JSON/CSV outputs")
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
    if args.top_refreshes_per_sample <= 0:
        parser.error("--top-refreshes-per-sample must be > 0")

    bench = W2Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())

