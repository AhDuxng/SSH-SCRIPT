#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc


DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_COMMANDS = [
    "cat /tmp/w4_paths_100kb.txt",
]
COMMAND_LABELS = {
    "cat /tmp/w4_paths_500b.txt": "fixture 500b",
    "cat /tmp/w4_paths_100kb.txt": "fixture 100kb",
    "cat /tmp/w4_paths_small.txt": "fixture small",
    "cat /tmp/w4_paths_2mb.txt": "fixture 2mb",
    "find /": "find /",
    "docker logs $(docker ps -q | head -n 1)": "docker logs",
    "docker logs <container_name> 2>/dev/null": "docker logs",
    'cid=$(docker ps -q | head -n 1); [ -n "$cid" ] && docker logs "$cid" 2>/dev/null || true': "docker logs",
    "ps aux": "ps aux",
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
    ref_output_bytes: int = 0
    output_delta_bytes: int = 0
    expected_sha256: str = ""
    received_sha256: str = ""
    content_match: bool = False
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
    content_ok_pct: Optional[float]
    content_bad: int


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
        self.completed_samples: Set[Tuple[str, str, int, int]] = set()
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.output_sizes: Dict[str, Dict[str, List[int]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {c: [] for c in args.commands} for p in args.protocols
        }
        self.ref_output_sha256: Dict[str, str] = {}
        self.ref_output_bytes: Dict[str, int] = self._collect_reference_outputs()
        self._init_incremental_outputs()

    @staticmethod
    def _sample_key(
        protocol: str, command: str, trial_id: int, sample_id: int
    ) -> Tuple[str, str, int, int]:
        return (protocol, command, int(trial_id), int(sample_id))

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
        if self.args.max_output_lines > 0:
            return None
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
        n_runs = 2
        ssh_cmd = self._ssh_exec_command()

        print("=== Collecting reference output bytes ===", flush=True)
        for cmd in self.args.commands:
            fixture_ref = self._fixture_ref_command(cmd)
            if fixture_ref is not None:
                result = subprocess.run(
                    ssh_cmd + [fixture_ref],
                    capture_output=True,
                    timeout=30,
                    text=True,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        "Failed to read static fixture size for "
                        f"{cmd!r}. Run setup_w4_fixtures.sh on the server first. "
                        f"stderr={result.stderr.strip()!r}"
                    )
                try:
                    parts = result.stdout.strip().split()
                    ref[cmd].append(int(parts[0]))
                    self.ref_output_sha256[cmd] = parts[1]
                except (IndexError, ValueError) as exc:
                    raise RuntimeError(
                        f"Invalid fixture size output for {cmd!r}: {result.stdout!r}"
                    ) from exc
                print(
                    f"  ref[{cmd}] = {ref[cmd][0]} bytes, sha256={self.ref_output_sha256[cmd]}",
                    flush=True,
                )
                continue

            for run_idx in range(1, n_runs + 1):
                wrapped = cmd
                if self.args.max_output_lines > 0:
                    wrapped = f"{{ {cmd}; }} 2>&1 | head -n {int(self.args.max_output_lines)}"
                try:
                    result = subprocess.run(
                        ssh_cmd + [wrapped],
                        capture_output=True, timeout=120,
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
        self.line_csv = outdir / "w4_line_log.csv"
        self.setup_csv = outdir / "w4_session_setup.csv"

        if self.args.resume and self.line_csv.exists():
            self._load_incremental_outputs()
            print(
                f"=== Resume mode: loaded {len(self.records)} ok samples, "
                f"{len(self.failures)} failures from {self.line_csv} ===",
                flush=True,
            )
            if not self.setup_csv.exists():
                with self.setup_csv.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["scenario", "protocol", "command", "trial_id", "session_setup_ms"])
                    f.flush()
                    os.fsync(f.fileno())
            return

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
                    "throughput_kib_s",
                    "received_pct",
                    "status",
                    "error_type",
                    "error_message",
                ]
            )
            f.flush()
            os.fsync(f.fileno())

        with self.setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["scenario", "protocol", "command", "trial_id", "session_setup_ms"])
            f.flush()
            os.fsync(f.fileno())

    @staticmethod
    def _float_or_none(value: str) -> Optional[float]:
        if value == "" or value is None:
            return None
        return float(value)

    @staticmethod
    def _int_or_zero(value: str) -> int:
        if value == "" or value is None:
            return 0
        return int(value)

    def _load_incremental_outputs(self) -> None:
        allowed_protocols = set(self.args.protocols)
        allowed_commands = set(self.args.commands)

        with self.line_csv.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                protocol = row.get("protocol", "")
                command = row.get("command", "")
                if protocol not in allowed_protocols or command not in allowed_commands:
                    continue
                if row.get("scenario", self.scenario) not in ("", self.scenario):
                    continue
                try:
                    trial_id = int(row.get("round_id", ""))
                    sample_id = int(row.get("sample_id", ""))
                    command_id = int(row.get("command_id", ""))
                except ValueError:
                    continue

                workload = row.get("workload", "") or self._workload_for_command(command)
                status = row.get("status", "")
                key = self._sample_key(protocol, command, trial_id, sample_id)

                if status == "ok":
                    try:
                        latency_ms = float(row.get("latency_ms", ""))
                    except ValueError:
                        continue
                    throughput = self._float_or_none(row.get("throughput_kib_s", ""))
                    output_bytes = self._int_or_zero(row.get("output_bytes", ""))
                    record = SampleRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=sample_id,
                        command_id=command_id,
                        command=command,
                        latency_ms=latency_ms,
                        output_bytes=output_bytes,
                        throughput_kib_s=throughput,
                        ref_output_bytes=self._int_or_zero(row.get("ref_output_bytes", "")),
                        output_delta_bytes=self._int_or_zero(row.get("output_delta_bytes", "")),
                        expected_sha256=row.get("expected_sha256", ""),
                        received_sha256=row.get("received_sha256", ""),
                        content_match=row.get("content_match", "").lower() == "true",
                        received_pct=float(row.get("received_pct", "100") or "100"),
                    )
                    self.records.append(record)
                    self.results[protocol][command].append(latency_ms)
                    self.output_sizes[protocol][command].append(output_bytes)
                    self.completed_samples.add(key)
                elif status == "fail":
                    failure = FailureRecord(
                        protocol=protocol,
                        workload=workload,
                        round_id=trial_id,
                        sample_id=sample_id,
                        command_id=command_id,
                        command=command,
                        error_type=row.get("error_type", ""),
                        error_message=row.get("error_message", ""),
                    )
                    self.failures.append(failure)
                    self.completed_samples.add(key)

        if self.setup_csv.exists():
            with self.setup_csv.open("r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    protocol = row.get("protocol", "")
                    command = row.get("command", "")
                    if protocol not in allowed_protocols or command not in allowed_commands:
                        continue
                    if row.get("scenario", self.scenario) not in ("", self.scenario):
                        continue
                    try:
                        setup_ms = float(row.get("session_setup_ms", ""))
                    except ValueError:
                        continue
                    self.session_setups[protocol][command].append(setup_ms)

    def _append_record_csv(self, record: SampleRecord) -> None:
        self._fsync_csv_row(
            self.line_csv,
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
                record.ref_output_bytes,
                record.output_delta_bytes,
                record.expected_sha256,
                record.received_sha256,
                "true" if record.content_match else "false",
                (
                    f"{record.throughput_kib_s:.6f}"
                    if record.throughput_kib_s is not None
                    else ""
                ),
                f"{record.received_pct:.2f}",
                "ok",
                "",
                "",
            ],
        )

    def _append_failure_csv(self, failure: FailureRecord) -> None:
        self._fsync_csv_row(
            self.line_csv,
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
                "",
                "",
                "",
                "",
                "",
                "",
                "fail",
                failure.error_type,
                failure.error_message,
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
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = []
        if len(parts) == 2 and parts[0] == "cat":
            fixture_labels = {
                "w4_paths_500b.txt": "fixture 500b",
                "w4_paths_100kb.txt": "fixture 100kb",
                "w4_paths_small.txt": "fixture small",
                "w4_paths_medium.txt": "fixture medium",
                "w4_paths_large.txt": "fixture large",
                "w4_paths_2mb.txt": "fixture 2mb",
            }
            label = fixture_labels.get(Path(parts[1]).name)
            if label is not None:
                return label
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
        _PASSWORD_RE = r"[Pp]assword:\s*$"
        idx = child.expect([_INITIAL_PROMPT_RE, _PASSWORD_RE], timeout=self.args.timeout)
        if idx == 1:
            child.close(force=True)
            raise RuntimeError(
                f"Key auth failed for {protocol} — server asked for password. "
                f"Run: ssh-copy-id -i {self.args.identity_file}.pub "
                f"{self.args.user}@{self.args.host}"
            )
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

    def _wrap_measured_command(self, command: str, start_marker: str, end_marker: str) -> str:
        start_arg = shlex.quote(start_marker)
        end_arg = shlex.quote(end_marker)
        producer = f"{{ {command}; }} 2>&1"
        if self.args.max_output_lines > 0:
            producer = f"{producer} | head -n {int(self.args.max_output_lines)}"
        return f"printf '%s\\n' {start_arg}; {producer}; printf '\\n%s\\n' {end_arg}"

    @staticmethod
    def _payload_bytes(text: str) -> bytes:
        return text.encode("utf-8", errors="ignore")

    def _wait_for_marker(
        self,
        child: pexpect.spawn,
        protocol: str,
        start_marker: str,
        end_marker: str,
    ) -> tuple[int, str]:
        deadline = time.monotonic() + float(self.args.sample_timeout)
        idle_timeout = float(self.args.command_idle_timeout)
        start_re = self._build_token_re(start_marker)
        end_re = self._build_token_re(end_marker)
        read_size = max(4096, int(self.args.maxread))

        buffer = ""
        debug_tail = ""
        output_bytes = 0
        output_hash = hashlib.sha256()
        capturing = False
        saw_activity = False
        last_data_at = time.monotonic()

        def timeout_error(reason: str) -> pexpect.TIMEOUT:
            return pexpect.TIMEOUT(
                f"{reason} while waiting for marker {end_marker!r}. "
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
                    f"EOF while waiting for marker {end_marker!r}. "
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

            if not capturing:
                end_match = end_re.search(buffer)
                if protocol == "mosh" and end_match is not None:
                    # Mosh synchronizes terminal state, not a byte stream. Under
                    # bursty large output it can deliver the final marker and
                    # prompt after dropping earlier scrollback, including the
                    # start marker. Count the visible payload instead of
                    # classifying a completed command as a timeout.
                    final_text = buffer[: end_match.start()]
                    if final_text.endswith("\n"):
                        final_text = final_text[:-1]
                    final_bytes = self._payload_bytes(final_text)
                    output_hash.update(final_bytes)
                    return output_bytes + len(final_bytes), output_hash.hexdigest()

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

    def _measure_output_delivery(
        self,
        child: pexpect.spawn,
        protocol: str,
        command: str,
        start_marker: str,
        end_marker: str,
    ) -> tuple[float, int, str]:
        self._drain_pending_output(child)
        wrapped = self._wrap_measured_command(command, start_marker, end_marker)
        start_ns = time.perf_counter_ns()
        child.sendline(wrapped)
        output_bytes, received_sha256 = self._wait_for_marker(
            child, protocol, start_marker, end_marker
        )
        end_ns = time.perf_counter_ns()
        return (end_ns - start_ns) / 1_000_000.0, output_bytes, received_sha256

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
            if self._sample_key(protocol, command, trial_id, sample_id) in self.completed_samples:
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}/{self.args.trials}"
                    f" sample {sample_id:>3}/{self.args.iterations}: resume skip",
                    flush=True,
                )
                continue

            marker = self._next_marker_token()
            start_marker = f"W4_BEGIN_{marker}"
            end_marker = f"W4_END_{marker}"
            try:
                latency_ms, output_bytes, received_sha256 = self._measure_output_delivery(
                    child, protocol, command, start_marker, end_marker
                )
                throughput = (
                    (output_bytes / 1024.0) / (latency_ms / 1000.0)
                    if latency_ms > 0
                    else None
                )
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

                self.results[protocol][command].append(latency_ms)
                self.output_sizes[protocol][command].append(output_bytes)
                record = SampleRecord(
                    protocol,
                    workload,
                    trial_id,
                    sample_id,
                    command_id,
                    command,
                    latency_ms,
                    output_bytes,
                    throughput,
                    ref_bytes,
                    output_delta_bytes,
                    expected_sha256,
                    received_sha256,
                    content_match,
                    received_pct=received_pct,
                )
                self.records.append(record)
                self._append_record_csv(record)
                self.completed_samples.add(
                    self._sample_key(protocol, command, trial_id, sample_id)
                )
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}/{self.args.trials}"
                    f" sample {sample_id:>3}/{self.args.iterations}:"
                    f" {latency_ms:.2f} ms, {output_bytes / 1024.0:.1f} KiB"
                    f" | recv {received_pct:.1f}%"
                    f" | delta {output_delta_bytes:+d} B",
                    f" | content {'ok' if content_match else 'BAD'}",
                    flush=True,
                )
            except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                recovered = True
                if isinstance(exc, pexpect.TIMEOUT):
                    recovered = self._recover_after_timeout(child)
                failure = FailureRecord(
                    protocol,
                    workload,
                    trial_id,
                    sample_id,
                    command_id,
                    command,
                    type(exc).__name__,
                    str(exc),
                )
                self.failures.append(failure)
                self._append_failure_csv(failure)
                self.completed_samples.add(
                    self._sample_key(protocol, command, trial_id, sample_id)
                )
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}"
                    f" sample {sample_id:>3}: FAIL ({type(exc).__name__}: {exc})"
                    f"{'' if recovered else ' [session not recovered]'}",
                    flush=True,
                )
                if self.args.reopen_on_failure or not recovered:
                    raise

    def _trial_complete(self, protocol: str, command: str, trial_id: int) -> bool:
        return all(
            self._sample_key(protocol, command, trial_id, sample_id)
            in self.completed_samples
            for sample_id in range(1, self.args.iterations + 1)
        )

    def _run_session_group(
        self,
        protocol: str,
        workload: str,
        command: str,
        command_id: int,
    ) -> None:
        for trial_id in range(1, self.args.trials + 1):
            if self._trial_complete(protocol, command, trial_id):
                print(
                    f"[{protocol:>4}/{workload:<12}] trial {trial_id:>2}/{self.args.trials}"
                    " resume skip complete trial",
                    flush=True,
                )
                continue

            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][command].append(setup_ms)
                self._append_setup_csv(protocol, command, trial_id, setup_ms)
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
        content_data = [
            r.content_match for r in self.records
            if r.protocol == protocol and r.command == command
        ]
        content_bad = sum(1 for ok in content_data if not ok)
        content_ok_pct = (
            100.0 * (len(content_data) - content_bad) / len(content_data)
            if content_data
            else None
        )

        if not data:
            return SummaryRow(
                protocol,
                workload,
                command,
                0,
                failures,
                success_rate,
                None, None, None, None, None, None, None, None, None, None, None, None,
                None, 0,
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
            content_ok_pct=content_ok_pct,
            content_bad=content_bad,
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
            f" {'Recv%Avg':>8} | {'Recv%Min':>8} | {'ContentOK%':>10} | {'BadHash':>7}"
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
                f" {self._fmt(row.recv_pct_mean):>8} | {self._fmt(row.recv_pct_min):>8} |"
                f" {self._fmt(row.content_ok_pct):>10} | {row.content_bad:>7}"
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
        line_tmp = line_csv.with_suffix(line_csv.suffix + ".tmp")
        setup_tmp = setup_csv.with_suffix(setup_csv.suffix + ".tmp")

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
                        record.ref_output_bytes,
                        record.output_delta_bytes,
                        record.expected_sha256,
                        record.received_sha256,
                        "true" if record.content_match else "false",
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
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "fail",
                        failure.error_type,
                        failure.error_message,
                    ]
                )
            f.flush()
            os.fsync(f.fileno())
        os.replace(line_tmp, line_csv)

        with setup_tmp.open("w", newline="", encoding="utf-8") as f:
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
            f.flush()
            os.fsync(f.fileno())
        os.replace(setup_tmp, setup_csv)

        print(f"Saved line log CSV  : {line_csv}")
        print(f"Saved setup CSV     : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="W4 cat-file benchmark")
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
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--sample-timeout", type=float, default=900.0)
    parser.add_argument("--command-idle-timeout", type=float, default=180.0)
    parser.add_argument("--max-output-lines", type=int, default=1000)
    parser.add_argument("--maxread", type=int, default=65535)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="w4_results_trungnt/100KB/default")
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
    parser.add_argument("--resume", action="store_true", help="Append to existing CSVs and skip samples already logged as ok/fail")
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

    interrupted = False

    def _handle_stop(signum, _frame) -> None:
        raise KeyboardInterrupt(f"received signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handle_stop)
        except (AttributeError, ValueError):
            pass

    benchmark = W4Benchmark(args)
    try:
        benchmark.run()
    except KeyboardInterrupt as exc:
        interrupted = True
        print(f"\nInterrupted: {exc}. Exporting partial in-memory summary...", flush=True)
    finally:
        benchmark.print_report()
        benchmark.export()
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
