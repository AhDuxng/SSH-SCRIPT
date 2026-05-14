#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import shutil
import re
import shlex
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect.  Install with:  pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["interactive_shell", "vim", "nano"]
DEFAULT_PROMPT    = "__W3_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"

PROBE_TOKEN = "W3_PROBE_FIXED_Q9J5V2K7M4T8X1"
PROBE_TAIL_LEN = 10

TMUX_READY_MARKER = "__W3_TMUX_READY__"
TMUX_MISSING_MARKER = "__W3_TMUX_MISSING__"

_ANSI_SEQ   = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ECHO_GAP   = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
    re.MULTILINE,
)


class MissingExecutableError(RuntimeError):
    """Raised when a local client command such as ssh3 or mosh is missing."""


@dataclass
class SampleRecord:
    protocol:   str
    workload:   str
    round_id:   int
    sample_id:  int
    latency_ms: float


@dataclass
class FailureRecord:
    protocol:      str
    workload:      str
    round_id:      int
    sample_id:     int
    error_type:    str
    error_message: str


@dataclass
class SummaryRow:
    protocol:           str
    workload:           str
    n:                  int
    failures:           int
    success_rate_pct:   float
    min_ms:             Optional[float]
    mean_ms:            Optional[float]
    median_ms:          Optional[float]
    stdev_ms:           Optional[float]
    p95_ms:             Optional[float]
    p99_ms:             Optional[float]
    max_ms:             Optional[float]
    ci95_half_width_ms: Optional[float]


