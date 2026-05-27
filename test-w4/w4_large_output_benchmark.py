#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import random
import re
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc


DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_COMMANDS = [
    "find /",
    "git status",
    "docker logs $(docker ps -q | head -n 1)",
]
COMMAND_LABELS = {
    "find /": "find /",
    "git status": "git status",
    "docker logs $(docker ps -q | head -n 1)": "docker logs",
    "docker logs <container_name> 2>/dev/null": "docker logs",
    'cid=$(docker ps -q | head -n 1); [ -n "$cid" ] && docker logs "$cid" 2>/dev/null || true': "docker logs",
}
DEFAULT_PROMPT = "__W4_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
MARKER_TOKEN_LEN = 24
TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z]|\x1b[\(\)][0-9A-Za-z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
)
_ANSI_STRIP_RE = re.compile(_ANSI_SEQ)


def _normalize_command(command: str) -> str:
    cmd = (command or "").strip()
    if "<container_name>" in cmd:
        # Legacy placeholder breaks shell parsing because '<' is treated as
        # input-redirection. Replace it with a dynamic container-id lookup.
        cmd = cmd.replace("<container_name>", "$(docker ps -q | head -n 1)")
    return cmd


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
    received_pct: float = 100.0


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
    recv_pct_mean: Optional[float]
    recv_pct_min: Optional[float]


