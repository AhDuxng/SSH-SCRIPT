#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shlex
import signal
import statistics
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
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["command_loop"]
DEFAULT_PROMPT = "__W1_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
DEFAULT_COMMANDS = [
    "cat /tmp/w1_fixture_small.txt",
    "cat /tmp/w1_fixture_medium.txt",
    "cat /tmp/w1_fixture_large.txt",
]
_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ANSI_STRIP_RE = re.compile(r"\x1b\[\??[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)
TAIL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MARKER_TOKEN_LEN = 24


@dataclass
class SampleRecord:
    protocol: str
    workload: str
    round_id: int
    sample_id: int
    command_id: int
    command: str
    latency_ms: float
    output_bytes: int = 0
    ref_output_bytes: int = 0
    output_delta_bytes: int = 0
    expected_sha256: str = ""
    received_sha256: str = ""
    content_match: bool = False
    received_pct: float = 100.0
    residual_bytes: int = 0
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
    recv_pct_mean: Optional[float]
    recv_pct_min: Optional[float]
    content_ok_pct: Optional[float]
    content_bad: int


class W1Benchmark:
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
        # Prompt bytes can be split by ANSI redraw sequences (esp. over mosh).
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.prev_marker_token: Optional[str] = None

        self.records: List[SampleRecord] = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.ref_output_sha256: Dict[str, str] = {}
        self.ref_output_bytes: Dict[str, int] = self._collect_reference_outputs()
        self._init_incremental_outputs()

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        child.expect(self.prompt_re, timeout=self.args.timeout)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return _ANSI_STRIP_RE.sub("", text).replace("\b", "")

    def _extract_output_lines(self, raw: str, command: str) -> List[str]:
        cleaned = self._strip_ansi(raw)
        lines = cleaned.split("\n")
        if lines and command in lines[0]:
            lines = lines[1:]
        return [l for l in lines if l.strip()]

    def _ssh_exec_command(self) -> List[str]:
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes",
        ]
        if self.args.source_ip:
            ssh_cmd += ["-b", self.args.source_ip]
        if self.args.identity_file:
            ssh_cmd += ["-i", self.args.identity_file]
        ssh_cmd.append(self.target)
        return ssh_cmd

    def _fixture_ref_command(self, command: str) -> Optional[str]:
        try:
            parts = shlex.split(command)
        except ValueError:
            return None
        if len(parts) != 2 or parts[0] != "cat":
            return None
        path = parts[1]
        if not path.startswith("/"):
            return None
        quoted = shlex.quote(path)
        return f"""
            test -r {quoted} || {{ echo 'missing fixture: {quoted}' >&2; exit 66; }}
            bytes=$(wc -c < {quoted})
            hash=$(sha256sum {quoted} | awk '{{print $1}}')
            printf '%s %s\\n' "$bytes" "$hash"
        """

    def _collect_reference_outputs(self) -> Dict[str, int]:
        ref: Dict[str, List[int]] = {cmd: [] for cmd in self.args.commands}
        n_runs = 3
        ssh_cmd = self._ssh_exec_command()

        print("=== Collecting reference output bytes (via SSH exec, no PTY) ===", flush=True)
        for cmd in self.args.commands:
            fixture_ref = self._fixture_ref_command(cmd)
            if fixture_ref is not None:
                try:
                    result = subprocess.run(
                        ssh_cmd + [fixture_ref],
                        capture_output=True,
                        timeout=30,
                        text=True,
                    )
                    if result.returncode != 0:
                        raise RuntimeError(result.stderr.strip())
                    parts = result.stdout.strip().split()
                    ref[cmd].append(int(parts[0]))
                    self.ref_output_sha256[cmd] = parts[1]
                except Exception as exc:
                    raise RuntimeError(
                        "Failed to read static fixture reference for "
                        f"{cmd!r}. Run setup_w1_fixtures.sh on the server first. "
                        f"error={exc}"
                    ) from exc
                print(
                    f"  ref[{cmd}] = {ref[cmd][0]} bytes, sha256={self.ref_output_sha256[cmd]}",
                    flush=True,
                )
                continue

            for run_idx in range(1, n_runs + 1):
                try:
                    result = subprocess.run(
                        ssh_cmd + [cmd],
                        capture_output=True, timeout=60,
                    )
                    ref[cmd].append(len(result.stdout))
                    if cmd not in self.ref_output_sha256:
                        self.ref_output_sha256[cmd] = hashlib.sha256(result.stdout).hexdigest()
                except Exception as exc:
                    print(f"  ref[{cmd}] run {run_idx} FAILED: {exc}", flush=True)
                print(f"  ref[{cmd}] run {run_idx}/{n_runs} done", flush=True)

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
    def _fsync_csv_row(path: Path, row: List[object]) -> None:
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())

    def _init_incremental_outputs(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)
        self.line_csv = outdir / "w1_line_log.csv"
        self.setup_csv = outdir / "w1_session_setup.csv"

        with self.line_csv.open("w", newline="", encoding="utf-8") as f:
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
                    "ref_output_bytes",
                    "output_delta_bytes",
                    "expected_sha256",
                    "received_sha256",
                    "content_match",
                    "received_pct",
                    "residual_bytes",
                    "status",
                    "warmup",
                    "error_type",
                    "error_message",
                ]
            )
            f.flush()
            os.fsync(f.fileno())

        with self.setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["scenario", "protocol", "command", "trial_id", "session_setup_ms"]
            )
            f.flush()
            os.fsync(f.fileno())

    def _append_record_csv(self, r: SampleRecord) -> None:
        self._fsync_csv_row(
            self.line_csv,
            [
                self.scenario,
                r.protocol,
                r.workload,
                r.round_id,
                r.sample_id,
                r.command_id,
                r.command,
                f"{r.latency_ms:.6f}",
                r.output_bytes,
                r.ref_output_bytes,
                r.output_delta_bytes,
                r.expected_sha256,
                r.received_sha256,
                "true" if r.content_match else "false",
                f"{r.received_pct:.2f}",
                r.residual_bytes,
                "ok",
                "1" if r.warmup else "0",
                "",
                "",
            ],
        )

    def _append_failure_csv(self, r: FailureRecord) -> None:
        self._fsync_csv_row(
            self.line_csv,
            [
                self.scenario,
                r.protocol,
                r.workload,
                r.round_id,
                r.sample_id,
                r.command_id,
                r.command,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "fail",
                "1" if r.warmup else "0",
                r.error_type,
                r.error_message,
            ],
        )

    def _append_setup_csv(
        self, protocol: str, command: str, trial_id: int, setup_ms: float
    ) -> None:
        self._fsync_csv_row(
            self.setup_csv,
            [self.scenario, protocol, command, trial_id, f"{setup_ms:.6f}"],
        )

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    @staticmethod
    def _build_token_re(token: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in token]
        return re.compile("".join(parts))

    @staticmethod
    def _payload_bytes(text: str) -> bytes:
        return text.encode("utf-8", errors="ignore")

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

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 64) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    @staticmethod
    def _measure_residual_bytes(child: pexpect.spawn, settle_ms: float = 100.0) -> int:
        """Read bytes that arrive AFTER prompt was matched (output still in-flight)."""
        total = 0
        deadline = time.perf_counter() + settle_ms / 1000.0
        while time.perf_counter() < deadline:
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.01)
                total += len(chunk.encode("utf-8", errors="ignore")) if isinstance(chunk, str) else len(chunk)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break
        return total

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
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
        )
        # Set PTY window size so programs using ioctl(TIOCGWINSZ) (e.g. ps aux)
        # report the same column width as the no-PTY reference measurement.
        child.setwinsize(50, 200)

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child)
        child.sendline("export COLUMNS=200")
        self._expect_prompt(child)
        child.sendline("stty -echo")
        self._expect_prompt(child)
        self._drain_pending_output(child)
        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
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

    def _wrap_measured_command(self, command: str, start_marker: str, end_marker: str) -> str:
        start_arg = shlex.quote(start_marker)
        end_arg = shlex.quote(end_marker)
        return f"printf '%s\\n' {start_arg}; {{ {command}; }} 2>&1; printf '\\n%s\\n' {end_arg}"

    def _wait_for_marker(
        self,
        child: pexpect.spawn,
        start_marker: str,
        end_marker: str,
    ) -> tuple[int, str]:
        start_re = self._build_token_re(start_marker)
        end_re = self._build_token_re(end_marker)
        deadline = time.monotonic() + float(self.args.timeout)
        buffer = ""
        output_bytes = 0
        output_hash = hashlib.sha256()
        capturing = False

        while True:
            now = time.monotonic()
            if now >= deadline:
                raise pexpect.TIMEOUT(
                    f"Marker not received within {self.args.timeout:.1f}s while "
                    f"waiting for {end_marker!r}. output_bytes={output_bytes}, "
                    f"clean_tail={buffer[-500:]!r}"
                )

            try:
                chunk = child.read_nonblocking(size=4096, timeout=min(0.5, deadline - now))
            except pexpect.TIMEOUT:
                continue

            clean_chunk = self._strip_ansi(chunk)
            if not clean_chunk:
                continue
            buffer += clean_chunk

            if not capturing:
                start_match = start_re.search(buffer)
                if start_match is None:
                    if len(buffer) > 4096:
                        buffer = buffer[-4096:]
                    continue
                buffer = buffer[start_match.end() :]
                if buffer.startswith("\n"):
                    buffer = buffer[1:]
                capturing = True

            end_match = end_re.search(buffer)
            if end_match is not None:
                final_text = buffer[: end_match.start()]
                if final_text.endswith("\n"):
                    final_text = final_text[:-1]
                final_bytes = self._payload_bytes(final_text)
                output_hash.update(final_bytes)
                return output_bytes + len(final_bytes), output_hash.hexdigest()

            if capturing and len(buffer) > 262_144:
                dropped = buffer[:-262_144]
                dropped_bytes = self._payload_bytes(dropped)
                output_hash.update(dropped_bytes)
                output_bytes += len(dropped_bytes)
                buffer = buffer[-262_144:]

    def _measure_command_completion(
        self,
        child: pexpect.spawn,
        command: str,
    ) -> tuple[float, float, int, int, int, int, str, str, bool]:
        self._drain_pending_output(child)
        marker = self._next_marker_token()
        start_marker = f"W1_BEGIN_{marker}"
        end_marker = f"W1_END_{marker}"
        wrapped = self._wrap_measured_command(command, start_marker, end_marker)

        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes, received_sha256 = self._wait_for_marker(
            child, start_marker, end_marker
        )
        end_ns = time.perf_counter_ns()
        latency_ms = (end_ns - start_ns) / 1_000_000.0

        ref_bytes = self.ref_output_bytes.get(command, 0)
        expected_sha256 = self.ref_output_sha256.get(command, "")
        output_delta_bytes = output_bytes - ref_bytes
        if ref_bytes > 0:
            received_pct = min(100.0, output_bytes / ref_bytes * 100.0)
        else:
            received_pct = 100.0
        content_match = (
            bool(expected_sha256)
            and expected_sha256 == received_sha256
            and output_delta_bytes == 0
        )

        residual_bytes = self._measure_residual_bytes(child)

        return (
            latency_ms,
            received_pct,
            residual_bytes,
            output_bytes,
            ref_bytes,
            output_delta_bytes,
            expected_sha256,
            received_sha256,
            content_match,
        )

    # def _run_trial(
    #     self,
    #     child: pexpect.spawn,
    #     protocol: str,
    #     workload: str,
    #     command: str,
    #     command_id: int,
    #     trial_id: int,
    # ) -> None:
    #     for sample_id in range(1, self.args.iterations + 1):
    #         is_warmup = sample_id <= self.warmup
    #         marker_tail = self._next_marker_tail()
    #         marker = f"__W1_DONE_{trial_id}_{sample_id}_{command_id}_{marker_tail}__"
    #         try:
    #             lat = self._measure_command_completion(
    #                 child,
    #                 command,
    #                 marker,
    #                 marker_tail,
    #             )
    #             if not is_warmup:
    #                 self.results[protocol][command].append(lat)
    #             self.records.append(
    #                 SampleRecord(
    #                     protocol,
    #                     workload,
    #                     trial_id,
    #                     sample_id,
    #                     command_id,
    #                     command,
    #                     lat,
    #                     warmup=is_warmup,
    #                 )
    #             )
    #             tag = "WARM" if is_warmup else "meas"
    #             print(
    #                 f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
    #                 f" {tag} {sample_id:>3}/{self.args.iterations}: {lat:.2f} ms",
    #                 flush=True,
    #             )
    #         except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
    #             self.failures.append(
    #                 FailureRecord(
    #                     protocol=protocol,
    #                     workload=workload,
    #                     round_id=trial_id,
    #                     sample_id=sample_id,
    #                     command_id=command_id,
    #                     command=command,
    #                     error_type=type(exc).__name__,
    #                     error_message=str(exc),
    #                     warmup=is_warmup,
    #                 )
    #             )
    #             tag = "WARM-FAIL" if is_warmup else "FAIL"
    #             print(
    #                 f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}"
    #                 f" {tag} {sample_id:>3}: ({type(exc).__name__}: {exc})",
    #                 flush=True,
    #             )
    #             if self.args.reopen_on_failure:
    #                 raise

    def _run_session_group(
        self,
        protocol: str,
        workload: str,
        command: str,
        command_id: int,
    ) -> None:
        for trial_id in range(1, self.args.trials + 1):
            sample_id = 1
            while sample_id <= self.args.iterations:
                child: Optional[pexpect.spawn] = None
                try:
                    child, setup_ms = self._open_session(protocol)
                    if sample_id == 1:
                        self.session_setups[protocol][command].append(setup_ms)
                        self._append_setup_csv(protocol, command, trial_id, setup_ms)
                    print(
                        f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
                        f" session_setup={setup_ms:.1f} ms (resuming from sample {sample_id})",
                        flush=True,
                    )
                    
                    # Chạy các samples còn lại của trial
                    while sample_id <= self.args.iterations:
                        is_warmup = sample_id <= self.warmup

                        (
                            lat,
                            received_pct,
                            residual_bytes,
                            output_bytes,
                            ref_bytes,
                            output_delta_bytes,
                            expected_sha256,
                            received_sha256,
                            content_match,
                        ) = self._measure_command_completion(child, command)

                        if self.args.min_recv_pct > 0 and received_pct < self.args.min_recv_pct:
                            tag = "WARM-SKIP" if is_warmup else "SKIP"
                            print(
                                f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
                                f" {tag} {sample_id:>3}/{self.args.iterations}: {lat:.2f} ms | recv {received_pct:.1f}% < {self.args.min_recv_pct}% (residual {residual_bytes}B)",
                                flush=True,
                            )
                            sample_id += 1
                            continue

                        if not is_warmup:
                            self.results[protocol][command].append(lat)
                        record = SampleRecord(
                            protocol, workload, trial_id, sample_id,
                            command_id, command, lat,
                            output_bytes=output_bytes,
                            ref_output_bytes=ref_bytes,
                            output_delta_bytes=output_delta_bytes,
                            expected_sha256=expected_sha256,
                            received_sha256=received_sha256,
                            content_match=content_match,
                            received_pct=received_pct,
                            residual_bytes=residual_bytes, warmup=is_warmup,
                        )
                        self.records.append(record)
                        self._append_record_csv(record)
                        tag = "WARM" if is_warmup else "meas"
                        print(
                            f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}/{self.args.trials}"
                            f" {tag} {sample_id:>3}/{self.args.iterations}: {lat:.2f} ms"
                            f" | recv {received_pct:.1f}% | residual {residual_bytes}B",
                            f" | content {'ok' if content_match else 'BAD'}",
                            flush=True,
                        )
                        sample_id += 1

                except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                    failure = FailureRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=sample_id,
                        command_id=command_id,
                        command=command,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                        warmup=(sample_id <= self.warmup),
                    )
                    self.failures.append(failure)
                    self._append_failure_csv(failure)
                    tag = "WARM-FAIL" if (sample_id <= self.warmup) else "FAIL"
                    print(
                        f"[{protocol:>4}/{command:<18}] trial {trial_id:>2}"
                        f" {tag} {sample_id:>3}: ({type(exc).__name__}: {exc})",
                        flush=True,
                    )
                    
                    if not self.args.reopen_on_failure:
                        break # Bỏ qua trial này nếu không bật reopen_on_failure
                    # Nếu bật reopen, vòng lặp while bên ngoài sẽ mở lại child và tiếp tục tại sample_id hiện tại
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

        recv_data = [
            r.received_pct for r in self.records
            if r.protocol == protocol and r.workload == workload
            and r.command == command and not r.warmup
        ]
        content_data = [
            r.content_match for r in self.records
            if r.protocol == protocol and r.workload == workload
            and r.command == command and not r.warmup
        ]
        content_bad = sum(1 for ok in content_data if not ok)
        content_ok_pct = (
            100.0 * (len(content_data) - content_bad) / len(content_data)
            if content_data
            else None
        )

        if n == 0:
            return SummaryRow(
                protocol, workload, command, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None, None, None,
                None, 0,
            )

        mean_ms = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms = statistics.stdev(data) if n > 1 else 0.0
        ci95 = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
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
            recv_pct_mean=statistics.mean(recv_data) if recv_data else None,
            recv_pct_min=min(recv_data) if recv_data else None,
            content_ok_pct=content_ok_pct,
            content_bad=content_bad,
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

    def print_report(self) -> None:
        def fmt(v: Optional[float]) -> str:
            return f"{v:.2f}" if v is not None else "N/A"

        width = 216
        print("\n" + "=" * width)
        print(
            f"{'Protocol':<8} | {'Workload':<12} | {'Command':<26} | {'N':>4} | {'Fail':>4} | {'Success%':>8} | "
            f"{'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+/-':>9} | "
            f"{'Recv%Avg':>8} | {'Recv%Min':>8} | {'ContentOK%':>10} | {'BadHash':>7}"
        )
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<12} | {row.command:<26} | {row.n:>4} | {row.failures:>4} | "
                f"{row.success_rate_pct:>8.1f} | {fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} | {fmt(row.median_ms):>8} | "
                f"{fmt(row.stdev_ms):>8} | {fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} | {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9} | "
                f"{fmt(row.recv_pct_mean):>8} | {fmt(row.recv_pct_min):>8} | {fmt(row.content_ok_pct):>10} | {row.content_bad:>7}"
            )
        print("=" * width)

        ss_width = 98
        print("\n" + "-" * ss_width)
        print(
            "SESSION SETUP LATENCY (ms)  "
            "[spawn -> first shell prompt, PS1 export excluded]"
        )
        print(
            f"{'Protocol':<8} | {'Command':<26} | {'N':>3} |"
            f" {'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} | {'Max':>8}"
        )
        print("-" * ss_width)
        for protocol in self.args.protocols:
            for command in self.args.commands:
                s = self._session_setup_stats(protocol, command)
                print(
                    f"{protocol:<8} | {command:<26} | {s['n']:>3} |"
                    f" {fmt(s['min']):>8} | {fmt(s['mean']):>8} |"
                    f" {fmt(s['median']):>8} | {fmt(s['stdev']):>8} |"
                    f" {fmt(s['max']):>8}"
                )
        print("-" * ss_width)

    def export(self) -> None:
        outdir = Path(self.args.output_dir)
        outdir.mkdir(parents=True, exist_ok=True)

        line_csv = outdir / "w1_line_log.csv"
        setup_csv = outdir / "w1_session_setup.csv"
        meta_json = outdir / "w1_meta.json"
        line_tmp = line_csv.with_suffix(line_csv.suffix + ".tmp")
        setup_tmp = setup_csv.with_suffix(setup_csv.suffix + ".tmp")
        meta_tmp = meta_json.with_suffix(meta_json.suffix + ".tmp")

        with line_tmp.open("w", newline="", encoding="utf-8") as f:
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
                    "ref_output_bytes",
                    "output_delta_bytes",
                    "expected_sha256",
                    "received_sha256",
                    "content_match",
                    "received_pct",
                    "residual_bytes",
                    "status",
                    "warmup",
                    "error_type",
                    "error_message",
                ]
            )
            for r in self.records:
                writer.writerow(
                    [
                        self.scenario,
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        r.command_id,
                        r.command,
                        f"{r.latency_ms:.6f}",
                        r.output_bytes,
                        r.ref_output_bytes,
                        r.output_delta_bytes,
                        r.expected_sha256,
                        r.received_sha256,
                        "true" if r.content_match else "false",
                        f"{r.received_pct:.2f}",
                        r.residual_bytes,
                        "ok",
                        "1" if r.warmup else "0",
                        "",
                        "",
                    ]
                )
            for r in self.failures:
                writer.writerow(
                    [
                        self.scenario,
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        r.command_id,
                        r.command,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "fail",
                        "1" if r.warmup else "0",
                        r.error_type,
                        r.error_message,
                    ]
                )
            f.flush()
            os.fsync(f.fileno())
        os.replace(line_tmp, line_csv)

        with setup_tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["scenario", "protocol", "command", "trial_id", "session_setup_ms"]
            )
            for p in self.args.protocols:
                for c in self.args.commands:
                    for trial_id, ms in enumerate(self.session_setups[p][c], start=1):
                        writer.writerow(
                            [self.scenario, p, c, trial_id, f"{ms:.6f}"]
                        )
            f.flush()
            os.fsync(f.fileno())
        os.replace(setup_tmp, setup_csv)

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
            "min_recv_pct": self.args.min_recv_pct,
            "mosh_predict": self.args.mosh_predict,
            "summary": [asdict(row) for row in self.summaries()],
            "session_setup": {
                p: {c: self._session_setup_stats(p, c) for c in self.args.commands}
                for p in self.args.protocols
            },
        }
        with meta_tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(meta, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(meta_tmp, meta_json)

        print(f"Saved line log CSV    : {line_csv}")
        print(f"Saved session setup   : {setup_csv}")
        print(f"Saved meta JSON       : {meta_json}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W1 Command Loop benchmark")
    p.add_argument("--host", default="192.168.8.102", help="Target host IP or hostname")
    p.add_argument("--user", default="trungnt", help="Remote username")
    p.add_argument("--source-ip", default="192.168.8.100", help="Client source IP for SSH / Mosh where supported")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"), help="SSH private key path")
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--commands", nargs="+", default=DEFAULT_COMMANDS, help="Commands executed sequentially in each sample")
    p.add_argument("--trials", type=int, default=15, help="Independent sessions per protocol/workload pair")
    p.add_argument("--iterations", type=int, default=100, help="Recorded command-loop samples per trial")
    p.add_argument("--warmup", type=int, default=0, help="First N samples per trial are measured but excluded from summary (flagged warmup=1 in CSV)")
    p.add_argument("--scenario", default="", help="Free-form network scenario label (e.g. low/medium/high). Written to CSV/meta for later aggregation.")
    p.add_argument("--timeout", type=int, default=20, help="pexpect timeout in seconds")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--output-dir", default="w1_results", help="Directory for JSON/CSV outputs")
    p.add_argument("--prompt", default=DEFAULT_PROMPT, help="Unique shell prompt marker used after session is ready")
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH, help="SSH3 terminal path suffix")
    p.add_argument("--ssh3-insecure", action="store_true", help="Pass -insecure to ssh3")
    p.add_argument("--batch-mode", action="store_true", help="Enable BatchMode for SSHv2 / Mosh bootstrap SSH")
    p.add_argument("--strict-host-key-checking", action="store_true", help="Keep strict host key checking enabled")
    p.add_argument("--mosh-predict", default="adaptive", choices=["adaptive", "always", "never"], help="Mosh prediction mode")
    p.add_argument("--shuffle-pairs", action="store_true", help="Shuffle protocol/workload execution order")
    p.add_argument("--reopen-on-failure", action="store_true", help="Reopen session after failure")
    p.add_argument("--min-recv-pct", type=float, default=0.0, help="Discard samples with received_pct below this threshold (0=disabled). Use 95.0 to ensure output completeness.")
    p.add_argument("--log-pexpect", action="store_true", help="Deprecated compatibility flag (no-op): pexpect logs are disabled")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")

    interrupted = False

    def _handle_stop(signum, _frame) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handle_stop)
        except (AttributeError, ValueError):
            pass

    bench = W1Benchmark(args)
    try:
        bench.run()
    except KeyboardInterrupt as exc:
        interrupted = True
        print(f"\nInterrupted: {exc}. Exporting partial in-memory summary...", flush=True)
    finally:
        bench.print_report()
        bench.export()
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
