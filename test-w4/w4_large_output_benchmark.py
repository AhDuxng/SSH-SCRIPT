#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import errno
import json
import math
import random
import re
import shlex
import statistics
import sys
import time
from dataclasses import asdict, dataclass
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
DEFAULT_WORKLOADS = ["large_output"]
DEFAULT_PROMPT = "W4PROMPT#"
DEFAULT_SSH3_PATH = "/ssh3-term"
MARKER_TAIL_LEN = 12
_TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# Deterministic, fixed-size, low-CPU large outputs. Each command prints a
# Pre-generated fixture files containing real filesystem paths from `find /`.
# Generate once on the server with setup_w4_fixtures.sh before running benchmarks.
# Properties: byte-identical output per trial, page-cached after warmup,
# realistic ~4:1 gzip ratio (vs ~1000:1 for base64-of-zeros).
# server_exec_time ≈ <5ms (cat from page cache) — network signal dominates.
DEFAULT_COMMANDS = [
    "cat /tmp/w4_paths_small.txt",    # ~512 KiB of real filesystem paths
    "cat /tmp/w4_paths_medium.txt",   # ~2.5 MiB
    "cat /tmp/w4_paths_large.txt",    # ~10 MiB
]

_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)
_ANSI_STRIP_RE = re.compile(_ANSI_SEQ)
_MARKER_SCAN_STRIP_RE = re.compile(r"[^A-Za-z0-9_]+")


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    command_id: int
    command: str
    latency_ms: float
    ttfb_ms: Optional[float]
    output_bytes: int
    throughput_kib_s: Optional[float]
    warmup: bool = False


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
    warmup: bool = False


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
    mean_ttfb_ms: Optional[float]
    median_ttfb_ms: Optional[float]
    mean_output_kib: Optional[float]
    mean_throughput_kib_s: Optional[float]


