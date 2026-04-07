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
   Time from sending a line of input to receiving a unique server-side
   acknowledgement marker from the remote helper process. This measures the true
   application-level round-trip time (RTT): the token must travel to the server,
   be processed by the helper, and the acknowledgement marker must return to
   the client.

   IMPORTANT: We do NOT stop timing at the PTY echo of the token itself, because
   PTY echo can be handled locally (especially in Mosh with local prediction).
   Stopping at the server-side acknowledgement guarantees the server actually
   received and processed the line — making the measurement protocol-fair.

Methodology notes
-----------------
- Warmup samples are excluded from summary statistics.
- Each trial opens a fresh session. One setup-latency sample is recorded per trial.
- Interactive samples are measured inside a persistent session-local helper.
- Results are exported as JSON and CSV for later statistical analysis.
- Network baseline RTT (ping) is recorded per trial as a covariate.
- Remote system metadata (kernel, protocol version) is captured once at preflight.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import random
import re
import shlex
import statistics
import string
import subprocess
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

TERMINAL_NOISE_GAP = (
    r"(?:"
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
    r"|[\r\n\x00\x08]"
    r")*"
)
ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_FE_RE = re.compile(r"\x1b[@-Z\\-_]")
CONTROL_CHAR_RE = re.compile(r"[\r\x00\x08]")


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


