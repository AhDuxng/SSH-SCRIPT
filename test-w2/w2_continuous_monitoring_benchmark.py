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

_GAPPED_EPOCH_NS = rf"(\d(?:{_ECHO_GAP}\d){{18}})"

_GAPPED_PING_EPOCH_US = (
    rf"(\d(?:{_ECHO_GAP}\d){{9}}"
    rf"{_ECHO_GAP}\."
    rf"{_ECHO_GAP}\d(?:{_ECHO_GAP}\d){{5}})"
)
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
class ClockOffsetRecord:
    protocol: str
    workload: str
    round_id: int
    probes: int
    offset_ns: int
    offset_ms: float
    median_rtt_ms: float
    method: str

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
        self.clock_offsets: List[ClockOffsetRecord] = []
        self.current_clock_offset_ns: int = 0

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    @staticmethod
    def _build_gapped_literal(token: str) -> str:
        return "".join(re.escape(ch) + _ECHO_GAP for ch in token)

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
            mosh_ssh = [arg for arg in ssh_common[:-1] if arg != "-tt"]
            ssh_cmd = shlex.join(mosh_ssh)
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

        if last_exc is None:
            raise RuntimeError(f"_open_session({protocol}) failed without a captured exception")
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

    @staticmethod
    def _digits_to_epoch_ns(token: str) -> int:
        n = int(token)
        digits = len(token)
        if digits >= 18:
            return n
        if digits >= 15:
            return n * 1_000
        if digits >= 12:
            return n * 1_000_000
        return n * 1_000_000_000

    @classmethod
    def _token_to_epoch_candidates_ns(cls, token: str) -> List[int]:
        if not token:
            return []
        if token.endswith("%N") and token[:-2].isdigit():
            return [int(token[:-2]) * 1_000_000_000]
        if "." in token:
            sec_s, frac_s = token.split(".", 1)
            if sec_s.isdigit() and frac_s.isdigit():
                frac_ns = (frac_s + "000000000")[:9]
                return [int(sec_s) * 1_000_000_000 + int(frac_ns)]
            return []
        if not token.isdigit():
            return []

        if len(token) <= 19:
            return [cls._digits_to_epoch_ns(token)]

        return [int(token[i : i + 19]) for i in range(0, len(token) - 18)]

    @classmethod
    def _parse_epoch_to_ns(cls, raw: str, recv_local_ns: Optional[int] = None) -> int:
        """Parse epoch strings in one of: ns, us, ms, s, or s.frac into ns."""
        cleaned = re.sub(_ANSI_SEQ, "", raw).replace("\b", " ")
        cleaned = cleaned.strip()
        if not cleaned:
            raise ValueError("Empty remote timestamp")

        tokens = re.findall(r"\d+%N|\d+\.\d+|\d+", cleaned)
        if not tokens:
            raise ValueError(f"Invalid epoch timestamp: {raw!r}")

        candidates: List[int] = []
        for token in tokens:
            candidates.extend(cls._token_to_epoch_candidates_ns(token))

        if not candidates:
            raise ValueError(f"Invalid epoch timestamp: {raw!r}")

        if recv_local_ns is None:
            return candidates[0]

        return min(candidates, key=lambda ns: abs(recv_local_ns - ns))

    def _estimate_clock_offset_ns(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> int:
        if self.args.clock_offset_mode != "estimate":
            return 0

        marker_re = re.compile(
            self._build_gapped_literal("W2_CLOCK_TS:") + _GAPPED_EPOCH_NS
        )
        offsets_ns: List[int] = []
        rtts_ms: List[float] = []
        probes = max(1, self.args.clock_offset_probes)

        for probe_idx in range(1, probes + 1):
            try:
                t0 = time.time_ns()
                child.sendline("echo \"W2_CLOCK_TS:$(date +%s%N)\"")
                child.expect(marker_re, timeout=self.args.timeout)
                t1 = time.time_ns()
                mid_local_ns = (t0 + t1) // 2
                remote_ns = self._parse_epoch_to_ns(child.match.group(1), mid_local_ns)
                offsets_ns.append(mid_local_ns - remote_ns)
                rtts_ms.append((t1 - t0) / 1_000_000.0)
                self._expect_prompt(child)
            except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                print(
                    f"  [WARN] clock offset probe {probe_idx}/{probes} failed: {type(exc).__name__}. "
                    "Fallback to zero offset for this trial.",
                    flush=True,
                )
                try:
                    child.sendcontrol("c")
                    self._expect_prompt(child)
                except Exception:
                    pass
                offsets_ns.clear()
                rtts_ms.clear()
                break

        if not offsets_ns:
            self.clock_offsets.append(
                ClockOffsetRecord(
                    protocol=protocol,
                    workload=workload,
                    round_id=trial_id,
                    probes=probes,
                    offset_ns=0,
                    offset_ms=0.0,
                    median_rtt_ms=0.0,
                    method="estimate_failed_fallback_zero",
                )
            )
            return 0

        offset_ns = int(statistics.median(offsets_ns))
        median_rtt_ms = statistics.median(rtts_ms)
        self.clock_offsets.append(
            ClockOffsetRecord(
                protocol=protocol,
                workload=workload,
                round_id=trial_id,
                probes=probes,
                offset_ns=offset_ns,
                offset_ms=offset_ns / 1_000_000.0,
                median_rtt_ms=median_rtt_ms,
                method="midpoint_round_trip",
            )
        )
        print(
            f"[{protocol:>4}/{workload:<18}] trial {trial_id:>2}/{self.args.trials}"
            f" clock_offset_est={offset_ns / 1_000_000.0:.2f} ms (median_rtt={median_rtt_ms:.2f} ms)",
            flush=True,
        )
        return offset_ns

    def _event_latency_ms(self, remote_event_ns: int, recv_local_ns: int) -> float:
        raw_ns = recv_local_ns - remote_event_ns
        corrected_ns = raw_ns - self.current_clock_offset_ns
        lat = corrected_ns / 1_000_000.0
        if lat < 0:
            print(
                f"  [WARN] Negative latency {lat:.2f} ms detected "
                f"(clock offset mode={self.args.clock_offset_mode})",
                flush=True,
            )
        return lat

    def _latency_is_valid(self, latency_ms: float) -> bool:
        return self.args.min_valid_latency_ms <= latency_ms <= self.args.max_valid_latency_ms

    def _warn_and_count_invalid(
        self,
        workload: str,
        dropped: int,
        reason: str,
        raw_token: str = "",
    ) -> int:
        dropped += 1
        detail = f" raw={raw_token[:120]!r}" if raw_token else ""
        print(
            f"  [WARN] Dropping invalid {workload} sample #{dropped}: {reason}.{detail}",
            flush=True,
        )
        if dropped > self.args.max_invalid_samples:
            raise ValueError(
                f"Too many invalid {workload} samples (>{self.args.max_invalid_samples})"
            )
        return dropped

    def _measure_top(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        interval = self.args.top_interval
        marker_re = re.compile(
            self._build_gapped_literal("W2_TOP_REFRESH:") + _GAPPED_EPOCH_NS
        )
        TOP_LOOP = (
            f"while true; do "
            f"echo \"W2_TOP_REFRESH:$(date +%s%N)\"; "
            f"top -bn1 2>/dev/null | head -20; "
            f"sleep {interval}; "
            f"done"
        )
        child.sendline(TOP_LOOP)

        expect_timeout = max(self.args.timeout, int(interval * 3) + 5)
        dropped = 0

        for _ in range(3):
            child.expect(marker_re, timeout=expect_timeout)

        sample_id = 0
        while sample_id < iterations:
            child.expect(marker_re, timeout=expect_timeout)
            recv_ns = time.time_ns()
            raw_token = child.match.group(1)
            try:
                remote_event_ns = self._parse_epoch_to_ns(raw_token, recv_ns)
            except ValueError as exc:
                dropped = self._warn_and_count_invalid("top", dropped, str(exc), raw_token)
                continue
            lat = self._event_latency_ms(remote_event_ns, recv_ns)
            if not self._latency_is_valid(lat):
                dropped = self._warn_and_count_invalid(
                    "top",
                    dropped,
                    (
                        f"latency {lat:.2f} ms outside "
                        f"[{self.args.min_valid_latency_ms:.2f}, {self.args.max_valid_latency_ms:.2f}]"
                    ),
                    raw_token,
                )
                continue
            sample_id += 1
            report_cb(sample_id, lat)

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
            self._build_gapped_literal("W2_TAIL_") + r"\d(?:" + _ECHO_GAP + r"\d)*" + _ECHO_GAP +
            re.escape(":") + _ECHO_GAP + _GAPPED_EPOCH_NS
        )
        remote_log = "/tmp/w2_test.log"
        writer_pid = ""
        child.sendline(f"rm -f {remote_log} && touch {remote_log}")
        self._expect_prompt(child)

        child.sendline(
            f"(sleep 0.20; "
            f"W2_SEQ=0; while true; do "
            f"W2_SEQ=$((W2_SEQ+1)); "
            f"echo \"W2_TAIL_$W2_SEQ:$(date +%s%N)\" >> {remote_log}; "
            f"sleep 0.05; "
            f"done) & "
            f"W2_WRITER_PID=$!; echo W2_WRITER_PID=$W2_WRITER_PID; "
            f"tail -n 0 -f {remote_log}"
        )
        child.expect(r"W2_WRITER_PID=(\d+)", timeout=self.args.timeout)
        writer_pid = child.match.group(1)
        dropped = 0

        try:
            for _ in range(10):
                child.expect(marker_re, timeout=self.args.timeout)

            sample_id = 0
            while sample_id < iterations:
                child.expect(marker_re, timeout=self.args.timeout)
                recv_ns = time.time_ns()
                raw_token = child.match.group(1)
                try:
                    remote_event_ns = self._parse_epoch_to_ns(raw_token, recv_ns)
                except ValueError as exc:
                    dropped = self._warn_and_count_invalid("tail", dropped, str(exc), raw_token)
                    continue
                lat = self._event_latency_ms(remote_event_ns, recv_ns)
                if not self._latency_is_valid(lat):
                    dropped = self._warn_and_count_invalid(
                        "tail",
                        dropped,
                        (
                            f"latency {lat:.2f} ms outside "
                            f"[{self.args.min_valid_latency_ms:.2f}, {self.args.max_valid_latency_ms:.2f}]"
                        ),
                        raw_token,
                    )
                    continue
                sample_id += 1
                report_cb(sample_id, lat)
        finally:
            try:
                child.sendcontrol("c")
                self._expect_prompt(child)
            except Exception:
                try:
                    child.sendcontrol("c")
                    time.sleep(1)
                    self._expect_prompt(child)
                except Exception:
                    pass

            try:
                child.sendline(f"kill {writer_pid} 2>/dev/null; rm -f {remote_log}")
                self._expect_prompt(child)
            except Exception:
                pass

    def _measure_ping(
        self,
        child: pexpect.spawn,
        iterations: int,
        report_cb: Callable[[int, float], None],
    ) -> None:
        marker_re = re.compile(
            re.escape("[") + _ECHO_GAP + _GAPPED_PING_EPOCH_US +
            _ECHO_GAP + re.escape("]")
        )
        ping_target = self.args.ping_target or "127.0.0.1"
        child.sendline(f"ping -D -i 0.1 {ping_target}")
        child.expect(r"PING ", timeout=self.args.timeout)
        dropped = 0

        for _ in range(5):
            child.expect(marker_re, timeout=self.args.timeout)

        sample_id = 0
        while sample_id < iterations:
            child.expect(marker_re, timeout=self.args.timeout)
            recv_ns = time.time_ns()
            raw_token = child.match.group(1)
            try:
                remote_event_ns = self._parse_epoch_to_ns(raw_token, recv_ns)
            except ValueError as exc:
                dropped = self._warn_and_count_invalid("ping", dropped, str(exc), raw_token)
                continue
            lat = self._event_latency_ms(remote_event_ns, recv_ns)
            if not self._latency_is_valid(lat):
                dropped = self._warn_and_count_invalid(
                    "ping",
                    dropped,
                    (
                        f"latency {lat:.2f} ms outside "
                        f"[{self.args.min_valid_latency_ms:.2f}, {self.args.max_valid_latency_ms:.2f}]"
                    ),
                    raw_token,
                )
                continue
            sample_id += 1
            report_cb(sample_id, lat)

        child.sendcontrol("c")
        self._expect_prompt(child)

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> List[float]:
        trial_samples: List[float] = []

        def report_cb(s_idx: int, lat: float) -> None:
            self.results[protocol][workload].append(lat)
            trial_samples.append(lat)
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
        return trial_samples

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
                    self.current_clock_offset_ns = self._estimate_clock_offset_ns(
                        child, protocol, workload, trial_id
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
                self.current_clock_offset_ns = 0
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
        else:
            print(
                "  [INFO] --shuffle-pairs is disabled; enabling it is recommended to reduce order effects.",
                flush=True,
            )
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
            return SummaryRow(
                protocol,
                workload,
                0,
                fail_n,
                success_rate,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
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

        if self.args.clock_offset_mode == "estimate":
            clock_offset_warning = None
        else:
            clock_offset_warning = (
                "Clock offset correction is disabled. "
                "Raw screen-update visibility latency can include client/server clock skew."
            )

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
                "topology": {
                    "client": self.args.source_ip or "unknown",
                    "server": self.args.host,
                },
                "metric_name": "end_to_end_screen_update_latency_ms",
                "metric_alias": "terminal_output_visibility_latency_ms",
                "shuffle_pairs_enabled": self.args.shuffle_pairs,
                "fairness_recommendation": (
                    "Use --shuffle-pairs to reduce order effects across protocol/workload runs."
                ),
                "clock_offset_mode": self.args.clock_offset_mode,
                "clock_offset_probes": self.args.clock_offset_probes,
                "clock_offset_warning": clock_offset_warning,
                "metric_note": (
                    "This benchmark measures end-to-end terminal output visibility latency: "
                    "(client_receive_time_ns - remote_event_timestamp_ns - estimated_clock_offset_ns). "
                    "It is not a pure protocol/network RTT metric. "
                    f"top emits W2_TOP_REFRESH:<epoch_ns> every {self.args.top_interval}s; "
                    "tail uses tail -n 0 -f and a background writer emits W2_TAIL_<seq>:<epoch_ns> every 50ms; "
                    f"ping uses 'ping -D -i 0.1 {self.args.ping_target or '127.0.0.1'}' and measures ping output update latency "
                    "(screen visibility of ping timestamps), not ICMP network ping RTT. "
                    "When clock offset correction is disabled, results can include host clock-offset bias. "
                    f"Samples outside [{self.args.min_valid_latency_ms:.2f}, {self.args.max_valid_latency_ms:.2f}] ms "
                    f"are treated as invalid and dropped (limit: {self.args.max_invalid_samples} per trial)."
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
    p.add_argument("--clock-offset-mode", default="none", choices=["none", "estimate"], help="Clock offset correction mode")
    p.add_argument("--clock-offset-probes", type=int, default=3, help="Remote date probes per trial when --clock-offset-mode=estimate")
    p.add_argument("--min-valid-latency-ms", type=float, default=-5000.0, help="Drop samples below this latency threshold")
    p.add_argument("--max-valid-latency-ms", type=float, default=60000.0, help="Drop samples above this latency threshold")
    p.add_argument("--max-invalid-samples", type=int, default=100, help="Max dropped invalid samples per trial before failing")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.max_invalid_samples < 0:
        parser.error("--max-invalid-samples must be >= 0")
    if args.max_valid_latency_ms <= args.min_valid_latency_ms:
        parser.error("--max-valid-latency-ms must be > --min-valid-latency-ms")
    if args.clock_offset_probes <= 0:
        parser.error("--clock-offset-probes must be > 0")

    bench = W2Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