class W3Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args       = args
        self.target     = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.probe_token = PROBE_TOKEN
        self.probe_tail = self.probe_token[-PROBE_TAIL_LEN:]
        self.probe_echo_re = self._build_probe_echo_re(self.probe_token)
        self.probe_tail_echo_re = self._build_probe_echo_re(self.probe_tail)
        self.probe_seq = 0
        self.records:  List[SampleRecord]  = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }

    @staticmethod
    def _build_probe_echo_re(token: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in token]
        return re.compile("".join(parts))

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        # TUI redraw and tmux pane updates may split prompt bytes with ANSI updates.
        child.expect(self.prompt_re, timeout=self.args.timeout)

    def _next_probe_token(self) -> str:
        """Return a unique token so pexpect cannot match stale echo bytes."""
        self.probe_seq += 1
        return f"{self.probe_token}_{self.probe_seq:08d}"

    def _expect_probe_echo(self, child: pexpect.spawn, token: str) -> None:
        # Match the full unique token only.  Matching only the tail is unsafe
        # when tmux/background panes produce a lot of output.
        child.expect(self._build_probe_echo_re(token), timeout=self.args.timeout)

    @staticmethod
    def _ensure_vim_insert_mode(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.send("i")

    @staticmethod
    def _erase_probe_token(child: pexpect.spawn, token: str) -> None:
        if token:
            child.send("\x7f" * len(token))

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 16) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=8192, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _probe_once(self, child: pexpect.spawn, erase_after_echo: bool = False) -> float:
        token = self._next_probe_token()
        self._drain_pending_output(child)
        start_ns = time.perf_counter_ns()
        child.send(token)
        self._expect_probe_echo(child, token)
        end_ns = time.perf_counter_ns()
        if erase_after_echo:
            self._erase_probe_token(child, token)
        return (end_ns - start_ns) / 1_000_000.0

    @staticmethod
    def _recover_nano_state(child: pexpect.spawn) -> None:
        child.sendcontrol("l")

    @staticmethod
    def _recover_vim_state(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.sendcontrol("l")
        child.send("i")

    def _probe_vim_once(self, child: pexpect.spawn) -> float:
        self._ensure_vim_insert_mode(child)
        return self._probe_once(child, erase_after_echo=True)

    @staticmethod
    def _protocol_binary(protocol: str) -> str:
        if protocol == "ssh":
            return "ssh"
        if protocol == "ssh3":
            return "ssh3"
        if protocol == "mosh":
            return "mosh"
        raise ValueError(f"Unsupported protocol: {protocol}")

    def _ensure_protocol_available(self, protocol: str) -> None:
        binary = self._protocol_binary(protocol)
        if shutil.which(binary) is None:
            raise MissingExecutableError(
                f"Local command '{binary}' was not found in PATH. "
                f"Install {binary} on this client, or remove '{protocol}' "
                f"from --protocols / PROTOCOLS. Current PATH={os.environ.get('PATH', '')}"
            )

    def _session_command(self, protocol: str) -> str:
        target     = self.target
        ssh_common = ["ssh", "-tt"]
        if self.args.source_ip:
            ssh_common += ["-b", self.args.source_ip]
        if self.args.strict_host_key_checking:
            ssh_common += ["-o", "StrictHostKeyChecking=yes"]
        else:
            ssh_common += [
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ]
        if self.args.identity_file:
            ssh_common += ["-i", self.args.identity_file]
        if self.args.batch_mode:
            ssh_common += ["-o", "BatchMode=yes"]
        ssh_common += [target]

        if protocol == "ssh":
            return shlex.join(ssh_common)

        if protocol == "mosh":
            ssh_cmd    = shlex.join(ssh_common[:-1])
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

    def _remote_cmd_expect_prompt(self, child: pexpect.spawn, command: str) -> None:
        self._drain_pending_output(child)
        child.sendline(command)
        self._expect_prompt(child)

    def _make_tmux_session_name(self, protocol: str, workload: str, trial_id: int) -> str:
        safe_protocol = re.sub(r"[^A-Za-z0-9_]", "_", protocol)
        safe_workload = re.sub(r"[^A-Za-z0-9_]", "_", workload)
        return (
            f"{self.args.tmux_session_prefix}_"
            f"{safe_protocol}_{safe_workload}_t{trial_id}_p{os.getpid()}"
        )

    def _default_bg_load_commands(self) -> List[str]:
        # Each command is intentionally visual, moderate-rate, and long-running.
        return [
            (
                "while true; do "
                "printf '[pane1-clock] '; date '+%H:%M:%S.%3N'; "
                "sleep 0.20; "
                "done"
            ),
            (
                "while true; do "
                "i=1; while [ $i -le 18 ]; do "
                "printf '[pane2-stream] line=%02d data=%08x\\n' $i $RANDOM; "
                "i=$((i+1)); "
                "done; "
                "sleep 0.35; "
                "done"
            ),
            (
                "while true; do "
                "printf '[pane3-cpu] %s\\n' \"$(date '+%H:%M:%S')\"; "
                "ps -eo pid,comm,%cpu,%mem --sort=-%cpu 2>/dev/null | head -n 8; "
                "sleep 0.45; "
                "done"
            ),
            (
                "while true; do "
                "ping -c 1 -W 1 127.0.0.1 2>/dev/null | sed 's/^/[pane4-ping] /'; "
                "sleep 0.30; "
                "done"
            ),
        ]

    def _bg_load_command_for_pane(self, pane_idx: int) -> str:
        custom = self.args.bg_load_cmd
        if custom:
            return custom[(pane_idx - 1) % len(custom)]
        defaults = self._default_bg_load_commands()
        return defaults[(pane_idx - 1) % len(defaults)]

    def _check_remote_tmux(self, child: pexpect.spawn) -> None:
        marker_cmd = (
            f"command -v tmux >/dev/null 2>&1 "
            f"&& echo {TMUX_READY_MARKER} || echo {TMUX_MISSING_MARKER}"
        )
        self._drain_pending_output(child)
        child.sendline(marker_cmd)
        idx = child.expect(
            [TMUX_READY_MARKER, TMUX_MISSING_MARKER],
            timeout=self.args.timeout,
        )
        self._expect_prompt(child)
        if idx == 1:
            raise ValueError(
                "Remote host does not have tmux. Install tmux on the server "
                "or run with --tmux-panes 1 to disable the 5-pane mode."
            )

    def _setup_remote_tmux_panes(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> str:
        panes = self.args.tmux_panes
        if panes < 2:
            return ""

        self._check_remote_tmux(child)
        session = self._make_tmux_session_name(protocol, workload, trial_id)
        q_session = shlex.quote(session)
        target0 = f"{session}:0.0"

        # Remove stale session from a previous interrupted run.
        self._remote_cmd_expect_prompt(
            child,
            f"tmux kill-session -t {q_session} >/dev/null 2>&1 || true",
        )

        # Pane 0 is the measured interactive pane.
        measured_shell = "bash --noprofile --norc"
        self._remote_cmd_expect_prompt(
            child,
            shlex.join([
                "tmux", "new-session", "-d",
                "-s", session,
                "-x", str(self.args.term_cols),
                "-y", str(self.args.term_rows),
                measured_shell,
            ]),
        )

        tmux_setup_commands = [
            ["tmux", "set-option", "-t", session, "status", "on"],
            ["tmux", "set-option", "-t", session, "status-interval", "1"],
            ["tmux", "set-option", "-t", session, "remain-on-exit", "off"],
            ["tmux", "set-window-option", "-t", f"{session}:0", "automatic-rename", "off"],
            ["tmux", "set-window-option", "-t", f"{session}:0", "pane-base-index", "0"],
            ["tmux", "set-option", "-t", session, "pane-border-status", "top"],
            [
                "tmux", "set-option", "-t", session,
                "pane-border-format", "#[bold]pane #{pane_index}: #{pane_title}#[default]",
            ],
            ["tmux", "rename-window", "-t", f"{session}:0", "W3-5pane"],
            ["tmux", "select-pane", "-t", target0, "-T", "MEASURE pane0"],
        ]
        for cmd in tmux_setup_commands:
            self._remote_cmd_expect_prompt(child, shlex.join(cmd))

        # Create pane 1..N-1. They continuously generate visible background output.
        for pane_idx in range(1, panes):
            bg_cmd = self._bg_load_command_for_pane(pane_idx)
            shell_cmd = (
                f"printf '[pane{pane_idx}] background load started\\n'; "
                f"{bg_cmd}"
            )
            self._remote_cmd_expect_prompt(
                child,
                shlex.join([
                    "tmux", "split-window", "-d",
                    "-t", target0,
                    shlex.join(["bash", "-lc", shell_cmd]),
                ]),
            )
            self._remote_cmd_expect_prompt(
                child,
                shlex.join([
                    "tmux", "select-pane",
                    "-t", f"{session}:0.{pane_idx}",
                    "-T", f"LOAD pane{pane_idx}",
                ]),
            )

        # Make all panes visible and select pane 0 for measurement.
        self._remote_cmd_expect_prompt(
            child,
            shlex.join(["tmux", "select-layout", "-t", f"{session}:0", "tiled"]),
        )

        # Set a unique prompt inside pane 0 before attaching.
        pane0_init = f"export PS1={shlex.quote(self.args.prompt)}; clear"
        self._remote_cmd_expect_prompt(
            child,
            shlex.join(["tmux", "send-keys", "-t", target0, "-l", pane0_init]),
        )
        self._remote_cmd_expect_prompt(
            child,
            shlex.join(["tmux", "send-keys", "-t", target0, "C-m"]),
        )
        self._remote_cmd_expect_prompt(
            child,
            shlex.join(["tmux", "select-pane", "-t", target0]),
        )

        # Attach to the 5-pane tmux session. From this point pexpect types into pane 0.
        self._drain_pending_output(child)
        child.sendline(shlex.join(["tmux", "attach-session", "-t", session]))
        self._expect_prompt(child)
        setattr(child, "_w3_tmux_session", session)
        setattr(child, "_w3_using_tmux", True)
        return session

    def _open_session(self, protocol: str, workload: str, trial_id: int) -> tuple:
        self._ensure_protocol_available(protocol)
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
            maxread=20000,
            searchwindowsize=20000,
            dimensions=(self.args.term_rows, self.args.term_cols),
            env={"TERM": self.args.term},
        )
        # pexpect defaults delaybeforesend to 0.05 s.  That creates an
        # artificial 50 ms floor in keystroke-latency measurements.
        # None disables the pre-send sleep.
        child.delaybeforesend = None

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        # Set prompt for the original remote shell too; useful after detaching tmux.
        child.sendline(
            f"export PS1={shlex.quote(self.args.prompt)}; "
            f"export TERM={shlex.quote(self.args.term)}"
        )
        self._expect_prompt(child)

        if self.args.tmux_panes > 1:
            self._setup_remote_tmux_panes(child, protocol, workload, trial_id)
        else:
            setattr(child, "_w3_using_tmux", False)

        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        using_tmux = bool(getattr(child, "_w3_using_tmux", False))
        tmux_session = getattr(child, "_w3_tmux_session", "")

        if using_tmux:
            # Detach from tmux first. This works even if pane 0 is in vim/nano/cat.
            try:
                child.sendcontrol("b")
                time.sleep(0.05)
                child.send("d")
                child.expect([self.prompt_re, r"\[detached"], timeout=5)
                try:
                    self._expect_prompt(child)
                except Exception:
                    pass
            except Exception:
                pass

            # Kill the tmux session so background load panes cannot remain alive.
            if tmux_session:
                try:
                    child.sendline(
                        f"tmux kill-session -t {shlex.quote(tmux_session)} "
                        f">/dev/null 2>&1 || true"
                    )
                    self._expect_prompt(child)
                except Exception:
                    pass

        try:
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=5)
        except Exception:
            child.close(force=True)
        finally:
            try:
                if getattr(child, "logfile_read", None) is not None:
                    child.logfile_read.close()
            except Exception:
                pass

    def _measure_interactive_shell(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        child.sendline("cat")
        child.expect_exact("\n", timeout=self.args.timeout)

        for _ in range(warmup):
            self._probe_once(child, erase_after_echo=False)

        latencies: List[float] = []
        for i in range(iterations):
            lat = self._probe_once(child, erase_after_echo=False)
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("c")
        child.sendcontrol("c")
        self._expect_prompt(child)
        return latencies

    def _measure_vim(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        remote_file = self.args.remote_vim_file
        child.sendline(f"vim -Nu NONE -n {shlex.quote(remote_file)}")
        child.send("i")
        child.expect([r"-- INSERT --", r"INSERT"], timeout=self.args.timeout)

        for _ in range(warmup):
            try:
                self._probe_vim_once(child)
            except pexpect.TIMEOUT:
                self._recover_vim_state(child)
                self._probe_vim_once(child)

        latencies: List[float] = []
        for i in range(iterations):
            try:
                lat = self._probe_vim_once(child)
            except pexpect.TIMEOUT:
                self._recover_vim_state(child)
                lat = self._probe_vim_once(child)
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.send("\x1b")
        child.sendline(":q!")
        self._expect_prompt(child)
        return latencies

    def _measure_nano(
        self,
        child: pexpect.spawn,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
    ) -> List[float]:
        remote_file = self.args.remote_nano_file
        child.sendline(f"nano --ignorercfiles {shlex.quote(remote_file)}")
        child.expect([r"GNU nano", r"\^G Help"], timeout=self.args.timeout)

        for _ in range(warmup):
            try:
                self._probe_once(child, erase_after_echo=True)
            except pexpect.TIMEOUT:
                self._recover_nano_state(child)
                self._probe_once(child, erase_after_echo=True)

        latencies: List[float] = []
        for i in range(iterations):
            try:
                lat = self._probe_once(child, erase_after_echo=True)
            except pexpect.TIMEOUT:
                self._recover_nano_state(child)
                lat = self._probe_once(child, erase_after_echo=True)
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("x")
        child.send("n")
        self._expect_prompt(child)
        return latencies

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> List[float]:
        def report_cb(s_idx: int, lat: float) -> None:
            self.results[protocol][workload].append(lat)
            self.records.append(
                SampleRecord(protocol, workload, trial_id, s_idx, lat)
            )
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {s_idx:>3}/{self.args.iterations}:"
                f" {lat:.2f} ms",
                flush=True,
            )

        if workload == "interactive_shell":
            latencies = self._measure_interactive_shell(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        elif workload == "vim":
            latencies = self._measure_vim(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        elif workload == "nano":
            latencies = self._measure_nano(
                child,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
            )
        else:
            raise ValueError(f"Unsupported workload: {workload}")

        return latencies

    def _record_failure(
        self,
        protocol: str,
        workload: str,
        trial_id: int,
        sample_id: int,
        exc: BaseException,
    ) -> None:
        self.failures.append(
            FailureRecord(
                protocol=protocol,
                workload=workload,
                round_id=trial_id,
                sample_id=sample_id,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        )

    def _run_session_group(self, protocol: str, workload: str) -> None:
        try:
            self._ensure_protocol_available(protocol)
        except MissingExecutableError as exc:
            self._record_failure(protocol, workload, 0, -1, exc)
            print(
                f"[{protocol:>4}/{workload:<18}] SKIP "
                f"({type(exc).__name__}: {exc})",
                flush=True,
            )
            return

        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                try:
                    child, setup_ms = self._open_session(protocol, workload, trial_id)
                except (
                    MissingExecutableError,
                    pexpect.ExceptionPexpect,
                    pexpect.TIMEOUT,
                    pexpect.EOF,
                    OSError,
                    ValueError,
                ) as exc:
                    self._record_failure(protocol, workload, trial_id, -1, exc)
                    print(
                        f"[{protocol:>4}/{workload:<18}]"
                        f" trial {trial_id:>2}/{self.args.trials}: SESSION FAIL"
                        f" ({type(exc).__name__}: {exc})",
                        flush=True,
                    )
                    continue

                self.session_setups[protocol][workload].append(setup_ms)
                pane_note = (
                    f", tmux_panes={self.args.tmux_panes}"
                    if self.args.tmux_panes > 1 else ""
                )
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms{pane_note}",
                    flush=True,
                )

                try:
                    self._run_trial(child, protocol, workload, trial_id)
                except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                    self._record_failure(protocol, workload, trial_id, -1, exc)
                    print(
                        f"[{protocol:>4}/{workload:<18}]"
                        f" trial {trial_id:>2}: FAIL"
                        f" ({type(exc).__name__}: {exc})",
                        flush=True,
                    )
                    if self.args.reopen_on_failure:
                        if child is not None:
                            self._close_session(child)
                            child = None
                        try:
                            child, setup_ms = self._open_session(
                                protocol, workload, trial_id
                            )
                        except (
                            MissingExecutableError,
                            pexpect.ExceptionPexpect,
                            pexpect.TIMEOUT,
                            pexpect.EOF,
                            OSError,
                            ValueError,
                        ) as reopen_exc:
                            self._record_failure(
                                protocol, workload, trial_id, -1, reopen_exc
                            )
                            print(
                                f"[{protocol:>4}/{workload:<18}]"
                                f" trial {trial_id:>2}: REOPEN FAIL"
                                f" ({type(reopen_exc).__name__}: {reopen_exc})",
                                flush=True,
                            )

            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
        pairs = [
            (p, w)
            for p in self.args.protocols
            for w in self.args.workloads
        ]
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
        s     = sorted(values)
        k     = (len(s) - 1) * (p / 100.0)
        lower = math.floor(k)
        upper = math.ceil(k)
        if lower == upper:
            return s[int(k)]
        return s[lower] + (s[upper] - s[lower]) * (k - lower)

    def _summary_row(self, protocol: str, workload: str) -> SummaryRow:
        data   = self.results[protocol][workload]
        fail_n = sum(
            1 for f in self.failures
            if f.protocol == protocol and f.workload == workload
        )
        n     = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None,
            )

        mean_ms   = statistics.mean(data)
        median_ms = statistics.median(data)
        stdev_ms  = statistics.stdev(data) if n > 1 else 0.0
        ci95      = (1.96 * stdev_ms / math.sqrt(n)) if n > 1 else 0.0
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
            return dict(n=0, mean=None, median=None, stdev=None,
                        min=None, max=None)
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
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>4} | {'Fail':>4} |"
            f" {'Success%':>8} | {'Min':>8} | {'Mean':>8} | {'Median':>8} |"
            f" {'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} |"
            f" {'CI95+/-':>9}"
        )
        print("-" * width)
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.workload:<18} | {row.n:>4} |"
                f" {row.failures:>4} | {row.success_rate_pct:>8.1f} |"
                f" {fmt(row.min_ms):>8} | {fmt(row.mean_ms):>8} |"
                f" {fmt(row.median_ms):>8} | {fmt(row.stdev_ms):>8} |"
                f" {fmt(row.p95_ms):>8} | {fmt(row.p99_ms):>8} |"
                f" {fmt(row.max_ms):>8} | {fmt(row.ci95_half_width_ms):>9}"
            )
        print("=" * width)

        ss_width = 96
        print("\n" + "-" * ss_width)
        print(
            "SESSION SETUP LATENCY (ms)  "
            "[spawn -> first shell prompt, PS1/tmux setup excluded]"
        )
        print(
            f"{'Protocol':<8} | {'Workload':<18} | {'N':>3} |"
            f" {'Min':>8} | {'Mean':>8} | {'Median':>8} | {'Std':>8} |"
            f" {'Max':>8}"
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

        line_csv = outdir / "w3_line_log.csv"
        setup_csv = outdir / "w3_session_setup.csv"

        with line_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "protocol",
                    "workload",
                    "round_id",
                    "sample_id",
                    "latency_ms",
                    "status",
                    "error_type",
                    "error_message",
                ]
            )
            for r in self.records:
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        f"{r.latency_ms:.6f}",
                        "ok",
                        "",
                        "",
                    ]
                )
            for r in self.failures:
                writer.writerow(
                    [
                        r.protocol,
                        r.workload,
                        r.round_id,
                        r.sample_id,
                        "",
                        "fail",
                        r.error_type,
                        r.error_message,
                    ]
                )

        with setup_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["protocol", "workload", "trial_id", "session_setup_ms"]
            )
            for p in self.args.protocols:
                for w in self.args.workloads:
                    for trial_id, ms in enumerate(
                        self.session_setups[p][w], start=1
                    ):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved line log CSV    : {line_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W3 Interactive Editing benchmark with 5-pane visible remote load")
    p.add_argument(
        "--host", default="192.168.8.102",
        help="Target host IP or hostname",
    )
    p.add_argument(
        "--user", default="trungnt",
        help="Remote username",
    )
    p.add_argument(
        "--source-ip", default="192.168.8.100",
        help="Client source IP for SSH / Mosh where supported",
    )
    p.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "id_rsa"),
        help="SSH private key path",
    )
    p.add_argument(
        "--protocols", nargs="+",
        default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS,
    )
    p.add_argument(
        "--workloads", nargs="+",
        default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS,
    )
    p.add_argument(
        "--trials", type=int, default=15,
        help="Independent sessions per protocol/workload pair",
    )
    p.add_argument(
        "--iterations", type=int, default=100,
        help="Recorded samples per trial",
    )
    p.add_argument(
        "--warmup-rounds", type=int, default=5,
        help="Warmup samples per trial (not recorded)",
    )
    p.add_argument(
        "--timeout", type=int, default=20,
        help="pexpect timeout in seconds",
    )
    p.add_argument(
        "--seed", type=int, default=42,
        help="Random seed",
    )
    p.add_argument(
        "--output-dir", default="w3_results",
        help="Directory for CSV outputs",
    )
    p.add_argument(
        "--prompt", default=DEFAULT_PROMPT,
        help="Unique shell prompt marker used after session is ready",
    )
    p.add_argument(
        "--ssh3-path", default=DEFAULT_SSH3_PATH,
        help="SSH3 terminal path suffix",
    )
    p.add_argument(
        "--ssh3-insecure", action="store_true",
        help="Pass -insecure to ssh3",
    )
    p.add_argument(
        "--batch-mode", action="store_true",
        help="Enable BatchMode for SSHv2 / Mosh bootstrap SSH",
    )
    p.add_argument(
        "--strict-host-key-checking", action="store_true",
        help="Keep strict host key checking enabled",
    )
    p.add_argument(
        "--mosh-predict", default="adaptive",
        choices=["adaptive", "always", "never"],
        help="Mosh prediction mode",
    )
    p.add_argument(
        "--remote-vim-file", default="/tmp/w3_vim_bench.txt",
        help="Remote file path used for the vim workload",
    )
    p.add_argument(
        "--remote-nano-file", default="/tmp/w3_nano_bench.txt",
        help="Remote file path used for the nano workload",
    )
    p.add_argument(
        "--shuffle-pairs", action="store_true",
        help="Shuffle protocol/workload execution order",
    )
    p.add_argument(
        "--reopen-on-failure", action="store_true",
        help="Reopen session after each failed measured sample",
    )
    p.add_argument(
        "--tmux-panes", type=int, default=5,
        help=(
            "Number of panes inside one remote tmux session. "
            "Default 5: pane 0 is measured, panes 1-4 show background load. "
            "Use 1 to disable tmux-pane mode."
        ),
    )
    p.add_argument(
        "--tmux-session-prefix", default="w3bench",
        help="Prefix for temporary remote tmux session names",
    )
    p.add_argument(
        "--bg-load-cmd", action="append", default=[],
        help=(
            "Custom background load command for tmux load panes. "
            "Can be passed multiple times; commands are reused cyclically."
        ),
    )
    p.add_argument(
        "--term", default="xterm-256color",
        help="TERM value used for the pexpect PTY and remote shells",
    )
    p.add_argument(
        "--term-rows", type=int, default=45,
        help="PTY/tmux height. Larger values keep 5 panes usable.",
    )
    p.add_argument(
        "--term-cols", type=int, default=160,
        help="PTY/tmux width. Larger values keep 5 panes usable.",
    )
    p.add_argument(
        "--log-pexpect", action="store_true",
        help="Deprecated compatibility flag (no-op): pexpect logs are disabled",
    )
    return p


def main() -> int:
    parser = build_arg_parser()
    args   = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.warmup_rounds < 0:
        parser.error("--warmup-rounds must be >= 0")
    if args.tmux_panes <= 0:
        parser.error("--tmux-panes must be >= 1")
    if args.tmux_panes > 9:
        parser.error("--tmux-panes should be <= 9 to keep panes readable")
    if args.term_rows < 24:
        parser.error("--term-rows should be >= 24")
    if args.term_cols < 80:
        parser.error("--term-cols should be >= 80")

    bench = W3Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
