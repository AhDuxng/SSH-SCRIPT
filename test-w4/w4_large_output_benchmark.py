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
DEFAULT_PROMPT = "__W4_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
MARKER_TAIL_LEN = 12
_TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DEFAULT_COMMANDS = [
    "find /",
    "git status",
    "docker logs $(docker ps -q | head -n 1)",
]

_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)
_ANSI_STRIP_RE = re.compile(_ANSI_SEQ)


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    command_id: int
    command: str
    latency_ms: float
    output_bytes: int
    throughput_kib_s: Optional[float]


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
    mean_output_kib: Optional[float]
    mean_throughput_kib_s: Optional[float]


class W4Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
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
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        child.expect(self.prompt_re, timeout=self.args.timeout)

    def _wait_for_marker_line(self, child: pexpect.spawn, marker: str, marker_tail: str) -> int:
        _ = marker_tail
        deadline = time.monotonic() + float(self.args.timeout)
        idle_timeout = float(self.args.command_idle_timeout)
        last_data_at = time.monotonic()
        clean_buffer = ""
        max_buffer_chars = 262_144
        output_bytes = 0
        saw_activity = False

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

            if not chunk:
                continue

            last_data_at = time.monotonic()
            clean_chunk = self._strip_ansi_keep_newlines(chunk)
            if not clean_chunk:
                continue

            saw_activity = True
            clean_buffer += clean_chunk
            if len(clean_buffer) > max_buffer_chars:
                clean_buffer = clean_buffer[-max_buffer_chars:]

            lines = clean_buffer.split("\n")
            clean_buffer = lines.pop() if lines else ""
            for line in lines:
                stripped = line.strip()
                if stripped == marker:
                    return output_bytes
                marker_pos = line.find(marker)
                if marker_pos >= 0:
                    output_bytes += len(line[:marker_pos].encode("utf-8", errors="ignore"))
                    return output_bytes
                output_bytes += len((line + "\n").encode("utf-8", errors="ignore"))

            marker_pos = clean_buffer.find(marker)
            if marker_pos >= 0:
                output_bytes += len(clean_buffer[:marker_pos].encode("utf-8", errors="ignore"))
                return output_bytes

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
        child.sendline("stty -echo")
        self._expect_prompt(child)
    
        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("stty echo >/dev/null 2>&1 || true")
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

    def _measure_output_delivery(
        self,
        child: pexpect.spawn,
        command: str,
        marker: str,
        marker_tail: str,
    ) -> tuple[float, int]:
        _ = marker_tail
        wrapped = f"{{ {command}; }} 2>&1; printf '%s\\n' {shlex.quote(marker)}"

        self._drain_pending_output(child)

        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes = self._wait_for_marker_line(child, marker, marker_tail)
        end_ns = time.perf_counter_ns()

        latency_ms = (end_ns - start_ns) / 1_000_000.0
        return latency_ms, output_bytes

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
            marker_tail = self._next_marker_tail()
            marker = f"__W4_DONE_{trial_id}_{sample_id}_{command_id}_{marker_tail}__"
            try:
                lat, output_bytes = self._measure_output_delivery(
                    child,
                    command,
                    marker,
                    marker_tail,
                )
                throughput_kib_s: Optional[float]
                if lat > 0:
                    throughput_kib_s = (output_bytes / 1024.0) / (lat / 1000.0)
                else:
                    throughput_kib_s = None
                self.results[protocol][command].append(lat)
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
                        output_bytes=output_bytes,
                        throughput_kib_s=throughput_kib_s,
                    )
                )
                print(
                    f"[{protocol:>4}/{command:<36}] trial {trial_id:>2}/{self.args.trials}"
                    f" sample {sample_id:>3}/{self.args.iterations}: {lat:.2f} ms,"
                    f" {output_bytes / 1024.0:.1f} KiB",
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
                    )
                )
                print(
                    f"[{protocol:>4}/{command:<36}] trial {trial_id:>2}"
                    f" sample {sample_id:>3}: FAIL ({type(exc).__name__}: {exc})"
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
        sizes = self.output_sizes[protocol][command]
        fail_n = sum(
            1
            for f in self.failures
            if f.protocol == protocol and f.workload == workload and f.command == command
        )
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, command, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None, None, None,
            )

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
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

        proto_w = 8
        workload_w = 11
        command_w = 24
        command_labels = {
            c: self._display_command(c, command_w) for c in self.args.commands
        }

        summary_header = (
            f"{'Protocol':<{proto_w}} | {'Workload':<{workload_w}} | {'Command':<{command_w}} | "
            f"{'N':>4} | {'Fail':>4} | {'Succ%':>6} | {'Min':>7} | {'Mean':>7} | {'Med':>7} | "
            f"{'Std':>7} | {'P95':>7} | {'P99':>7} | {'Max':>7} | {'CI95':>7} | {'OutKB':>7} | {'KB/s':>7}"
        )
        width = len(summary_header)
        print("\n" + "=" * width)
        print(summary_header)
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<{proto_w}} | {row.workload:<{workload_w}} | {command_labels[row.command]:<{command_w}} | "
                f"{row.n:>4} | {row.failures:>4} | {row.success_rate_pct:>6.1f} | {fmt(row.min_ms):>7} | {fmt(row.mean_ms):>7} | "
                f"{fmt(row.median_ms):>7} | {fmt(row.stdev_ms):>7} | {fmt(row.p95_ms):>7} | {fmt(row.p99_ms):>7} | "
                f"{fmt(row.max_ms):>7} | {fmt(row.ci95_half_width_ms):>7} | {fmt(row.mean_output_kib):>7} | "
                f"{fmt(row.mean_throughput_kib_s):>7}"
            )
        print("=" * width)

        setup_header = (
            f"{'Protocol':<{proto_w}} | {'Command':<{command_w}} | {'N':>3} |"
            f" {'Min':>7} | {'Mean':>7} | {'Median':>7} | {'Std':>7} | {'Max':>7}"
        )
        ss_width = len(setup_header)
        print("\n" + "-" * ss_width)
        print(
            "SESSION SETUP LATENCY (ms)  "
            "[spawn -> first shell prompt, PS1 export excluded]"
        )
        print(setup_header)
        print("-" * ss_width)
        for protocol in self.args.protocols:
            for command in self.args.commands:
                s = self._session_setup_stats(protocol, command)
                print(
                    f"{protocol:<{proto_w}} | {command_labels[command]:<{command_w}} | {s['n']:>3} |"
                    f" {fmt(s['min']):>7} | {fmt(s['mean']):>7} |"
                    f" {fmt(s['median']):>7} | {fmt(s['stdev']):>7} |"
                    f" {fmt(s['max']):>7}"
                )
        print("-" * ss_width)
        print("Command map:")
        for command in self.args.commands:
            label = command_labels[command]
            print(f"  {label:<{command_w}} -> {command}")

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        summary_json = outdir / "w4_summary.json"
        raw_csv = outdir / "w4_raw_samples.csv"
        failures_csv = outdir / "w4_failures.csv"
        setup_csv = outdir / "w4_session_setup.csv"

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
                "command_idle_timeout_sec": self.args.command_idle_timeout,
                "maxread_bytes": self.args.maxread,
                "search_window_size": self.args.search_window_size,
                "random_seed": self.args.seed,
                "topology": {
                    "client": "192.168.8.100",
                    "server": self.args.host,
                },
                "metric_name": "output_delivery_latency_ms",
                "metric_note": (
                    "Latency = time from sendline(command) to unique completion marker "
                    "visibility after command output flush. Commands are wrapped as "
                    "'{ command; } 2>&1; echo <marker>'."
                ),
                "additional_fields": {
                    "output_bytes": "Byte length observed before completion marker",
                    "throughput_kib_s": "output_bytes / latency window",
                },
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
            writer.writerow(
                [
                    "protocol",
                    "workload",
                    "round_id",
                    "sample_id",
                    "command_id",
                    "command",
                    "latency_ms",
                    "output_bytes",
                    "throughput_kib_s",
                ]
            )
            for r in self.records:
                throughput = f"{r.throughput_kib_s:.6f}" if r.throughput_kib_s is not None else ""
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        r.command_id,
                        r.command,
                        f"{r.latency_ms:.6f}",
                        r.output_bytes,
                        throughput,
                    ]
                )

        with failures_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "protocol",
                    "workload",
                    "round_id",
                    "sample_id",
                    "command_id",
                    "command",
                    "error_type",
                    "error_message",
                ]
            )
            for r in self.failures:
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        r.command_id,
                        r.command,
                        r.error_type,
                        r.error_message,
                    ]
                )

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["protocol", "command", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for c in self.args.commands:
                    for trial_id, ms in enumerate(self.session_setups[p][c], start=1):
                        writer.writerow([p, c, trial_id, f"{ms:.6f}"])

        print(f"Saved summary JSON    : {summary_json}")
        print(f"Saved raw samples CSV : {raw_csv}")
        print(f"Saved failures CSV    : {failures_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W4 Large Output Commands benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default="192.168.8.100", help="Client source IP for SSH / Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--commands", nargs="+", default=DEFAULT_COMMANDS, help="Large-output commands executed in each sample")
    p.add_argument("--trials", type=int, default=10, help="Independent sessions per protocol/workload pair")
    p.add_argument("--iterations", type=int, default=20, help="Recorded command samples per trial")
    p.add_argument("--timeout", type=int, default=300, help="pexpect timeout in seconds")
    p.add_argument("--command-idle-timeout", type=float, default=30.0, help="Fail command only if no output is observed for this many seconds (0 = disable idle timeout)")
    p.add_argument("--maxread", type=int, default=65535, help="pexpect maxread bytes")
    p.add_argument("--search-window-size", type=int, default=8192, help="pexpect search window size (0 = unlimited)")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="w4_results", help="Directory for JSON/CSV outputs")
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