class W4Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.scenario = (args.scenario or "").strip() or "unspecified"
        self.target = f"{args.user}@{args.host}"
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.prev_marker_token: Optional[str] = None

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
        self.ref_output_bytes: Dict[str, int] = self._collect_reference_outputs()

    def _collect_reference_outputs(self) -> Dict[str, int]:
        ref: Dict[str, List[int]] = {cmd: [] for cmd in self.args.commands}
        n_runs = 2
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
        ]
        if self.args.identity_file:
            ssh_cmd += ["-i", self.args.identity_file]
        ssh_cmd.append(self.target)

        print("=== Collecting reference output bytes (via SSH exec, no PTY) ===", flush=True)
        for run_idx in range(1, n_runs + 1):
            for cmd in self.args.commands:
                wrapped = cmd
                if self.args.max_output_lines > 0:
                    wrapped = f"{{ {cmd}; }} 2>&1 | head -n {int(self.args.max_output_lines)}"
                try:
                    result = subprocess.run(
                        ssh_cmd + [wrapped],
                        capture_output=True, timeout=120,
                    )
                    ref[cmd].append(len(result.stdout))
                except Exception as exc:
                    print(f"  ref[{cmd}] run {run_idx} FAILED: {exc}", flush=True)
            print(f"  run {run_idx}/{n_runs} done", flush=True)

        result_dict = {}
        for cmd in self.args.commands:
            sizes = ref[cmd]
            if sizes:
                sizes.sort()
                result_dict[cmd] = sizes[len(sizes) // 2]
            else:
                result_dict[cmd] = 0
            print(f"  ref[{cmd}] = {result_dict[cmd]} bytes (samples: {sizes})", flush=True)
        print("", flush=True)
        return result_dict

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*$")

    @staticmethod
    def _build_token_re(token: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in token]
        return re.compile("".join(parts))

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return _ANSI_STRIP_RE.sub("", text).replace("\r", "").replace("\b", "")

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    @staticmethod
    def _workload_for_command(command: str) -> str:
        return COMMAND_LABELS.get(command, command)

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

    def _session_command(self, protocol: str) -> str:
        target = self.target
        ssh_common = ["ssh", "-tt"]
        if self.args.source_ip:
            ssh_common += ["-b", self.args.source_ip]
        if self.args.strict_host_key_checking:
            ssh_common += ["-o", "StrictHostKeyChecking=yes"]
        else:
            ssh_common += [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
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
        child.delaybeforesend = 0
        child.setwinsize(50, 200)

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child, protocol=protocol)
        # Keep setup timing identical to test/w3, then disable echo so command
        # lines cannot expose the end marker before the real workload finishes.
        child.sendline("stty -echo")
        self._expect_prompt(child, protocol=protocol)
        self._drain_pending_output(child)
        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=5)
        except Exception:
            child.close(force=True)

    def _next_marker_token(self) -> str:
        if self.prev_marker_token is None:
            token = "".join(random.choice(TAIL_ALPHABET) for _ in range(MARKER_TOKEN_LEN))
            self.prev_marker_token = token
            return token

        chars: List[str] = []
        for prev_ch in self.prev_marker_token:
            choices = [c for c in TAIL_ALPHABET if c != prev_ch]
            chars.append(random.choice(choices))
        token = "".join(chars)
        self.prev_marker_token = token
        return token

    def _wrap_measured_command(self, command: str, marker: str) -> str:
        marker_arg = shlex.quote(marker)
        producer = f"{{ {command}; }} 2>&1"
        if self.args.max_output_lines > 0:
            producer = f"{producer} | head -n {int(self.args.max_output_lines)}"
        return f"{producer}; printf '%s\\n' {marker_arg}"

    def _wait_for_marker(self, child: pexpect.spawn, marker: str) -> int:
        deadline = time.monotonic() + float(self.args.sample_timeout)
        idle_timeout = float(self.args.command_idle_timeout)
        marker_re = self._build_token_re(marker)
        read_size = max(4096, int(self.args.maxread))

        buffer = ""
        debug_tail = ""
        output_bytes = 0
        saw_activity = False
        last_data_at = time.monotonic()

        def timeout_error(reason: str) -> pexpect.TIMEOUT:
            return pexpect.TIMEOUT(
                f"{reason} while waiting for marker {marker!r}. "
                f"output_bytes={output_bytes}, clean_tail={debug_tail[-500:]!r}"
            )

        while True:
            now = time.monotonic()
            if now >= deadline:
                raise timeout_error(
                    f"Marker not received within {self.args.sample_timeout:.1f}s"
                )
            if idle_timeout > 0 and saw_activity and (now - last_data_at) >= idle_timeout:
                raise timeout_error(f"No output for {idle_timeout:.1f}s")

            read_timeout = min(0.5, max(0.05, deadline - now))
            if idle_timeout > 0 and saw_activity:
                idle_left = idle_timeout - (now - last_data_at)
                read_timeout = min(read_timeout, max(0.05, idle_left))

            try:
                chunk = child.read_nonblocking(size=read_size, timeout=read_timeout)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise pexpect.EOF(
                    f"EOF while waiting for marker {marker!r}. "
                    f"output_bytes={output_bytes}, clean_tail={debug_tail[-500:]!r}"
                ) from exc

            if not chunk:
                continue

            clean_chunk = self._strip_ansi(chunk)
            if not clean_chunk:
                continue

            saw_activity = True
            last_data_at = time.monotonic()
            debug_tail = (debug_tail + clean_chunk)[-500:]
            buffer += clean_chunk

            match = marker_re.search(buffer)
            if match is not None:
                return output_bytes + len(
                    buffer[: match.start()].encode("utf-8", errors="ignore")
                )

            lines = buffer.split("\n")
            buffer = lines.pop() if lines else ""
            for line in lines:
                marker_pos = line.find(marker)
                if marker_pos >= 0:
                    output_bytes += len(
                        line[:marker_pos].encode("utf-8", errors="ignore")
                    )
                    return output_bytes
                output_bytes += len((line + "\n").encode("utf-8", errors="ignore"))

            if len(buffer) > 262_144:
                dropped = buffer[:-262_144]
                output_bytes += len(dropped.encode("utf-8", errors="ignore"))
                buffer = buffer[-262_144:]

    def _measure_output_delivery(
        self,
        child: pexpect.spawn,
        command: str,
        marker: str,
    ) -> tuple[float, int]:
        self._drain_pending_output(child)
        wrapped = self._wrap_measured_command(command, marker)
        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes = self._wait_for_marker(child, marker)
        end_ns = time.perf_counter_ns()
        return (end_ns - start_ns) / 1_000_000.0, output_bytes

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
            marker = self._next_marker_token()
            try:
                latency_ms, output_bytes = self._measure_output_delivery(
                    child, command, marker
                )
                throughput = (
                    (output_bytes / 1024.0) / (latency_ms / 1000.0)
                    if latency_ms > 0
                    else None
                )
                ref_bytes = self.ref_output_bytes.get(command, 0)
                if ref_bytes > 0:
                    received_pct = min(100.0, output_bytes / ref_bytes * 100.0)
                else:
                    received_pct = 100.0

                self.results[protocol][command].append(latency_ms)
                self.output_sizes[protocol][command].append(output_bytes)
                self.records.append(
                    SampleRecord(
                        protocol,
                        workload,
                        trial_id,
                        sample_id,
                        command_id,
                        command,
                        latency_ms,
                        output_bytes,
                        throughput,
                        received_pct=received_pct,
                    )
                )
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}/{self.args.trials}"
                    f" sample {sample_id:>3}/{self.args.iterations}:"
                    f" {latency_ms:.2f} ms, {output_bytes / 1024.0:.1f} KiB"
                    f" | recv {received_pct:.1f}%",
                    flush=True,
                )
            except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                recovered = True
                if isinstance(exc, pexpect.TIMEOUT):
                    recovered = self._recover_after_timeout(child)
                self.failures.append(
                    FailureRecord(
                        protocol,
                        workload,
                        trial_id,
                        sample_id,
                        command_id,
                        command,
                        type(exc).__name__,
                        str(exc),
                    )
                )
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}"
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
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms",
                    flush=True,
                )
                try:
                    self._run_trial(child, protocol, workload, command, command_id, trial_id)
                except (pexpect.TIMEOUT, pexpect.EOF, ValueError):
                    if self.args.reopen_on_failure:
                        continue
            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
        sequence = [
            (protocol, self._workload_for_command(command), command, command_id)
            for protocol in self.args.protocols
            for command_id, command in enumerate(self.args.commands, start=1)
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
        ordered = sorted(values)
        k = (len(ordered) - 1) * (p / 100.0)
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return ordered[int(k)]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

    def _summary_row(self, protocol: str, command: str) -> SummaryRow:
        workload = self._workload_for_command(command)
        data = self.results[protocol][command]
        sizes = self.output_sizes[protocol][command]
        failures = sum(
            1
            for failure in self.failures
            if failure.protocol == protocol and failure.command == command
        )
        total = len(data) + failures
        success_rate = (100.0 * len(data) / total) if total else 0.0

        recv_data = [
            r.received_pct for r in self.records
            if r.protocol == protocol and r.command == command
        ]

        if not data:
            return SummaryRow(
                protocol,
                workload,
                command,
                0,
                failures,
                success_rate,
                None, None, None, None, None, None, None, None, None, None, None, None,
            )

        mean_ms = statistics.mean(data)
        mean_output_kib = statistics.mean(sizes) / 1024.0 if sizes else None
        mean_throughput = (
            mean_output_kib / (mean_ms / 1000.0)
            if mean_output_kib is not None and mean_ms > 0
            else None
        )
        stdev_ms = statistics.stdev(data) if len(data) > 1 else 0.0
        return SummaryRow(
            protocol,
            workload,
            command,
            len(data),
            failures,
            success_rate,
            min(data),
            mean_ms,
            statistics.median(data),
            stdev_ms,
            self._percentile(data, 95),
            self._percentile(data, 99),
            max(data),
            (1.96 * stdev_ms / math.sqrt(len(data))) if len(data) > 1 else 0.0,
            mean_output_kib,
            mean_throughput,
            recv_pct_mean=statistics.mean(recv_data) if recv_data else None,
            recv_pct_min=min(recv_data) if recv_data else None,
        )

    def summaries(self) -> List[SummaryRow]:
        return [
            self._summary_row(protocol, command)
            for protocol in self.args.protocols
            for command in self.args.commands
        ]

    def _session_setup_stats(self, protocol: str, command: str) -> dict:
        data = self.session_setups[protocol][command]
        if not data:
            return dict(n=0, min=None, mean=None, median=None, stdev=None, max=None)
        return dict(
            n=len(data),
            min=min(data),
            mean=statistics.mean(data),
            median=statistics.median(data),
            stdev=statistics.stdev(data) if len(data) > 1 else 0.0,
            max=max(data),
        )

    @staticmethod
    def _fmt(value: Optional[float]) -> str:
        return f"{value:.2f}" if value is not None else "N/A"

    def print_report(self) -> None:
        header = (
            f"{'Protocol':<8} | {'Command':<12} | {'N':>4} | {'Fail':>4} |"
            f" {'Succ%':>6} | {'Min':>8} | {'Mean':>8} | {'Median':>8} |"
            f" {'P95':>8} | {'P99':>8} | {'Max':>8} | {'OutKB':>8} | {'KB/s':>8} |"
            f" {'Recv%Avg':>8} | {'Recv%Min':>8}"
        )
        print("\n" + "=" * len(header))
        print(header)
        print("-" * len(header))
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<12} | {row.n:>4} |"
                f" {row.failures:>4} | {row.success_rate_pct:>6.1f} |"
                f" {self._fmt(row.min_ms):>8} | {self._fmt(row.mean_ms):>8} |"
                f" {self._fmt(row.median_ms):>8} | {self._fmt(row.p95_ms):>8} |"
                f" {self._fmt(row.p99_ms):>8} | {self._fmt(row.max_ms):>8} |"
                f" {self._fmt(row.mean_output_kib):>8} |"
                f" {self._fmt(row.mean_throughput_kib_s):>8} |"
                f" {self._fmt(row.recv_pct_mean):>8} | {self._fmt(row.recv_pct_min):>8}"
            )
        print("=" * len(header))

        setup_header = (
            f"{'Protocol':<8} | {'Command':<12} | {'N':>3} | {'Min':>8} |"
            f" {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'Max':>8}"
        )
        print("\nSESSION SETUP LATENCY (ms) [same logic as test/w3]")
        print(setup_header)
        print("-" * len(setup_header))
        for protocol in self.args.protocols:
            for command in self.args.commands:
                stats = self._session_setup_stats(protocol, command)
                print(
                    f"{protocol:<8} | {self._workload_for_command(command):<12} |"
                    f" {stats['n']:>3} | {self._fmt(stats['min']):>8} |"
                    f" {self._fmt(stats['mean']):>8} |"
                    f" {self._fmt(stats['median']):>8} |"
                    f" {self._fmt(stats['stdev']):>8} | {self._fmt(stats['max']):>8}"
                )
        print("Command map:")
        for command in self.args.commands:
            print(f"  {self._workload_for_command(command)} -> {command}")

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        line_csv = outdir / "w4_line_log.csv"
        setup_csv = outdir / "w4_session_setup.csv"

        with line_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "scenario",
                    "protocol",
                    "workload",
                    "round_id",
                    "sample_id",
                    "command_id",
                    "command",
                    "latency_ms",
                    "output_bytes",
                    "throughput_kib_s",
                    "received_pct",
                    "status",
                    "error_type",
                    "error_message",
                ]
            )
            for record in self.records:
                writer.writerow(
                    [
                        self.scenario,
                        record.protocol,
                        record.workload,
                        record.round_id,
                        record.sample_id,
                        record.command_id,
                        record.command,
                        f"{record.latency_ms:.6f}",
                        record.output_bytes,
                        (
                            f"{record.throughput_kib_s:.6f}"
                            if record.throughput_kib_s is not None
                            else ""
                        ),
                        f"{record.received_pct:.2f}",
                        "ok",
                        "",
                        "",
                    ]
                )
            for failure in self.failures:
                writer.writerow(
                    [
                        self.scenario,
                        failure.protocol,
                        failure.workload,
                        failure.round_id,
                        failure.sample_id,
                        failure.command_id,
                        failure.command,
                        "",
                        "",
                        "",
                        "fail",
                        failure.error_type,
                        failure.error_message,
                    ]
                )

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["scenario", "protocol", "command", "trial_id", "session_setup_ms"])
            for protocol in self.args.protocols:
                for command in self.args.commands:
                    for trial_id, setup_ms in enumerate(
                        self.session_setups[protocol][command], start=1
                    ):
                        writer.writerow(
                            [self.scenario, protocol, command, trial_id, f"{setup_ms:.6f}"]
                        )

        print(f"Saved line log CSV  : {line_csv}")
        print(f"Saved setup CSV     : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W4 real large-output benchmark")
    parser.add_argument("--host", default="192.168.8.102")
    parser.add_argument("--user", default="trungnt")
    parser.add_argument("--source-ip", default="192.168.8.100")
    parser.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "id_rsa"),
    )
    parser.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    parser.add_argument("--commands", nargs="+", default=DEFAULT_COMMANDS)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--sample-timeout", type=float, default=60.0)
    parser.add_argument("--command-idle-timeout", type=float, default=15.0)
    parser.add_argument("--max-output-lines", type=int, default=1000)
    parser.add_argument("--maxread", type=int, default=65535)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="w4_results")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
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
    parser.add_argument("--log-pexpect", action="store_true", help="Compatibility no-op")
    parser.add_argument("--scenario", default="", help="Free-form network scenario label (e.g. low/medium/high). Written to CSV/meta for later aggregation.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.sample_timeout <= 0:
        parser.error("--sample-timeout must be > 0")
    if args.command_idle_timeout < 0:
        parser.error("--command-idle-timeout must be >= 0")
    if args.max_output_lines < 0:
        parser.error("--max-output-lines must be >= 0")
    if args.maxread <= 0:
        parser.error("--maxread must be > 0")

    normalized_commands: List[str] = []
    for raw in args.commands:
        normalized = _normalize_command(raw)
        if normalized != raw:
            print(
                f"[compat] rewritten legacy command:\n"
                f"  from: {raw}\n"
                f"    to: {normalized}",
                flush=True,
            )
        normalized_commands.append(normalized)
    args.commands = normalized_commands

    benchmark = W4Benchmark(args)
    benchmark.run()
    benchmark.print_report()
    benchmark.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