@dataclass
class RemoteMeta:
    """System metadata collected from the remote host at preflight time."""
    kernel: str = "unknown"
    mosh_version: str = "unknown"
    ssh_version: str = "unknown"
    ssh3_version: str = "unknown"
    python_version: str = "unknown"


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
        self.ping_rtts: Dict[str, List[Optional[float]]] = {p: [] for p in args.protocols}
        self.remote_meta = RemoteMeta()
        self.results: Dict[str, Dict[str, List[float]]] = {
            protocol: {metric: [] for metric in DEFAULT_METRICS}
            for protocol in args.protocols
        }
        self._literal_pattern_cache: Dict[str, re.Pattern[str]] = {}

    def _literal_pattern(self, literal: str) -> re.Pattern[str]:
        pattern = self._literal_pattern_cache.get(literal)
        if pattern is None:
            pattern = re.compile(
                "".join(f"{re.escape(ch)}{TERMINAL_NOISE_GAP}" for ch in literal),
                re.DOTALL,
            )
            self._literal_pattern_cache[literal] = pattern
        return pattern

    def _expect_literal(self, child: pexpect.spawn, literal: str, timeout: Optional[float] = None) -> int:
        return child.expect(self._literal_pattern(literal), timeout=timeout)

    @staticmethod
    def _strip_terminal_noise(text: str) -> str:
        text = ANSI_CSI_RE.sub("", text)
        text = ANSI_FE_RE.sub("", text)
        text = CONTROL_CHAR_RE.sub("", text)
        return text

    def _debug_buffer(self, child: pexpect.spawn, limit: int = 400) -> str:
        raw = getattr(child, "before", "") or ""
        clean = self._strip_terminal_noise(raw)
        if len(raw) > limit:
            raw = raw[-limit:]
        if len(clean) > limit:
            clean = clean[-limit:]
        return f"raw_tail={raw!r} | clean_tail={clean!r}"

    def _token(self, protocol: str, metric: str, trial_id: int, sample_id: int) -> str:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return f"__W3TOK__{protocol}__{metric}__t{trial_id}__s{sample_id}__{rand}__"

    def _measure_ping_rtt(self) -> Optional[float]:
        """Send a single ICMP ping to the target host and return RTT in ms, or None on failure."""
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", self.args.host],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "time=" in line:
                    for part in line.split():
                        if part.startswith("time="):
                            return float(part.split("=")[1])
        except Exception:
            pass
        return None

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
            maxread=65535,
        )
        try:
            child.setwinsize(self.args.pty_rows, self.args.pty_cols)
        except Exception:
            pass
        if self.args.log_pexpect:
            log_path = Path(self.args.output_dir) / f"pexpect_{protocol}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child.logfile_read = open(log_path, "a", encoding="utf-8")
        return child

    def _await_shell_ready(self, child: pexpect.spawn) -> None:
        deadline = time.monotonic() + self.args.timeout

        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
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
                    self._literal_pattern(self.args.prompt),
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
                    [
                        self._literal_pattern("__W3_READY__"),
                        r"\[Pp\]assword:",
                        "Permission denied",
                        pexpect.EOF,
                        pexpect.TIMEOUT,
                    ],
                    timeout=max(1.0, min(5.0, deadline - time.monotonic())),
                )
                if probe_idx == 0:
                    return
                if probe_idx == 1:
                    raise SessionOpenError("Authentication fell back to password during readiness probe")
                if probe_idx == 2:
                    raise SessionOpenError("Permission denied during readiness probe")
                if probe_idx == 3:
                    raise SessionOpenError(f"Session closed early (EOF). {self._debug_buffer(child)}")
                raise SessionOpenError(f"Timeout while probing shell readiness. {self._debug_buffer(child)}")

            if idx == 14:
                raise SessionOpenError(f"Session closed early (EOF). {self._debug_buffer(child)}")
            if idx == 15:
                raise SessionOpenError(f"Timeout waiting for remote shell. {self._debug_buffer(child)}")

        raise SessionOpenError(f"Timeout waiting for remote shell. {self._debug_buffer(child)}")

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
            self._expect_literal(child, setup_marker, timeout=self.args.timeout)
            self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)

            child.sendline(
                f"stty -echo -echoctl cols {self.args.pty_cols} rows {self.args.pty_rows} >/dev/null 2>&1 || true"
            )
            self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)

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
                extra = f" | {self._debug_buffer(child)}"
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

    def _collect_remote_meta(self, child: pexpect.spawn) -> None:
        """Query remote system info and populate self.remote_meta."""

        def _run(cmd: str, default: str = "unknown") -> str:
            child.sendline(cmd)
            self._expect_literal(child, self.args.prompt, timeout=10)
            clean = self._strip_terminal_noise(child.before or "")
            lines = [line.strip() for line in clean.splitlines() if line.strip()]
            filtered = [line for line in lines if line != cmd]
            return filtered[-1] if filtered else default

        self.remote_meta.kernel = _run("uname -r")
        self.remote_meta.mosh_version = _run("mosh --version 2>&1 | head -1")
        self.remote_meta.ssh_version = _run("ssh -V 2>&1 | head -1")
        self.remote_meta.ssh3_version = _run(
            "command -v ssh3 >/dev/null 2>&1 && ssh3 -version 2>&1 | head -1 || printf 'unknown\\n'"
        )
        self.remote_meta.python_version = _run("python3 --version 2>&1")

    def _preflight_protocol(self, protocol: str) -> None:
        child, _ = self._open_session(protocol)
        try:
            child.sendline("command -v python3 >/dev/null 2>&1 && printf '__W3_HAS_PY3__\\n' || printf '__W3_NO_PY3__\\n'")
            idx = child.expect(
                [self._literal_pattern("__W3_HAS_PY3__"), self._literal_pattern("__W3_NO_PY3__")],
                timeout=self.args.timeout,
            )
            self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)
            if idx == 1:
                raise PreflightError("Remote host is missing python3, required for controlled line-echo helper")

            if self.remote_meta.kernel == "unknown":
                self._collect_remote_meta(child)
        finally:
            self._close_session(child)

    def _start_line_helper(self, child: pexpect.spawn, protocol: str, trial_id: int) -> tuple[str, str]:
        ready_marker = f"__W3_HELPER_READY__{protocol}__t{trial_id}__"
        ack_prefix = f"__W3_HELPER_ACK__{protocol}__t{trial_id}__"
        exit_marker = f"__W3_HELPER_BYE__{protocol}__t{trial_id}__"

        helper_code = (
            "import os, sys; "
            "ready=os.environ['W3_READY']; "
            "ack=os.environ['W3_ACK']; "
            "bye=os.environ['W3_BYE']; "
            "print(ready, flush=True); "
            "\nfor line in sys.stdin:"
            "\n    line=line.rstrip('\\n')"
            "\n    if line == '__W3_EXIT_HELPER__':"
            "\n        print(bye, flush=True)"
            "\n        break"
            "\n    print(f'{ack}{line}', flush=True)"
        )

        cmd = (
            f"W3_READY={shlex.quote(ready_marker)} "
            f"W3_ACK={shlex.quote(ack_prefix)} "
            f"W3_BYE={shlex.quote(exit_marker)} "
            f"python3 -u -c {shlex.quote(helper_code)}"
        )
        child.sendline(cmd)
        self._expect_literal(child, ready_marker, timeout=self.args.timeout)
        return ack_prefix, exit_marker

    def _stop_line_helper(self, child: pexpect.spawn, exit_marker: str) -> None:
        child.sendline("__W3_EXIT_HELPER__")
        self._expect_literal(child, exit_marker, timeout=self.args.timeout)
        child.sendline("printf '__W3_BACK__\\n'")
        self._expect_literal(child, "__W3_BACK__", timeout=self.args.timeout)
        self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)

    def _measure_line_echo(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        ack_prefix: str,
    ) -> tuple[str, float]:
        """Measure true application-level round-trip time.

        Timing starts immediately before sendline() and ends when a unique
        server-side acknowledgement marker is observed. The remote helper emits
        ACK_PREFIX + TOKEN only after it has actually read and processed the
        line, so the measurement spans:

            client sendline → network → server receives → helper processes
            → server sends ack_prefix + token → network → client observes ack

        This is NOT stopped at any PTY echo of the token, which would capture
        only local PTY processing and is meaningless as a protocol comparison
        metric (especially for Mosh, which uses local echo prediction).
        """
        token = self._token(protocol, "line_echo", trial_id, sample_id)
        ack_marker = f"{ack_prefix}{token}"
        start_ns = time.perf_counter_ns()
        child.sendline(token)
        self._expect_literal(child, ack_marker, timeout=self.args.timeout)
        end_ns = time.perf_counter_ns()
        return token, (end_ns - start_ns) / 1_000_000.0

    def _run_protocol(self, protocol: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            # Measure network baseline RTT before each trial
            ping_rtt = self._measure_ping_rtt()
            self.ping_rtts[protocol].append(ping_rtt)
            ping_str = f"{ping_rtt:.2f} ms" if ping_rtt is not None else "N/A"
            print(f"[{protocol:>4}] trial {trial_id}/{self.args.trials} — ping baseline: {ping_str}")

            child: Optional[pexpect.spawn] = None
            ack_prefix = None
            exit_marker = None
            setup_completed = False
            try:
                child, setup_ms = self._open_session(protocol)
                setup_completed = True
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

                ack_prefix, exit_marker = self._start_line_helper(child, protocol, trial_id)
                total = self.args.warmup_samples + self.args.samples_per_trial
                for i in range(1, total + 1):
                    is_warmup = i <= self.args.warmup_samples
                    sample_id = i if is_warmup else (i - self.args.warmup_samples)
                    try:
                        token, latency_ms = self._measure_line_echo(child, protocol, trial_id, sample_id, ack_prefix)
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
                if not setup_completed:
                    self._record_failure(protocol, "session_setup", trial_id, 1, False, exc, child)
                    print(f"[{protocol:>4}/session_setup] trial {trial_id}: FAIL ({type(exc).__name__}: {exc})")
                else:
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
        ping_csv = outdir / "research_ping_rtts.csv"

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
                "pty_cols": self.args.pty_cols,
                "pty_rows": self.args.pty_rows,
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
                "remote_system": {
                    "kernel": self.remote_meta.kernel,
                    "mosh_version": self.remote_meta.mosh_version,
                    "ssh_version": self.remote_meta.ssh_version,
                    "ssh3_version": self.remote_meta.ssh3_version,
                    "python_version": self.remote_meta.python_version,
                },
                "metric_note": (
                    "session_setup_ms = client-spawn to stable shell (includes TCP+crypto handshake + shell init); "
                    "line_echo_ms = true application-level RTT measured from sendline() to a unique server-side ack marker "
                    "(ACK_PREFIX + token emitted by the remote helper after it reads the line; NOT stopped at local PTY echo "
                    "— ensures Mosh local prediction does not artificially deflate results). These are application-level "
                    "terminal timings, not human-perceived keyboard-to-screen latency."
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

        with ping_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "trial_id", "ping_rtt_ms"])
            for proto, rtts in self.ping_rtts.items():
                for i, rtt in enumerate(rtts, start=1):
                    writer.writerow([proto, i, f"{rtt:.3f}" if rtt is not None else ""])

        print(f"Saved summary JSON:      {summary_json}")
        print(f"Saved raw samples CSV:   {raw_csv}")
        print(f"Saved failures CSV:      {failures_csv}")
        print(f"Saved ping RTT CSV:      {ping_csv}")

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
    p.add_argument("--trials", type=int, default=30,
                   help="Independent session trials per protocol (recommended >= 30 for reliable CI)")
    p.add_argument("--samples-per-trial", type=int, default=50, help="Measured line-echo samples per trial")
    p.add_argument("--warmup-samples", type=int, default=5, help="Warmup line-echo samples per trial (excluded from summaries)")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--pty-cols", type=int, default=200, help="Client PTY width passed to the spawned terminal")
    p.add_argument("--pty-rows", type=int, default=40, help="Client PTY height passed to the spawned terminal")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="research_results", help="Directory for JSON/CSV outputs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Temporary shell prompt marker")
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH, help="SSH3 terminal path suffix")
    p.add_argument("--ssh3-insecure", action="store_true", help="Use -insecure for ssh3")
    p.add_argument("--ssh3-trust-on-first-use", action="store_true", help="Automatically answer yes to SSH3 certificate trust prompts")
    p.add_argument("--batch-mode", action="store_true", help="Enable BatchMode for SSHv2/Mosh bootstrap SSH")
    p.add_argument("--strict-host-key-checking", action="store_true", help="Keep strict host key checking enabled")
    p.add_argument("--mosh-predict", default="never", choices=["adaptive", "always", "never"],
                   help="Mosh prediction mode; 'never' is recommended for protocol comparison (disables local echo prediction)")
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
    if args.pty_cols <= 0:
        parser.error("--pty-cols must be > 0")
    if args.pty_rows <= 0:
        parser.error("--pty-rows must be > 0")

    bench = Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())