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
DEFAULT_WORKLOADS = ["top", "tail -f logs", "ping"]
DEFAULT_PROMPT = "__W2_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
MARKER_TAIL_LEN = 12
_TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

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

@dataclass
class ClockSyncRecord:
    protocol: str
    workload: str
    round_id: int
    offset_ms: float
    best_rtt_ms: float
    uncertainty_ms: float
    samples: int

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
        self.clock_sync_records: List[ClockSyncRecord] = []
        self.current_clock_offset_ns: int = 0
        self.current_clock_uncertainty_ns: int = 0
        self.prev_marker_tail: Optional[str] = None

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    @staticmethod
    def _build_gapped_literal(token: str) -> str:
        return "".join(re.escape(ch) + _ECHO_GAP for ch in token)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return re.sub(_ANSI_SEQ, "", text)

    def _extract_remote_ts_from_stream(
        self,
        raw_stream: str,
        marker_prefix: str,
    ) -> Optional[int]:
        """Extract the latest timestamp that follows a marker prefix.

        This matcher is resilient to ANSI/control characters inserted between
        marker bytes (common with terminal redraws / mosh).
        """
        marker_re = re.compile(
            self._build_gapped_literal(marker_prefix) +
            r"((?:[0-9.%N]|" + _ANSI_SEQ + r"|[\r\n\b])+)"
        )
        matches = marker_re.findall(raw_stream)
        for ts_raw in reversed(matches):
            try:
                return self._parse_epoch_to_ns(ts_raw)
            except ValueError:
                continue
        return None

    def _next_marker_tail(self) -> str:
        if self.prev_marker_tail is None:
            tail = "".join(random.choice(_TAIL_ALPHABET) for _ in range(MARKER_TAIL_LEN))
            self.prev_marker_tail = tail
            return tail

        chars: List[str] = []
        for prev_ch in self.prev_marker_tail:
            candidates = [c for c in _TAIL_ALPHABET if c != prev_ch]
            chars.append(random.choice(candidates))
        tail = "".join(chars)
        self.prev_marker_tail = tail
        return tail

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
                # Brief pause before retry
                time.sleep(2 * attempt)

        raise last_exc  # type: ignore[misc]

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

    @staticmethod
    def _parse_epoch_to_ns(raw: str) -> int:
        """Parse epoch strings in one of: ns, us, ms, s, or s.frac into ns."""
        token = raw.strip()
        token = re.sub(_ANSI_SEQ, "", token)
        if not token:
            raise ValueError("Empty remote timestamp")

        if token.endswith("%N") and token[:-2].isdigit():
            # Non-GNU date may print literal %N. Fall back to epoch seconds.
            return int(token[:-2]) * 1_000_000_000

        m = re.search(r"\d+\.\d+|\d+", token)
        if m is None:
            raise ValueError(f"Invalid epoch timestamp: {raw!r}")
        token = m.group(0)

        if "." in token:
            sec_s, frac_s = token.split(".", 1)
            if not sec_s.isdigit() or not frac_s.isdigit():
                raise ValueError(f"Invalid fractional epoch timestamp: {raw!r}")
            frac_ns = (frac_s + "000000000")[:9]
            return int(sec_s) * 1_000_000_000 + int(frac_ns)

        n = int(token)
        digits = len(token)
        if digits >= 18:
            return n
        if digits >= 15:
            return n * 1_000
        if digits >= 12:
            return n * 1_000_000
        return n * 1_000_000_000

    def _clock_sync(self, child: pexpect.spawn) -> tuple[int, int, int]:
        """Estimate remote-local clock offset using min-RTT sampling.

        Returns:
            (offset_ns, best_rtt_ns, uncertainty_ns)
        where offset_ns = remote_clock - local_clock.
        """
        samples: List[tuple[int, int]] = []
        marker = "__W2_CLK__"

        for _ in range(self.args.clock_sync_samples):
            sample_ok = False
            for attempt in range(1, 3):
                marker_tail = self._next_marker_tail()
                marker_prefix = f"{marker}:{marker_tail}:"

                t0 = time.time_ns()
                child.sendline(f'echo "{marker}:{marker_tail}:$(date +%s%N)"')
                self._expect_prompt(child)
                t1 = time.time_ns()

                remote_ns = self._extract_remote_ts_from_stream(
                    child.before,
                    marker_prefix,
                )

                if remote_ns is None:
                    debug_snippet = self._strip_ansi(child.before).replace("\r", "\\r").replace("\n", "\\n")
                    debug_snippet = debug_snippet[-200:]
                    print(
                        f"  [WARN] clock_sync sample parse failed (attempt {attempt}/2), retrying... "
                        f"marker_prefix={marker_prefix!r} tail_stream={debug_snippet!r}",
                        flush=True,
                    )
                    continue

                rtt_ns = max(0, t1 - t0)
                midpoint_local_ns = t0 + (rtt_ns // 2)
                offset_ns = remote_ns - midpoint_local_ns
                samples.append((rtt_ns, offset_ns))
                sample_ok = True
                break

            if not sample_ok:
                print(
                    "  [WARN] clock_sync sample dropped after retries",
                    flush=True,
                )

            if self.args.clock_sync_pause > 0:
                time.sleep(self.args.clock_sync_pause)

        if not samples:
            raise ValueError("No clock-sync samples collected after retries")

        best_rtt_ns, best_offset_ns = min(samples, key=lambda x: x[0])
        uncertainty_ns = best_rtt_ns // 2
        return best_offset_ns, best_rtt_ns, uncertainty_ns

    def _event_latency_ms(self, remote_event_ns: int, recv_local_ns: int) -> float:
        recv_remote_ns = recv_local_ns + self.current_clock_offset_ns
        return (recv_remote_ns - remote_event_ns) / 1_000_000.0

    def _measure_top(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        interval = self.args.top_interval
        marker_re = re.compile(
            self._build_gapped_literal("W2_TOP_REFRESH:") + r"(\d+)"
        )
        TOP_LOOP = (
            f"while true; do "
            f"echo \"W2_TOP_REFRESH:$(date +%s%N)\"; "
            f"top -bn1 2>/dev/null | head -20; "
            f"sleep {interval}; "
            f"done"
        )
        child.sendline(TOP_LOOP)

        for _ in range(3):
            child.expect(marker_re, timeout=self.args.timeout)

        for i in range(iterations):
            child.expect(marker_re, timeout=self.args.timeout)
            recv_ns = time.time_ns()
            remote_event_ns = self._parse_epoch_to_ns(child.match.group(1))
            lat = self._event_latency_ms(remote_event_ns, recv_ns)
            report_cb(i + 1, lat)

        for _ in range(3):
            child.sendcontrol("c")
            time.sleep(0.5)
        try:
            self._expect_prompt(child)
        except pexpect.TIMEOUT:
            child.sendcontrol("c")
            time.sleep(1)
            self._expect_prompt(child)

    def _measure_tail(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        marker_re = re.compile(
            self._build_gapped_literal("W2_TAIL_") + r"\d+" + _ECHO_GAP +
            re.escape(":") + _ECHO_GAP + r"(\d+)"
        )
        remote_log = "/tmp/w2_test.log"
        child.sendline(f"rm -f {remote_log} && touch {remote_log}")
        self._expect_prompt(child)

        child.sendline(
            f"W2_SEQ=0; while true; do "
            f"W2_SEQ=$((W2_SEQ+1)); "
            f"echo \"W2_TAIL_$W2_SEQ:$(date +%s%N)\" >> {remote_log}; "
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
            child.expect(marker_re, timeout=self.args.timeout)

        for i in range(iterations):
            child.expect(marker_re, timeout=self.args.timeout)
            recv_ns = time.time_ns()
            remote_event_ns = self._parse_epoch_to_ns(child.match.group(1))
            lat = self._event_latency_ms(remote_event_ns, recv_ns)
            report_cb(i + 1, lat)

        child.sendcontrol("c")
        self._expect_prompt(child)

        # Cleanup background writer
        child.sendline(f"kill {bg_pid} 2>/dev/null; rm -f {remote_log}")
        self._expect_prompt(child)

    def _measure_ping(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        marker_re = re.compile(
            re.escape("[") + _ECHO_GAP + r"(\d+\.\d+)" +
            _ECHO_GAP + re.escape("]")
        )
        ping_target = self.args.ping_target or "127.0.0.1"
        child.sendline(f"ping -D -i 0.1 {ping_target}")
        child.expect(r"PING ", timeout=self.args.timeout)

        for _ in range(5):
            child.expect(marker_re, timeout=self.args.timeout)

        for i in range(iterations):
            child.expect(marker_re, timeout=self.args.timeout)
            recv_ns = time.time_ns()
            remote_event_ns = self._parse_epoch_to_ns(child.match.group(1))
            lat = self._event_latency_ms(remote_event_ns, recv_ns)
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
        elif workload == "tail -f logs":
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
                    offset_ns, best_rtt_ns, uncertainty_ns = self._clock_sync(child)
                    self.current_clock_offset_ns = offset_ns
                    self.current_clock_uncertainty_ns = uncertainty_ns
                    self.clock_sync_records.append(
                        ClockSyncRecord(
                            protocol=protocol,
                            workload=workload,
                            round_id=trial_id,
                            offset_ms=offset_ns / 1_000_000.0,
                            best_rtt_ms=best_rtt_ns / 1_000_000.0,
                            uncertainty_ms=uncertainty_ns / 1_000_000.0,
                            samples=self.args.clock_sync_samples,
                        )
                    )
                    print(
                        f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}/{self.args.trials}"
                        f" clock_sync offset={offset_ns / 1_000_000.0:.3f} ms"
                        f" (best_rtt={best_rtt_ns / 1_000_000.0:.3f} ms, "
                        f"uncertainty<=+/-{uncertainty_ns / 1_000_000.0:.3f} ms)",
                        flush=True,
                    )
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
                        if child is not None:
                            self._close_session(child, protocol)
                            child = None
                        time.sleep(3)
                        continue
            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
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

    def _clock_sync_stats(self, protocol: str, workload: str) -> dict:
        rows = [
            r for r in self.clock_sync_records
            if r.protocol == protocol and r.workload == workload
        ]
        if not rows:
            return dict(
                n=0,
                offset_mean=None,
                offset_median=None,
                offset_stdev=None,
                best_rtt_mean=None,
                uncertainty_mean=None,
            )
        offsets = [r.offset_ms for r in rows]
        best_rtts = [r.best_rtt_ms for r in rows]
        uncertainties = [r.uncertainty_ms for r in rows]
        n = len(rows)
        return dict(
            n=n,
            offset_mean=statistics.mean(offsets),
            offset_median=statistics.median(offsets),
            offset_stdev=statistics.stdev(offsets) if n > 1 else 0.0,
            best_rtt_mean=statistics.mean(best_rtts),
            uncertainty_mean=statistics.mean(uncertainties),
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

        cs_width = 122
        print("\n" + "-" * cs_width)
        print(
            "CLOCK SYNC (ms)  "
            "[offset = remote_clock - local_clock; uncertainty ~= best_rtt/2]"
        )
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>3} |"
            f" {'OffsetMean':>10} | {'OffsetMed':>10} | {'OffsetStd':>10} |"
            f" {'BestRTTMean':>11} | {'UncertaintyMean':>15}"
        )
        print("-" * cs_width)
        for protocol in self.args.protocols:
            for workload in self.args.workloads:
                s = self._clock_sync_stats(protocol, workload)
                print(
                    f"{protocol:<8} | {workload:<18} | {s['n']:>3} |"
                    f" {fmt(s['offset_mean']):>10} | {fmt(s['offset_median']):>10} |"
                    f" {fmt(s['offset_stdev']):>10} | {fmt(s['best_rtt_mean']):>11} |"
                    f" {fmt(s['uncertainty_mean']):>15}"
                )
        print("-" * cs_width)

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        summary_json = outdir / "w2_summary.json"
        raw_csv = outdir / "w2_raw_samples.csv"
        failures_csv = outdir / "w2_failures.csv"
        setup_csv = outdir / "w2_session_setup.csv"
        clock_sync_csv = outdir / "w2_clock_sync.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "workloads": self.args.workloads,
                "trials": self.args.trials,
                "iterations": self.args.iterations,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "clock_sync_samples": self.args.clock_sync_samples,
                "clock_sync_pause_sec": self.args.clock_sync_pause,
                "topology": {
                    "client": self.args.source_ip or "unknown",
                    "server": self.args.host,
                },
                "metric_name": "screen_update_e2e_latency_ms",
                "metric_note": (
                    "Latency is measured as: remote event timestamp -> client receive timestamp. "
                    "For each session, remote/local clock offset is estimated with min-RTT clock sync samples "
                    "(Cristian-style; offset = remote_clock - local_clock). "
                    f"top emits W2_TOP_REFRESH:<epoch_ns> every {self.args.top_interval}s; "
                    "tail writer emits W2_TAIL_<seq>:<epoch_ns> every 50ms and client follows via tail -f; "
                    f"ping uses 'ping -D -i 0.1 {self.args.ping_target or '127.0.0.1'}' and parses [epoch.us]. "
                    "Per-sample latency uses event_remote_ns and receive_local_ns converted into remote timebase via estimated offset. "
                    "Clock-sync uncertainty is approximately +/- best_rtt/2 for the chosen sample."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {w: self._session_setup_stats(p, w) for w in self.args.workloads}
                for p in self.args.protocols
            },
            "clock_sync": {
                p: {w: self._clock_sync_stats(p, w) for w in self.args.workloads}
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

        with clock_sync_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "protocol",
                "workload",
                "trial_id",
                "offset_ms",
                "best_rtt_ms",
                "uncertainty_ms",
                "samples",
            ])
            for r in self.clock_sync_records:
                writer.writerow([
                    r.protocol,
                    r.workload,
                    r.round_id,
                    f"{r.offset_ms:.6f}",
                    f"{r.best_rtt_ms:.6f}",
                    f"{r.uncertainty_ms:.6f}",
                    r.samples,
                ])

        print(f"Saved summary JSON    : {summary_json}")
        print(f"Saved raw samples CSV : {raw_csv}")
        print(f"Saved failures CSV    : {failures_csv}")
        print(f"Saved session setup   : {setup_csv}")
        print(f"Saved clock sync CSV  : {clock_sync_csv}")


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
    p.add_argument("--trials", type=int, default=15, help="Independent sessions per protocol/workload pair")
    p.add_argument("--iterations", type=int, default=100, help="Recorded samples per trial")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--clock-sync-samples", type=int, default=7, help="Clock-sync probes per session (default: 7)")
    p.add_argument("--clock-sync-pause", type=float, default=0.02, help="Pause between clock-sync probes in seconds")
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
    if args.clock_sync_samples <= 0:
        parser.error("--clock-sync-samples must be > 0")
    if args.clock_sync_pause < 0:
        parser.error("--clock-sync-pause must be >= 0")

    bench = W2Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
