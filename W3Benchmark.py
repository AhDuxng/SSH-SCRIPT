#!/usr/bin/env python3
"""Research-oriented remote terminal benchmark.

This benchmark is designed for reproducible, protocol-comparison experiments
across SSHv2, SSHv3, and Mosh. It intentionally measures PTY-observed terminal
latency rather than human-perceived keyboard-to-screen latency.

Primary metrics
---------------
1. session_setup_ms
   Time from spawning the client process to obtaining a stable remote shell that
   accepts commands.

2. line_echo_ms
   Time from sending a line of input to observing the echoed line from a
   persistent remote helper process. This avoids command-line echo pollution and
   reduces prompt-dependent bias, which is especially important for Mosh.

Methodology notes
-----------------
- Warmup samples are excluded from summary statistics.
- Each trial opens a fresh session. One setup-latency sample is recorded per trial.
- Interactive samples are measured inside a persistent session-local helper.
- Results are exported as JSON and CSV for later statistical analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import random
import shlex
import statistics
import string
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit("Missing dependency: pexpect. Install with: pip install pexpect") from exc


DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_PROMPT = "__W3_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
DEFAULT_METRICS = ["session_setup", "line_echo"]


@dataclass
class SampleRecord:
    protocol: str
    metric: str
    trial_id: int
    sample_id: int
    is_warmup: bool
    token: str
    latency_ms: float


@dataclass
class FailureRecord:
    protocol: str
    metric: str
    trial_id: int
    sample_id: int
    is_warmup: bool
    error_type: str
    error_message: str


@dataclass
class SummaryRow:
    protocol: str
    metric: str
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


class SessionOpenError(RuntimeError):
    pass


class PreflightError(RuntimeError):
    pass


class Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.protocol_skip_reasons: Dict[str, str] = {}
        self.results: Dict[str, Dict[str, List[float]]] = {
            protocol: {metric: [] for metric in args.metrics}
            for protocol in args.protocols
        }

    def _token(self, protocol: str, metric: str, trial_id: int, sample_id: int) -> str:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return f"__W3TOK__{protocol}__{metric}__t{trial_id}__s{sample_id}__{rand}__"

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
            parts = ["mosh", f"--ssh={ssh_cmd}"]
            if self.args.mosh_predict != "adaptive":
                parts += ["--predict", self.args.mosh_predict]
            parts += [target]
            return shlex.join(parts)
        if protocol == "ssh3":
            parts = ["ssh3"]
            if self.args.identity_file:
                parts += ["-privkey", self.args.identity_file]
            if self.args.ssh3_insecure:
                parts += ["-insecure"]
            parts.append(f"{target}{self.args.ssh3_path}")
            return shlex.join(parts)

        raise ValueError(f"Unsupported protocol: {protocol}")

    def _spawn_child(self, protocol: str) -> pexpect.spawn:
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
        return child

    def _await_shell_ready(self, child: pexpect.spawn) -> None:
        deadline = time.monotonic() + self.args.timeout

        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            idx = child.expect(
                [
                    r"Are you sure you want to continue connecting \(yes/no(?:/\[fingerprint\])?\)\?",
                    r"Do you want to add this certificate to .*known_hosts \(yes/no\)\?",
                    r"\[Pp\]assword:",
                    r"Permission denied",
                    r"Connection refused",
                    r"No route to host",
                    r"Connection timed out",
                    r"Could not resolve hostname",
                    r"Cannot assign requested address",
                    r"Network is unreachable",
                    r"closed by remote host",
                    self.args.prompt,
                    r"[$#] ?",
                    r"\x1b\[[0-9;?]*[A-Za-z]",
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ],
                timeout=remaining,
            )

            if idx == 0:
                child.sendline("yes")
                continue
            if idx == 1:
                if self.args.ssh3_trust_on_first_use or self.args.ssh3_insecure:
                    child.sendline("yes")
                    continue
                raise SessionOpenError("SSH3 certificate prompt requires trust; rerun with --ssh3-insecure or --ssh3-trust-on-first-use")
            if idx == 2:
                raise SessionOpenError("Authentication fell back to password; key auth is not working")
            if idx == 3:
                raise SessionOpenError("Permission denied")
            if idx == 4:
                raise SessionOpenError("Connection refused")
            if idx == 5:
                raise SessionOpenError("No route to host")
            if idx == 6:
                raise SessionOpenError("Connection timed out")
            if idx == 7:
                raise SessionOpenError("Could not resolve hostname")
            if idx == 8:
                raise SessionOpenError("Cannot assign requested address for source IP")
            if idx == 9:
                raise SessionOpenError("Network is unreachable")
            if idx == 10:
                raise SessionOpenError("Connection closed by remote host")

            if idx in (11, 12, 13):
                child.sendline("printf '__W3_READY__\\n'")
                probe_idx = child.expect(
                    ["__W3_READY__", r"\[Pp\]assword:", "Permission denied", pexpect.EOF, pexpect.TIMEOUT],
                    timeout=max(1, min(5, remaining)),
                )
                if probe_idx == 0:
                    return
                if probe_idx == 1:
                    raise SessionOpenError("Authentication fell back to password during readiness probe")
                if probe_idx == 2:
                    raise SessionOpenError("Permission denied during readiness probe")
                if probe_idx == 3:
                    raise SessionOpenError(f"Session closed early (EOF). Output before EOF: {child.before!r}")
                raise SessionOpenError(f"Timeout while probing shell readiness. Output: {child.before!r}")

            if idx == 14:
                raise SessionOpenError(f"Session closed early (EOF). Output before EOF: {child.before!r}")
            if idx == 15:
                raise SessionOpenError(f"Timeout waiting for remote shell. Output: {child.before!r}")

        raise SessionOpenError(f"Timeout waiting for remote shell. Output: {child.before!r}")

    def _open_session(self, protocol: str) -> tuple[pexpect.spawn, float]:
        start_ns = time.perf_counter_ns()
        child = self._spawn_child(protocol)
        try:
            self._await_shell_ready(child)

            setup_marker = "__W3_PS1_OK__"
            setup_cmd = (
                "unset PROMPT_COMMAND >/dev/null 2>&1 || true; "
                "bind 'set enable-bracketed-paste off' >/dev/null 2>&1 || true; "
                f"export PS1={shlex.quote(self.args.prompt)}; "
                f"printf '{setup_marker}\\n'"
            )
            child.sendline(setup_cmd)
            child.expect_exact(setup_marker)
            child.expect_exact(self.args.prompt)

            child.sendline("stty -echoctl cols 200 rows 40 >/dev/null 2>&1 || true")
            child.expect_exact(self.args.prompt)

            end_ns = time.perf_counter_ns()
            return child, (end_ns - start_ns) / 1_000_000.0
        except Exception:
            self._safe_close(child)
            raise

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            if child.isalive():
                child.sendline("exit")
                child.expect(pexpect.EOF, timeout=min(5, self.args.timeout))
        except Exception:
            child.close(force=True)
        finally:
            try:
                if getattr(child, "logfile_read", None) is not None:
                    child.logfile_read.close()
            except Exception:
                pass

    def _safe_close(self, child: pexpect.spawn) -> None:
        try:
            self._close_session(child)
        except Exception:
            pass

    def _record_success(
        self,
        protocol: str,
        metric: str,
        trial_id: int,
        sample_id: int,
        is_warmup: bool,
        token: str,
        latency_ms: float,
    ) -> None:
        if is_warmup:
            return
        self.results[protocol][metric].append(latency_ms)
        self.records.append(SampleRecord(protocol, metric, trial_id, sample_id, is_warmup, token, latency_ms))

    def _record_failure(
        self,
        protocol: str,
        metric: str,
        trial_id: int,
        sample_id: int,
        is_warmup: bool,
        exc: Exception,
        child: Optional[pexpect.spawn] = None,
    ) -> None:
        extra = ""
        if child is not None:
            try:
                extra = f" | before={child.before!r}"
            except Exception:
                pass
        self.failures.append(
            FailureRecord(
                protocol=protocol,
                metric=metric,
                trial_id=trial_id,
                sample_id=sample_id,
                is_warmup=is_warmup,
                error_type=type(exc).__name__,
                error_message=f"{exc}{extra}",
            )
        )

    def _preflight_protocol(self, protocol: str) -> None:
        child, _ = self._open_session(protocol)
        try:
            child.sendline("command -v python3 >/dev/null 2>&1 && printf '__W3_HAS_PY3__\\n' || printf '__W3_NO_PY3__\\n'")
            idx = child.expect(["__W3_HAS_PY3__", "__W3_NO_PY3__"], timeout=self.args.timeout)
            child.expect_exact(self.args.prompt)
            if idx == 1:
                raise PreflightError("Remote host is missing python3, required for controlled line-echo helper")
        finally:
            self._close_session(child)

    def _start_line_helper(self, child: pexpect.spawn, protocol: str, trial_id: int) -> tuple[str, str]:
        ready_marker = f"__W3_HELPER_READY__{protocol}__t{trial_id}__"
        done_marker = f"__W3_HELPER_DONE__{protocol}__t{trial_id}__"
        exit_marker = f"__W3_HELPER_BYE__{protocol}__t{trial_id}__"

        helper_code = (
            "import os, sys; "
            "ready=os.environ['W3_READY']; "
            "done=os.environ['W3_DONE']; "
            "bye=os.environ['W3_BYE']; "
            "print(ready, flush=True); "
            "\nfor line in sys.stdin:" \
            "\n    line=line.rstrip('\\n')" \
            "\n    if line == '__W3_EXIT_HELPER__':" \
            "\n        print(bye, flush=True)" \
            "\n        break" \
            "\n    print(line, flush=True)" \
            "\n    print(done, flush=True)"
        )

        cmd = (
            f"W3_READY={shlex.quote(ready_marker)} "
            f"W3_DONE={shlex.quote(done_marker)} "
            f"W3_BYE={shlex.quote(exit_marker)} "
            f"python3 -u -c {shlex.quote(helper_code)}"
        )
        child.sendline(cmd)
        child.expect_exact(ready_marker)
        return done_marker, exit_marker

    def _stop_line_helper(self, child: pexpect.spawn, exit_marker: str) -> None:
        child.sendline("__W3_EXIT_HELPER__")
        child.expect_exact(exit_marker)
        child.sendline("printf '__W3_BACK__\\n'")
        child.expect_exact("__W3_BACK__")
        child.expect_exact(self.args.prompt)

    def _measure_line_echo(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        done_marker: str,
    ) -> tuple[str, float]:
        token = self._token(protocol, "line_echo", trial_id, sample_id)
        start_ns = time.perf_counter_ns()
        child.sendline(token)
        child.expect_exact(token)
        end_ns = time.perf_counter_ns()
        child.expect_exact(done_marker)
        if protocol == "mosh":
            time.sleep(self.args.mosh_settle_ms / 1000.0)
        return token, (end_ns - start_ns) / 1_000_000.0

    def _run_protocol(self, protocol: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            done_marker = None
            exit_marker = None
            try:
                child, setup_ms = self._open_session(protocol)
                self._record_success(
                    protocol=protocol,
                    metric="session_setup",
                    trial_id=trial_id,
                    sample_id=1,
                    is_warmup=False,
                    token="__W3_SETUP__",
                    latency_ms=setup_ms,
                )
                print(f"[{protocol:>4}/session_setup] trial {trial_id}/{self.args.trials}: OK ({setup_ms:.2f} ms)")

                if "line_echo" not in self.args.metrics:
                    continue

                done_marker, exit_marker = self._start_line_helper(child, protocol, trial_id)
                total = self.args.warmup_samples + self.args.samples_per_trial
                for i in range(1, total + 1):
                    is_warmup = i <= self.args.warmup_samples
                    sample_id = i if is_warmup else (i - self.args.warmup_samples)
                    try:
                        token, latency_ms = self._measure_line_echo(child, protocol, trial_id, sample_id, done_marker)
                        self._record_success(protocol, "line_echo", trial_id, sample_id, is_warmup, token, latency_ms)
                        tag = "warmup" if is_warmup else "measure"
                        limit = self.args.warmup_samples if is_warmup else self.args.samples_per_trial
                        print(f"[{protocol:>4}/line_echo   ] {tag} {sample_id}/{limit}: OK ({latency_ms:.2f} ms)")
                    except Exception as exc:
                        self._record_failure(protocol, "line_echo", trial_id, sample_id, is_warmup, exc, child)
                        print(f"[{protocol:>4}/line_echo   ] {'warmup' if is_warmup else 'measure'} {sample_id}: FAIL ({type(exc).__name__}: {exc})")
                        if not self.args.reopen_on_failure:
                            raise
                        break
            except Exception as exc:
                if child is None:
                    self._record_failure(protocol, "session_setup", trial_id, 1, False, exc, None)
                    print(f"[{protocol:>4}/session_setup] trial {trial_id}: FAIL ({type(exc).__name__}: {exc})")
                else:
                    # setup was successful, but later stage failed and was already logged.
                    if done_marker is None:
                        self._record_failure(protocol, "line_echo", trial_id, 0, False, exc, child)
                        print(f"[{protocol:>4}/line_echo   ] trial {trial_id}: FAIL ({type(exc).__name__}: {exc})")
            finally:
                if child is not None:
                    try:
                        if exit_marker is not None:
                            self._stop_line_helper(child, exit_marker)
                    except Exception:
                        pass
                    self._safe_close(child)

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

    def _summary_row(self, protocol: str, metric: str) -> SummaryRow:
        data = self.results[protocol][metric]
        fail_n = sum(1 for f in self.failures if f.protocol == protocol and f.metric == metric and not f.is_warmup)
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(protocol, metric, 0, fail_n, success_rate, None, None, None, None, None, None, None, None)

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol,
            metric=metric,
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
        return [self._summary_row(p, m) for p in self.args.protocols for m in self.args.metrics]

    def print_report(self) -> None:
        width = 146
        print("\n" + "=" * width)
        print(
            f"{'Protocol':<8} | {'Metric':<14} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+/-':>9}"
        )
        print("-" * width)

        def fmt(v: Optional[float]) -> str:
            return f"{v:.2f}" if v is not None else "N/A"

        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.metric:<14} | {row.n:>4} | {row.failures:>4} | {row.success_rate_pct:>8.1f} | "
                f"{fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | {fmt(row.stdev_ms):>8} | "
                f"{fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} | {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
            )
        print("=" * width)

        if self.protocol_skip_reasons:
            print("\nSkipped protocols:")
            for protocol, reason in self.protocol_skip_reasons.items():
                print(f"- {protocol}: {reason}")

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        summary_json = outdir / "research_summary.json"
        raw_csv = outdir / "research_samples.csv"
        failures_csv = outdir / "research_failures.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "metrics": self.args.metrics,
                "trials": self.args.trials,
                "samples_per_trial": self.args.samples_per_trial,
                "warmup_samples": self.args.warmup_samples,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "topology": {
                    "client": self.args.source_ip or "default-route",
                    "server": self.args.host,
                },
                "system": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "hostname": platform.node(),
                },
                "metric_note": (
                    "session_setup_ms = client-spawn to stable shell; "
                    "line_echo_ms = PTY-observed line echo in a persistent remote helper. "
                    "These are application-level terminal timings, not human-perceived keyboard-to-screen latency."
                ),
                "skipped_protocols": self.protocol_skip_reasons,
            },
            "summary": [asdict(row) for row in self.summaries()],
        }
        summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with raw_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "metric", "trial_id", "sample_id", "is_warmup", "token", "latency_ms"])
            for r in self.records:
                writer.writerow([r.protocol, r.metric, r.trial_id, r.sample_id, r.is_warmup, r.token, f"{r.latency_ms:.6f}"])

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "metric", "trial_id", "sample_id", "is_warmup", "error_type", "error_message"])
            for r in self.failures:
                writer.writerow([r.protocol, r.metric, r.trial_id, r.sample_id, r.is_warmup, r.error_type, r.error_message])

        print(f"Saved summary JSON: {summary_json}")
        print(f"Saved raw samples CSV: {raw_csv}")
        print(f"Saved failures CSV: {failures_csv}")

    def run(self) -> None:
        random.seed(self.args.seed)

        runnable_protocols = list(self.args.protocols)
        if self.args.shuffle_protocols:
            random.shuffle(runnable_protocols)

        if self.args.preflight:
            approved = []
            for protocol in runnable_protocols:
                print(f"[preflight/{protocol}] checking session bootstrap and python3 availability...")
                try:
                    self._preflight_protocol(protocol)
                    print(f"[preflight/{protocol}] OK")
                    approved.append(protocol)
                except Exception as exc:
                    self.protocol_skip_reasons[protocol] = str(exc)
                    print(f"[preflight/{protocol}] FAIL ({type(exc).__name__}: {exc})")
            runnable_protocols = approved

        for protocol in runnable_protocols:
            self._run_protocol(protocol)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Research-oriented terminal benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default=None, help="Client source IP for SSH/Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--metrics", nargs="+", default=DEFAULT_METRICS, choices=DEFAULT_METRICS)
    p.add_argument("--trials", type=int, default=10, help="Independent session trials per protocol")
    p.add_argument("--samples-per-trial", type=int, default=50, help="Measured line-echo samples per trial")
    p.add_argument("--warmup-samples", type=int, default=5, help="Warmup line-echo samples per trial (excluded from summaries)")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="research_results", help="Directory for JSON/CSV outputs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Temporary shell prompt marker")
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH, help="SSH3 terminal path suffix")
    p.add_argument("--ssh3-insecure", action="store_true", help="Use -insecure for ssh3")
    p.add_argument("--ssh3-trust-on-first-use", action="store_true", help="Automatically answer yes to SSH3 certificate trust prompts")
    p.add_argument("--batch-mode", action="store_true", help="Enable BatchMode for SSHv2/Mosh bootstrap SSH")
    p.add_argument("--strict-host-key-checking", action="store_true", help="Keep strict host key checking enabled")
    p.add_argument("--mosh-predict", default="never", choices=["adaptive", "always", "never"], help="Mosh prediction mode; 'never' is recommended for fairer protocol comparison")
    p.add_argument("--mosh-settle-ms", type=float, default=50.0, help="Small post-sample settle delay for Mosh full-screen updates")
    p.add_argument("--preflight", action="store_true", help="Run bootstrap and python3 availability check before measurements")
    p.add_argument("--log-pexpect", action="store_true", help="Save raw pexpect output per protocol")
    p.add_argument("--shuffle-protocols", action="store_true", help="Randomize protocol order")
    p.add_argument("--reopen-on-failure", action="store_true", help="Continue next trial after a sample failure by reopening a fresh session")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.samples_per_trial <= 0:
        parser.error("--samples-per-trial must be > 0")
    if args.warmup_samples < 0:
        parser.error("--warmup-samples must be >= 0")

    bench = Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