class W4Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.scenario = (args.scenario or "").strip() or "unspecified"
        self.warmup = max(0, int(args.warmup))
        if self.warmup >= args.iterations:
            raise ValueError(
                f"--warmup ({self.warmup}) must be < --iterations ({args.iterations})"
            )
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.prev_marker_tail: Optional[str] = None

        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.ttfb: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.output_sizes: Dict[str, Dict[str, List[int]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    @staticmethod
    def _build_token_re(token: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in token]
        return re.compile("".join(parts))

    @staticmethod
    def _strip_ansi_keep_newlines(text: str) -> str:
        return _ANSI_STRIP_RE.sub("", text).replace("\r", "").replace("\b", "")

    @staticmethod
    def _normalize_marker_scan(text: str) -> str:
        return _MARKER_SCAN_STRIP_RE.sub("", text).upper()

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 64, drain_timeout: float = 0.1) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=65536, timeout=drain_timeout)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    continue
                raise

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        child.expect(self.prompt_re, timeout=self.args.timeout)

    def _wait_for_marker_line(
        self, child: pexpect.spawn, marker: str, marker_tail: str, protocol: str = ""
    ) -> tuple[int, Optional[float]]:
        deadline = time.monotonic() + float(self.args.timeout)
        idle_timeout = float(self.args.command_idle_timeout)
        last_data_at = time.monotonic()
        clean_buffer = ""
        max_buffer_chars = 262_144
        output_bytes = 0
        ttfb_perf_ns: Optional[int] = None
        saw_activity = False
        marker_norm = self._normalize_marker_scan(marker)
        marker_tail_norm = self._normalize_marker_scan(marker_tail)

        def raise_timeout(reason: str) -> None:
            tail = clean_buffer[-500:]
            if "command not found" in tail.lower():
                raise ValueError(
                    f"Remote command failed before marker ({reason}): command not found. "
                    f"clean_tail={tail!r}"
                )
            raise pexpect.TIMEOUT(
                f"{reason} while waiting for marker {marker!r}. clean_tail={tail!r}"
            )

        def truncate_at(pos: int, window_bytes: int) -> int:
            # `pos` is the marker offset inside clean_buffer; add bytes of the
            # preceding text plus whatever we already accumulated from flushed lines.
            return window_bytes + len(
                clean_buffer[:pos].encode("utf-8", errors="ignore")
            )

        while True:
            now = time.monotonic()
            if now >= deadline:
                raise_timeout(f"Marker not received within {self.args.timeout:.1f}s")

            if idle_timeout > 0 and saw_activity and (now - last_data_at) >= idle_timeout:
                raise_timeout(f"No output for {idle_timeout:.1f}s")

            read_timeout = min(0.5, max(0.05, deadline - now))
            if idle_timeout > 0 and saw_activity:
                idle_left = idle_timeout - (now - last_data_at)
                read_timeout = min(read_timeout, max(0.05, idle_left))

            try:
                chunk = child.read_nonblocking(size=4096, timeout=read_timeout)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise pexpect.EOF(
                    f"EOF while waiting for marker {marker!r}. "
                    f"buffer_tail={clean_buffer[-500:]!r}"
                ) from exc
            except OSError as exc:
                if exc.errno == errno.ENOSPC:
                    continue
                raise

            if not chunk:
                continue

            if ttfb_perf_ns is None:
                ttfb_perf_ns = time.perf_counter_ns()

            last_data_at = time.monotonic()
            clean_chunk = self._strip_ansi_keep_newlines(chunk)
            if not clean_chunk:
                continue

            saw_activity = True
            clean_buffer += clean_chunk
            if len(clean_buffer) > max_buffer_chars:
                dropped = len(clean_buffer) - max_buffer_chars
                # dropped bytes are part of the delivered output; keep them in the counter
                output_bytes += len(
                    clean_buffer[:dropped].encode("utf-8", errors="ignore")
                )
                clean_buffer = clean_buffer[dropped:]

            marker_pos = clean_buffer.find(marker)
            if marker_pos >= 0:
                total_bytes = truncate_at(marker_pos, output_bytes)
                return total_bytes, _ns_to_ms(ttfb_perf_ns)
            # Prompt-marker fallback: only for Mosh, where the actual marker may
            # never arrive (SSP discards output and only syncs final screen state).
            # For SSH/SSH3 the marker always arrives; using prompt as fallback
            # causes premature return when the prior iteration's prompt leaks
            # into the buffer before _drain_pending_output clears it.
            if protocol == "mosh" and self.prompt_marker:
                prompt_pos = clean_buffer.find(self.prompt_marker)
                if prompt_pos >= 0:
                    total_bytes = truncate_at(prompt_pos, output_bytes)
                    return total_bytes, _ns_to_ms(ttfb_perf_ns)

            # Fuzzy matching only for Mosh: marker can be fragmented by screen
            # redraw / ANSI insertion. For SSH/SSH3 the marker arrives intact,
            # so fuzzy matching would cause premature exit and undercount output_bytes.
            if protocol == "mosh":
                normalized_window = self._normalize_marker_scan(clean_buffer[-8192:])
                if marker_norm in normalized_window or (
                    marker_tail_norm and marker_tail_norm in normalized_window
                ):
                    # Marker fragmented by mosh redraw / ANSI insertion. Best-effort
                    # byte count: charge everything outside the 8K fuzzy window to
                    # output, and ignore the window itself (may contain marker text).
                    window_start = max(0, len(clean_buffer) - 8192)
                    total_bytes = output_bytes + len(
                        clean_buffer[:window_start].encode("utf-8", errors="ignore")
                    )
                    return total_bytes, _ns_to_ms(ttfb_perf_ns)

            # Periodic flush: count completed lines so the buffer doesn't grow unbounded.
            lines = clean_buffer.split("\n")
            clean_buffer = lines.pop() if lines else ""
            for line in lines:
                output_bytes += len((line + "\n").encode("utf-8", errors="ignore"))

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
        search_window = (
            self.args.search_window_size if self.args.search_window_size > 0 else None
        )
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
            maxread=self.args.maxread,
            searchwindowsize=search_window,
            echo=False,
        )

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child)
        self._drain_pending_output(child)

        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF)
        except Exception:
            child.close(force=True)

    def _measure_output_delivery(
        self,
        child: pexpect.spawn,
        command: str,
        marker: str,
        marker_tail: str,
        protocol: str = "",
    ) -> tuple[float, Optional[float], int]:
        self._drain_pending_output(child)

        wrapped = f"{{ {command}; }} 2>&1; printf '%s\\n' {shlex.quote(marker)}"
        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes, ttfb_ms_relative = self._wait_for_marker_line(
            child, marker, marker_tail, protocol
        )
        end_ns = time.perf_counter_ns()

        latency_ms = (end_ns - start_ns) / 1_000_000.0
        ttfb_ms: Optional[float]
        if ttfb_ms_relative is None:
            ttfb_ms = None
        else:
            # _wait_for_marker_line returned perf_counter_ns of first byte
            # (as ms). Convert to delta from start.
            ttfb_ms = ttfb_ms_relative - (start_ns / 1_000_000.0)
            if ttfb_ms < 0:
                ttfb_ms = 0.0
        return latency_ms, ttfb_ms, output_bytes

    def _recover_after_timeout(self, child: pexpect.spawn) -> bool:
        try:
            child.sendcontrol("c")
            child.expect(self.prompt_re, timeout=min(10, self.args.timeout))
            self._drain_pending_output(child)
            return True
        except Exception:
            return False

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
            is_warmup = sample_id <= self.warmup
            marker_tail = self._next_marker_tail()
            marker = f"__W4_DONE_{trial_id}_{sample_id}_{command_id}_{marker_tail}__"
            try:
                lat, ttfb_ms, output_bytes = self._measure_output_delivery(
                    child, command, marker, marker_tail, protocol,
                )
                throughput_kib_s: Optional[float]
                if lat > 0:
                    throughput_kib_s = (output_bytes / 1024.0) / (lat / 1000.0)
                else:
                    throughput_kib_s = None
                if not is_warmup:
                    self.results[protocol][command].append(lat)
                    if ttfb_ms is not None:
                        self.ttfb[protocol][command].append(ttfb_ms)
                    self.output_sizes[protocol][command].append(output_bytes)
                self.records.append(
                    SampleRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=sample_id,
                        command_id=command_id,
                        command=command,
                        latency_ms=lat,
                        ttfb_ms=ttfb_ms,
                        output_bytes=output_bytes,
                        throughput_kib_s=throughput_kib_s,
                        warmup=is_warmup,
                    )
                )
                tag = "WARM" if is_warmup else "meas"
                ttfb_str = f"{ttfb_ms:.2f}" if ttfb_ms is not None else "N/A"
                print(
                    f"[{protocol:>4}/{command:<36}] trial {trial_id:>2}/{self.args.trials}"
                    f" {tag} {sample_id:>3}/{self.args.iterations}:"
                    f" total={lat:.2f} ms ttfb={ttfb_str} ms"
                    f" bytes={output_bytes / 1024.0:.1f} KiB",
                    flush=True,
                )
            except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                recovered = True
                if isinstance(exc, pexpect.TIMEOUT):
                    recovered = self._recover_after_timeout(child)
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
                        warmup=is_warmup,
                    )
                )
                tag = "WARM-FAIL" if is_warmup else "FAIL"
                print(
                    f"[{protocol:>4}/{command:<36}] trial {trial_id:>2}"
                    f" {tag} {sample_id:>3}: ({type(exc).__name__}: {exc})"
                    f"{'' if recovered else ' [session not recovered]'}",
                    flush=True,
                )
                if self.args.reopen_on_failure or not recovered:
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
                    f"[{protocol:>4}/{command:<36}] trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms",
                    flush=True,
                )
                try:
                    self._run_trial(
                        child, protocol, workload, command, command_id, trial_id,
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
        ttfb_data = self.ttfb[protocol][command]
        sizes = self.output_sizes[protocol][command]
        fail_n = sum(
            1
            for f in self.failures
            if f.protocol == protocol
            and f.workload == workload
            and f.command == command
            and not f.warmup
        )
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, command, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None,
                None, None, None, None,
            )

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
        mean_ttfb = statistics.mean(ttfb_data) if ttfb_data else None
        median_ttfb = statistics.median(ttfb_data) if ttfb_data else None
        mean_output_kib = statistics.mean(sizes) / 1024.0 if sizes else None
        mean_throughput_kib_s = (
            (mean_output_kib / (mean_ms / 1000.0))
            if mean_output_kib is not None and mean_ms > 0
            else None
        )
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
            mean_ttfb_ms=mean_ttfb,
            median_ttfb_ms=median_ttfb,
            mean_output_kib=mean_output_kib,
            mean_throughput_kib_s=mean_throughput_kib_s,
        )

    def summaries(self) -> List[SummaryRow]:
        return [
            self._summary_row(p, w, c)
            for p in self.args.protocols
            for w in self.args.workloads
            for c in self.args.commands
        ]

    def _session_setup_stats(self, protocol: str, command: str) -> dict:
        data = self.session_setups[protocol][command]
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

    @staticmethod
    def _display_command(command: str, width: int) -> str:
        if len(command) <= width:
            return command
        if width <= 3:
            return command[:width]
        if width <= 12:
            return command[: width - 3] + "..."
        right = 6
        left = width - 3 - right
        return f"{command[:left]}...{command[-right:]}"

    def print_report(self) -> None:
        def fmt(v: Optional[float]) -> str:
            return f"{v:.2f}" if v is not None else "N/A"

        command_w = 28
        command_labels = {
            c: self._display_command(c, command_w) for c in self.args.commands
        }

        print("\n" + "=" * 170)
        print(
            f"{'Proto':<6} | {'Workload':<11} | {'Command':<{command_w}} | {'N':>4} | {'Fail':>4} | "
            f"{'Succ%':>6} | {'Mean':>8} | {'Med':>8} | {'P95':>8} | {'P99':>8} | "
            f"{'TTFBmean':>9} | {'OutKB':>8} | {'KB/s':>8}"
        )
        print("-" * 170)
        for row in self.summaries():
            print(
                f"{row.protocol:<6} | {row.workload:<11} | {command_labels[row.command]:<{command_w}} | "
                f"{row.n:>4} | {row.failures:>4} | {row.success_rate_pct:>6.1f} | "
                f"{fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | {fmt(row.p95_ms):>8} | "
                f"{fmt(row.p99_ms):>8} | {fmt(row.mean_ttfb_ms):>9} | "
                f"{fmt(row.mean_output_kib):>8} | {fmt(row.mean_throughput_kib_s):>8}"
            )
        print("=" * 170)
        print("NOTE: Metric is Time-to-Interactive (sendline -> prompt returns).")
        print("      For Mosh, output_bytes = screen-sync diff (~0.2 KiB), NOT payload size.")
        print("      Throughput (KB/s) is valid for SSH and SSH3 only.")
        print("Command map:")
        for command in self.args.commands:
            print(f"  {command_labels[command]:<{command_w}} -> {command}")

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        line_csv = outdir / "w4_line_log.csv"
        setup_csv = outdir / "w4_session_setup.csv"
        meta_json = outdir / "w4_meta.json"

        with line_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "scenario", "protocol", "workload",
                "round_id", "sample_id", "command_id", "command",
                "latency_ms", "ttfb_ms", "output_bytes", "throughput_kib_s",
                "status", "warmup", "error_type", "error_message",
            ])
            for r in self.records:
                ttfb = f"{r.ttfb_ms:.6f}" if r.ttfb_ms is not None else ""
                thr = f"{r.throughput_kib_s:.6f}" if r.throughput_kib_s is not None else ""
                writer.writerow([
                    self.scenario, r.protocol, r.workload,
                    r.round_id, r.sample_id, r.command_id, r.command,
                    f"{r.latency_ms:.6f}", ttfb, r.output_bytes, thr,
                    "ok", "1" if r.warmup else "0", "", "",
                ])
            for r in self.failures:
                writer.writerow([
                    self.scenario, r.protocol, r.workload,
                    r.round_id, r.sample_id, r.command_id, r.command,
                    "", "", "", "",
                    "fail", "1" if r.warmup else "0", r.error_type, r.error_message,
                ])

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["scenario", "protocol", "command", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for c in self.args.commands:
                    for trial_id, ms in enumerate(self.session_setups[p][c], start=1):
                        writer.writerow([self.scenario, p, c, trial_id, f"{ms:.6f}"])

        meta = {
            "started_at_utc": self.started_at,
            "scenario": self.scenario,
            "target": self.target,
            "client_source_ip": self.args.source_ip,
            "protocols": self.args.protocols,
            "workloads": self.args.workloads,
            "commands": self.args.commands,
            "trials": self.args.trials,
            "iterations": self.args.iterations,
            "warmup": self.warmup,
            "timeout_sec": self.args.timeout,
            "random_seed": self.args.seed,
            "shuffle_pairs": bool(self.args.shuffle_pairs),
            "reopen_on_failure": bool(self.args.reopen_on_failure),
            "mosh_predict": self.args.mosh_predict,
            "metric_notes": {
                "latency_ms": (
                    "Time-to-Interactive: sendline(command) -> completion marker visible at client. "
                    "Includes server exec time (negligible for cat fixtures) + network delivery + RTT. "
                    "For Mosh: server exec + 1 RTT to sync final screen state (output bytes discarded by SSP)."
                ),
                "ttfb_ms": "sendline -> first byte of output visible at client",
                "output_bytes": (
                    "bytes seen on client-side PTY. For Mosh this is screen-sync bytes (~0.2 KiB), "
                    "not the number of bytes the command printed on the server. "
                    "Do NOT use for throughput comparison involving Mosh."
                ),
            },
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {c: self._session_setup_stats(p, c) for c in self.args.commands}
                for p in self.args.protocols
            },
        }
        meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        print(f"Saved line log CSV    : {line_csv}")
        print(f"Saved session setup   : {setup_csv}")
        print(f"Saved meta JSON       : {meta_json}")


