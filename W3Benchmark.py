#!/usr/bin/env python3
"""W3 Interactive Editing benchmark.

This script measures interactive response latency for remote terminal sessions
across SSHv2, SSHv3, and Mosh.

Topology defaults:
- Client / AI agent: 192.168.8.100
- Target host: 192.168.8.102

Important:
This script measures *interactive response latency* observed through pexpect,
not physical keyboard-to-screen keystroke latency.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import shlex
import statistics
import string
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit("Missing dependency: pexpect. Install with: pip install pexpect") from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["interactive_shell", "vim", "nano"]
DEFAULT_PROMPT = "__W3_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    token: str
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


class SessionOpenError(RuntimeError):
    pass


class WorkloadPrecheckError(RuntimeError):
    pass


class W3Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            protocol: {workload: [] for workload in args.workloads}
            for protocol in args.protocols
        }

    def _token(self, protocol: str, workload: str, round_id: int, sample_id: int) -> str:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return f"__W3TOK__{protocol}__{workload}__r{round_id}__s{sample_id}__{rand}__"

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
            ssh3_target = f"{target}{self.args.ssh3_path}"
            parts.append(ssh3_target)
            return shlex.join(parts)

        raise ValueError(f"Unsupported protocol: {protocol}")

    def _open_session(self, protocol: str) -> pexpect.spawn:
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

        try:
            self._await_shell_ready(child, protocol)
            child.sendline(f"export PS1='{self.args.prompt}'")
            child.expect_exact(self.args.prompt)
            # disable line wrapping side effects where possible
            child.sendline("stty -echoctl cols 200 rows 40 >/dev/null 2>&1 || true")
            child.expect_exact(self.args.prompt)
            return child
        except Exception:
            self._safe_close(child)
            raise

    def _await_shell_ready(self, child: pexpect.spawn, protocol: str) -> None:
        prompt_patterns = [
            self.args.prompt,
            r"[$#] ",
            r"[$#]$",
            r"\x1b\[[0-9;?]*[A-Za-z]",
        ]

        auth_patterns = [
            r"Are you sure you want to continue connecting \(yes/no(?:/\[fingerprint\])?\)\?",
            r"[Pp]assword:",
            r"Permission denied",
            r"Connection refused",
            r"No route to host",
            r"Connection timed out",
            r"Could not resolve hostname",
            r"Cannot assign requested address",
            r"Network is unreachable",
            r"closed by remote host",
            pexpect.EOF,
            pexpect.TIMEOUT,
        ]

        deadline = time.monotonic() + self.args.timeout

        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            idx = child.expect(prompt_patterns + auth_patterns, timeout=remaining)

            if idx == 0:
                return

            if idx in (1, 2, 3):
                # Saw a normal shell prompt or ANSI escape noise; probe shell readiness.
                child.sendline("printf '__W3_READY__\\n'")
                probe_idx = child.expect(
                    ["__W3_READY__", r"[Pp]assword:", "Permission denied", pexpect.EOF, pexpect.TIMEOUT],
                    timeout=max(1, min(5, remaining)),
                )
                if probe_idx == 0:
                    child.expect([r"[$#] ", r"[$#]$", self.args.prompt], timeout=max(1, min(5, remaining)))
                    return
                if probe_idx == 1:
                    raise SessionOpenError("Authentication fell back to password; key auth is not working")
                if probe_idx == 2:
                    raise SessionOpenError("Permission denied during readiness probe")
                if probe_idx == 3:
                    raise SessionOpenError(f"Session closed early (EOF). Output before EOF: {child.before!r}")
                raise SessionOpenError(f"Timeout while probing shell readiness. Output: {child.before!r}")

            auth_offset = len(prompt_patterns)
            which = idx - auth_offset

            if which == 0:
                child.sendline("yes")
                continue
            if which == 1:
                raise SessionOpenError("Authentication fell back to password; key auth is not working")
            if which == 2:
                raise SessionOpenError("Permission denied")
            if which == 3:
                raise SessionOpenError("Connection refused")
            if which == 4:
                raise SessionOpenError("No route to host")
            if which == 5:
                raise SessionOpenError("Connection timed out")
            if which == 6:
                raise SessionOpenError("Could not resolve hostname")
            if which == 7:
                raise SessionOpenError("Cannot assign requested address for source IP")
            if which == 8:
                raise SessionOpenError("Network is unreachable")
            if which == 9:
                raise SessionOpenError("Connection closed by remote host")
            if which == 10:
                raise SessionOpenError(f"Session closed early (EOF). Output before EOF: {child.before!r}")
            if which == 11:
                raise SessionOpenError(f"Timeout waiting for remote shell. Output: {child.before!r}")

        raise SessionOpenError(f"Timeout waiting for remote shell. Output: {child.before!r}")

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

    def _ensure_remote_command(self, child: pexpect.spawn, command_name: str) -> None:
        child.sendline(
            f"command -v {shlex.quote(command_name)} >/dev/null 2>&1 "
            f"&& printf '__W3_HAS_{command_name.upper()}__\\n' "
            f"|| printf '__W3_NO_{command_name.upper()}__\\n'"
        )
        idx = child.expect(
            [f"__W3_HAS_{command_name.upper()}__", f"__W3_NO_{command_name.upper()}__"],
            timeout=self.args.timeout,
        )
        child.expect_exact(self.args.prompt)
        if idx == 1:
            raise WorkloadPrecheckError(f"Remote command not found: {command_name}")

    def _measure_interactive_shell(self, child: pexpect.spawn, token: str) -> float:
        start_ns = time.perf_counter_ns()
        child.sendline(f"printf '%s\\n' {shlex.quote(token)}")
        child.expect_exact(token)
        child.expect_exact(self.args.prompt)
        end_ns = time.perf_counter_ns()
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_vim(self, child: pexpect.spawn, token: str) -> float:
        remote_file = self.args.remote_vim_file
        child.sendline(f"vim -Nu NONE -n {shlex.quote(remote_file)}")
        child.expect([r"-- INSERT --", r"INSERT", r'"[^"]*".*', r"\[New File\]"], timeout=self.args.timeout)

        child.send("i")
        start_ns = time.perf_counter_ns()
        child.send(token)
        child.expect_exact(token)
        end_ns = time.perf_counter_ns()

        child.send("\x1b")
        child.sendline(":q!")
        child.expect_exact(self.args.prompt)
        return (end_ns - start_ns) / 1_000_000.0

    def _measure_nano(self, child: pexpect.spawn, token: str) -> float:
        remote_file = self.args.remote_nano_file
        child.sendline(f"nano --ignorercfiles {shlex.quote(remote_file)}")
        child.expect([r"GNU nano", r"\^G Help"], timeout=self.args.timeout)

        start_ns = time.perf_counter_ns()
        child.send(token)
        child.expect_exact(token)
        end_ns = time.perf_counter_ns()

        child.sendcontrol("x")
        child.send("n")
        child.expect_exact(self.args.prompt)
        return (end_ns - start_ns) / 1_000_000.0

    def _run_sample(self, child: pexpect.spawn, protocol: str, workload: str, round_id: int, sample_id: int) -> None:
        token = self._token(protocol, workload, round_id, sample_id)
        if workload == "interactive_shell":
            latency = self._measure_interactive_shell(child, token)
        elif workload == "vim":
            latency = self._measure_vim(child, token)
        elif workload == "nano":
            latency = self._measure_nano(child, token)
        else:
            raise ValueError(f"Unsupported workload: {workload}")

        self.results[protocol][workload].append(latency)
        self.records.append(SampleRecord(protocol, workload, round_id, sample_id, token, latency))

    def _preflight_protocol(self, protocol: str) -> None:
        child = self._open_session(protocol)
        try:
            child.sendline("printf '__W3_PREFLIGHT_OK__\\n'")
            child.expect_exact("__W3_PREFLIGHT_OK__")
            child.expect_exact(self.args.prompt)

            if "vim" in self.args.workloads:
                self._ensure_remote_command(child, "vim")
            if "nano" in self.args.workloads:
                self._ensure_remote_command(child, "nano")
        finally:
            self._close_session(child)

    def _record_failure(
        self,
        protocol: str,
        workload: str,
        round_id: int,
        sample_id: int,
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
                workload=workload,
                round_id=round_id,
                sample_id=sample_id,
                error_type=type(exc).__name__,
                error_message=f"{exc}{extra}",
            )
        )

    def _run_session_group(self, protocol: str, workload: str) -> None:
        total = self.args.iterations + self.args.warmup_rounds
        child: Optional[pexpect.spawn] = None
        try:
            child = self._open_session(protocol)
            if workload == "vim":
                self._ensure_remote_command(child, "vim")
            elif workload == "nano":
                self._ensure_remote_command(child, "nano")

            for sample_idx in range(1, total + 1):
                is_warmup = sample_idx <= self.args.warmup_rounds
                round_id = 0 if is_warmup else (sample_idx - self.args.warmup_rounds)
                measure_id = sample_idx if is_warmup else (sample_idx - self.args.warmup_rounds)

                try:
                    self._run_sample(child, protocol, workload, round_id, measure_id)
                    tag = "warmup" if is_warmup else "measure"
                    limit = self.args.warmup_rounds if is_warmup else self.args.iterations
                    print(f"[{protocol:>4}/{workload:<18}] {tag} {measure_id}/{limit}: OK")
                except (pexpect.TIMEOUT, pexpect.EOF, ValueError, RuntimeError) as exc:
                    self._record_failure(protocol, workload, round_id, measure_id, exc, child)
                    print(
                        f"[{protocol:>4}/{workload:<18}] "
                        f"{'warmup' if is_warmup else 'measure'} {measure_id}: "
                        f"FAIL ({type(exc).__name__}: {exc})"
                    )

                    if child is not None:
                        self._safe_close(child)
                        child = None

                    if self.args.reopen_on_failure or is_warmup or protocol in ("ssh", "mosh", "ssh3"):
                        try:
                            child = self._open_session(protocol)
                            if workload == "vim":
                                self._ensure_remote_command(child, "vim")
                            elif workload == "nano":
                                self._ensure_remote_command(child, "nano")
                        except Exception as reopen_exc:
                            self._record_failure(protocol, workload, round_id, measure_id, reopen_exc, child)
                            print(
                                f"[{protocol:>4}/{workload:<18}] reopen after fail: "
                                f"FAIL ({type(reopen_exc).__name__}: {reopen_exc})"
                            )
                            child = None

                    if child is None and not self.args.reopen_on_failure:
                        break
        finally:
            if child is not None:
                self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)

        if self.args.preflight:
            for protocol in self.args.protocols:
                print(f"[preflight/{protocol}] checking session bootstrap and remote tools...")
                self._preflight_protocol(protocol)
                print(f"[preflight/{protocol}] OK")

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
        fail_n = sum(1 for f in self.failures if f.protocol == protocol and f.workload == workload)
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
        return [self._summary_row(p, w) for p in self.args.protocols for w in self.args.workloads]

    def print_report(self) -> None:
        width = 146
        print("\n" + "=" * width)
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+/-':>9}"
        )
        print("-" * width)
        for row in self.summaries():
            def fmt(v: Optional[float]) -> str:
                return f"{v:.2f}" if v is not None else "N/A"

            print(
                f"{row.protocol:<8} | {row.workload:<18} | {row.n:>4} | {row.failures:>4} | {row.success_rate_pct:>8.1f} | "
                f"{fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | {fmt(row.stdev_ms):>8} | "
                f"{fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} | {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
            )
        print("=" * width)

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        summary_json = outdir / "w3_summary.json"
        raw_csv = outdir / "w3_raw_samples.csv"
        failures_csv = outdir / "w3_failures.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "workloads": self.args.workloads,
                "iterations": self.args.iterations,
                "warmup_rounds": self.args.warmup_rounds,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "topology": {
                    "client": self.args.source_ip or "default-route",
                    "server": self.args.host,
                },
                "metric_name": "interactive_response_latency_ms",
                "metric_note": (
                    "Measured from input injection by pexpect to output observation in the terminal stream; "
                    "this is not physical keyboard-to-screen keystroke latency."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
        }
        summary_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        with raw_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "token", "latency_ms"])
            for r in self.records:
                writer.writerow([r.protocol, r.workload, r.round_id, r.sample_id, r.token, f"{r.latency_ms:.6f}"])

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "workload", "round_id", "sample_id", "error_type", "error_message"])
            for r in self.failures:
                writer.writerow([r.protocol, r.workload, r.round_id, r.sample_id, r.error_type, r.error_message])

        print(f"Saved summary JSON: {summary_json}")
        print(f"Saved raw samples CSV: {raw_csv}")
        print(f"Saved failures CSV: {failures_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W3 Interactive Editing benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default=None, help="Client source IP for SSH/Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--iterations", type=int, default=100, help="Recorded samples per protocol/workload")
    p.add_argument("--warmup-rounds", type=int, default=5, help="Warmup samples per protocol/workload")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="w3_results", help="Directory for JSON/CSV outputs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Temporary shell prompt marker")
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH, help="SSH3 terminal path suffix")
    p.add_argument("--ssh3-insecure", action="store_true", help="Use -insecure for ssh3")
    p.add_argument("--batch-mode", action="store_true", help="Enable BatchMode for SSHv2/Mosh bootstrap SSH")
    p.add_argument("--strict-host-key-checking", action="store_true", help="Keep strict host key checking enabled")
    p.add_argument("--mosh-predict", default="adaptive", choices=["adaptive", "always", "never"], help="Mosh prediction mode")
    p.add_argument("--remote-vim-file", default="/tmp/w3_vim_bench.txt", help="Remote file path used for vim workload")
    p.add_argument("--remote-nano-file", default="/tmp/w3_nano_bench.txt", help="Remote file path used for nano workload")
    p.add_argument("--shuffle-pairs", action="store_true", help="Shuffle protocol/workload order")
    p.add_argument("--reopen-on-failure", action="store_true", help="Reopen session after a failed measured sample")
    p.add_argument("--log-pexpect", action="store_true", help="Save raw pexpect output per protocol")
    p.add_argument("--preflight", action="store_true", help="Run one bootstrap/probe session per protocol before benchmarking")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

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