def _ns_to_ms(ns: Optional[int]) -> Optional[float]:
    if ns is None:
        return None
    return ns / 1_000_000.0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W4 Large Output Commands benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default="192.168.8.100")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"))
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--commands", nargs="+", default=DEFAULT_COMMANDS)
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--iterations", type=int, default=20)
    p.add_argument("--warmup", type=int, default=0, help="First N samples per trial excluded from summary")
    p.add_argument("--scenario", default="", help="Network scenario label (low/medium/high)")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--command-idle-timeout", type=float, default=30.0)
    p.add_argument("--maxread", type=int, default=65535)
    p.add_argument("--search-window-size", type=int, default=8192)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-dir", default="w4_results")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH)
    p.add_argument("--ssh3-insecure", action="store_true")
    p.add_argument("--batch-mode", action="store_true")
    p.add_argument("--strict-host-key-checking", action="store_true")
    p.add_argument("--mosh-predict", default="adaptive", choices=["adaptive", "always", "never"])
    p.add_argument("--shuffle-pairs", action="store_true")
    p.add_argument("--reopen-on-failure", action="store_true")
    p.add_argument("--log-pexpect", action="store_true", help="Deprecated no-op (kept for compat)")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.command_idle_timeout < 0:
        parser.error("--command-idle-timeout must be >= 0")
    if args.maxread <= 0:
        parser.error("--maxread must be > 0")
    if args.search_window_size < 0:
        parser.error("--search-window-size must be >= 0")

    bench = W4Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
