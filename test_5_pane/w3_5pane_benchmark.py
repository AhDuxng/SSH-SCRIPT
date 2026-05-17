#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import random
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

PROBE_CHAR_ALPHABET = "abcdegijkopvwxz"

# Include common CSI sequences plus SCS sequences like ESC(B
# that nano emits during screen redraws.
_ANSI_SEQ   = r"(?:\x1b\[\??[0-9;]*[a-zA-Z]|\x1b[\(\)][0-9A-Za-z])"
_ECHO_GAP   = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_INITIAL_PROMPT_RE = re.compile(
    r"[#$>](?:" + _ANSI_SEQ + r"|\s)*\s*$",
)

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
        if not args.probe_chars:
            raise ValueError("--probe-chars must contain at least one character")
        probe_chars = "".join(
            ch for ch in dict.fromkeys(args.probe_chars)
            if ch.isalnum() and ch.isprintable()
        )
        if not probe_chars:
            raise ValueError("--probe-chars must contain alphanumeric characters")
        if args.probe_search_window < 0:
            raise ValueError("--probe-search-window must be >= 0")
        if args.tmux_search_window < 0:
            raise ValueError("--tmux-search-window must be >= 0")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.probe_chars = probe_chars
        self.probe_search_window: Optional[int] = (
            None if args.probe_search_window == 0
            else max(8, args.probe_search_window)
        )
        self.tmux_search_window: Optional[int] = (
            None if args.tmux_search_window == 0
            else max(1024, args.tmux_search_window)
        )
        self.prev_probe_char: Optional[str] = None
        self.tmux_probe_counter: int = 0
        self.records:  List[SampleRecord]  = []
        self.failures: List[FailureRecord] = []
        self.results: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }
        self.session_setups: Dict[str, Dict[str, List[float]]] = {
            p: {w: [] for w in args.workloads} for p in args.protocols
        }

    @staticmethod
    def _build_prompt_re(prompt_marker: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in prompt_marker]
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*$")

    @staticmethod
    def _build_interleaved_text_re(text: str) -> re.Pattern[str]:
        parts = [re.escape(ch) + _ECHO_GAP for ch in text]
        return re.compile("".join(parts))

    @staticmethod
    def _build_loose_interleaved_text_re(
        text: str,
        max_gap: int = 32,
    ) -> re.Pattern[str]:
        gap = rf"[\s\S]{{0,{max(1, max_gap)}}}"
        parts = [re.escape(ch) + gap for ch in text]
        return re.compile("".join(parts))

    @staticmethod
    def _printf_literal_cmd(text: str) -> str:
        escaped = "".join(f"\\{ord(ch):03o}" for ch in text)
        return f"printf {shlex.quote(escaped)}"

    @staticmethod
    def _tmux_attach_mode() -> bool:
        return bool(os.environ.get("W3_ATTACH_CMD"))

    @staticmethod
    def _should_attach_after_login(protocol: Optional[str]) -> bool:
        if protocol is None:
            return False
        protocols = os.environ.get("W3_ATTACH_AFTER_LOGIN_PROTOCOLS", "")
        return protocol in protocols.split()

    def _expect_tmux_boot_marker(self, child: pexpect.spawn) -> None:
        boot_marker = os.environ.get(
            "W3_ATTACH_BOOT_MARKER",
            "__W3_ATTACH_PANE0_READY__",
        )
        child.expect_exact(
            boot_marker,
            timeout=self.args.timeout,
            searchwindowsize=self.tmux_search_window,
        )

    def _attach_tmux_after_login(self, child: pexpect.spawn) -> None:
        attach_cmd = os.environ.get("W3_ATTACH_CMD", "").strip()
        if not attach_cmd:
            raise ValueError("W3_ATTACH_CMD is not set for attach-after-login flow")
        self._drain_pending_output(child, max_reads=16)
        child.sendline(attach_cmd)
        self._expect_tmux_boot_marker(child)

    def _expect_prompt(
        self,
        child: pexpect.spawn,
        protocol: Optional[str] = None,
    ) -> None:
        if self._tmux_attach_mode():
            marker = f"__W3_PROMPT_READY__{random.randrange(10_000, 99_999)}__"
            last_exc: Optional[pexpect.TIMEOUT] = None
            for _ in range(3):
                self._drain_pending_output(child, max_reads=16)
                child.sendline("")
                child.sendline(self._printf_literal_cmd(marker + "\n"))
                try:
                    child.expect_exact(
                        marker,
                        timeout=self.args.timeout,
                        searchwindowsize=self.tmux_search_window,
                    )
                    return
                except pexpect.TIMEOUT as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc

        # TUI redraw (especially over mosh) may split prompt bytes with ANSI updates.
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

    def _refresh_prompt(
        self,
        child: pexpect.spawn,
        protocol: Optional[str] = None,
    ) -> None:
        self._drain_pending_output(child)
        child.sendline("")
        self._expect_prompt(child, protocol=protocol)

    @staticmethod
    def _erase_probe_chars(
        child: pexpect.spawn,
        length: int,
        erase_key: str = "\x7f",
    ) -> None:
        if length > 0:
            child.send(erase_key * length)

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _next_probe_text(self) -> str:
        if self._tmux_attach_mode():
            self.tmux_probe_counter += 1
            suffix = random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
            return f"~W3P{self.tmux_probe_counter:08d}{suffix}~"

        if len(self.probe_chars) == 1:
            probe_char = self.probe_chars[0]
            self.prev_probe_char = probe_char
            return probe_char

        probe_char = random.choice(self.probe_chars)
        if self.prev_probe_char is not None and probe_char == self.prev_probe_char:
            choices = self.probe_chars.replace(self.prev_probe_char, "")
            probe_char = random.choice(choices)
        self.prev_probe_char = probe_char
        return probe_char

    def _consume_stray_probe_text(
        self,
        child: pexpect.spawn,
        probe_text: str,
        search_window: Optional[int],
        max_polls: int = 4,
    ) -> None:
        # Remove buffered matches for the same probe text before timing.
        # This avoids matching stale screen output from a previous step.
        for _ in range(max_polls):
            idx = child.expect_exact(
                [probe_text, pexpect.TIMEOUT, pexpect.EOF],
                timeout=0,
                searchwindowsize=search_window,
            )
            if idx == 0:
                continue
            if idx == 1:
                return
            raise pexpect.EOF("EOF while draining stale probe bytes")

    def _probe_once(
        self,
        child: pexpect.spawn,
        erase_after_echo: bool = False,
        erase_key: str = "\x7f",
    ) -> float:
        self._drain_pending_output(child)
        search_window: Optional[int] = self.probe_search_window
        if self._tmux_attach_mode():
            search_window = None
        probe_text = self._next_probe_text()
        if not self._tmux_attach_mode():
            self._consume_stray_probe_text(child, probe_text, search_window)
        start_ns = time.perf_counter_ns()
        child.send(probe_text)
        if self._tmux_attach_mode():
            # Prefer exact match to avoid regex over-match across unrelated
            # background-pane redraw bytes; use bounded loose fallback when
            # redraw noise splits echoed probe bytes.
            exact_timeout = max(1, min(self.args.timeout, 8))
            try:
                child.expect_exact(
                    probe_text,
                    timeout=exact_timeout,
                    searchwindowsize=self.tmux_search_window,
                )
            except pexpect.TIMEOUT:
                remaining_timeout = max(1, self.args.timeout - exact_timeout)
                loose_re = self._build_loose_interleaved_text_re(
                    probe_text,
                    max_gap=self.args.tmux_probe_max_gap,
                )
                child.expect(
                    loose_re,
                    timeout=remaining_timeout,
                    searchwindowsize=self.tmux_search_window,
                )
        else:
            child.expect_exact(
                probe_text,
                timeout=self.args.timeout,
                searchwindowsize=search_window,
            )
        end_ns = time.perf_counter_ns()
        if erase_after_echo:
            self._erase_probe_chars(child, len(probe_text), erase_key=erase_key)
        return (end_ns - start_ns) / 1_000_000.0

    @staticmethod
    def _recover_nano_state(child: pexpect.spawn) -> None:
        child.sendcontrol("l")

    @staticmethod
    def _recover_shell_state(child: pexpect.spawn) -> None:
        child.sendcontrol("c")

    @staticmethod
    def _recover_vim_state(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.sendcontrol("l")
        child.send("i")

    def _reopen_session_for_sample_failure(
        self,
        child: pexpect.spawn,
        protocol: str,
    ) -> pexpect.spawn:
        self._close_session(child)
        reopened_child, _ = self._open_session(protocol)
        return reopened_child

    def _enter_vim_insert_mode(self, child: pexpect.spawn) -> None:
        remote_file = self.args.remote_vim_file
        child.sendline(f"vim -Nu NONE -n {shlex.quote(remote_file)}")
        child.send("i")
        if self._tmux_attach_mode():
            last_exc: Optional[pexpect.TIMEOUT] = None
            for _ in range(3):
                try:
                    self._recover_vim_state(child)
                    self._probe_once(child, erase_after_echo=True)
                    return
                except pexpect.TIMEOUT as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
        child.expect([r"-- INSERT --", r"INSERT"], timeout=self.args.timeout)

    def _enter_nano_mode(self, child: pexpect.spawn) -> None:
        remote_file = self.args.remote_nano_file
        child.sendline(f"nano --ignorercfiles {shlex.quote(remote_file)}")
        if self._tmux_attach_mode():
            # Under 5-pane redraw, nano banners may be fragmented by ANSI moves.
            last_exc: Optional[pexpect.TIMEOUT] = None
            for _ in range(3):
                try:
                    self._recover_nano_state(child)
                    self._probe_once(child, erase_after_echo=True)
                    return
                except pexpect.TIMEOUT as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
        child.expect([r"GNU nano", r"\^G Help"], timeout=self.args.timeout)

    def _probe_vim_once(
        self,
        child: pexpect.spawn,
        erase_after_echo: bool = True,
    ) -> float:
        # Assumes Vim is already in Insert mode.
        return self._probe_once(child, erase_after_echo=erase_after_echo)

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

    def _open_session(self, protocol: str) -> tuple:
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
        )
        child.delaybeforesend = 0

        start_ns = time.perf_counter_ns()
        if self._tmux_attach_mode():
            if self._should_attach_after_login(protocol):
                child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
                self._attach_tmux_after_login(child)
            else:
                self._expect_tmux_boot_marker(child)
        else:
            child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child, protocol=protocol)

        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        if self._tmux_attach_mode():
            try:
                child.sendcontrol("b")
                child.send("d")
                child.expect(
                    pexpect.EOF,
                    timeout=max(1, min(3, self.args.timeout)),
                )
                return
            except Exception:
                pass

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

    def _measure_interactive_shell(
        self,
        child: pexpect.spawn,
        protocol: str,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
        fail_cb: Optional[Callable[[int, Exception], None]] = None,
    ) -> tuple[List[float], pexpect.spawn]:
        self._refresh_prompt(child, protocol=protocol)
        cleanup_batch = max(1, self.args.editor_cleanup_batch)
        pending_chars = 0
        erase_per_probe = self._tmux_attach_mode()
        fail_streak = 0
        fail_limit = max(0, self.args.tmux_fail_streak_limit)
        fail_total = 0
        fail_trial_limit = max(0, self.args.tmux_trial_fail_limit)

        for _ in range(warmup):
            try:
                self._probe_once(child, erase_after_echo=erase_per_probe)
            except pexpect.TIMEOUT:
                self._recover_shell_state(child)
                self._refresh_prompt(child, protocol=protocol)
                continue
            if erase_per_probe:
                continue
            pending_chars += 1
            if pending_chars >= cleanup_batch:
                self._erase_probe_chars(child, pending_chars)
                pending_chars = 0
                self._drain_pending_output(child)

        # Keep warmup side effects outside measured rounds.
        if not erase_per_probe and pending_chars > 0:
            self._erase_probe_chars(child, pending_chars)
            pending_chars = 0
            self._drain_pending_output(child)
        self._refresh_prompt(child, protocol=protocol)

        latencies: List[float] = []
        for i in range(iterations):
            try:
                lat = self._probe_once(child, erase_after_echo=erase_per_probe)
            except pexpect.TIMEOUT as exc:
                fail_streak += 1
                fail_total += 1
                if fail_cb:
                    fail_cb(i + 1, exc)
                if self.args.reopen_on_failure:
                    child = self._reopen_session_for_sample_failure(child, protocol)
                else:
                    self._recover_shell_state(child)
                self._refresh_prompt(child, protocol=protocol)
                if (
                    self._tmux_attach_mode()
                    and fail_trial_limit > 0
                    and fail_total >= fail_trial_limit
                ):
                    raise ValueError(
                        f"too many TIMEOUT failures ({fail_total}) in interactive_shell"
                    )
                if self._tmux_attach_mode() and fail_limit > 0 and fail_streak >= fail_limit:
                    raise ValueError(
                        f"too many consecutive TIMEOUTs ({fail_streak}) in interactive_shell"
                    )
                continue
            fail_streak = 0
            if not erase_per_probe:
                pending_chars += 1
            if not erase_per_probe and pending_chars >= cleanup_batch:
                self._erase_probe_chars(child, pending_chars)
                pending_chars = 0
                self._drain_pending_output(child)
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        if not erase_per_probe and pending_chars > 0:
            self._erase_probe_chars(child, pending_chars)
            self._drain_pending_output(child)

        self._refresh_prompt(child, protocol=protocol)
        return latencies, child

    def _measure_vim(
        self,
        child: pexpect.spawn,
        protocol: str,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
        fail_cb: Optional[Callable[[int, Exception], None]] = None,
    ) -> tuple[List[float], pexpect.spawn]:
        self._enter_vim_insert_mode(child)
        erase_per_probe = self._tmux_attach_mode()
        fail_streak = 0
        fail_limit = max(0, self.args.tmux_fail_streak_limit)
        fail_total = 0
        fail_trial_limit = max(0, self.args.tmux_trial_fail_limit)

        for _ in range(warmup):
            try:
                self._probe_vim_once(
                    child,
                    erase_after_echo=erase_per_probe,
                )
            except pexpect.TIMEOUT:
                self._recover_vim_state(child)
                continue

        latencies: List[float] = []
        for i in range(iterations):
            try:
                lat = self._probe_vim_once(
                    child,
                    erase_after_echo=erase_per_probe,
                )
            except pexpect.TIMEOUT as exc:
                fail_streak += 1
                fail_total += 1
                if fail_cb:
                    fail_cb(i + 1, exc)
                if self.args.reopen_on_failure:
                    child = self._reopen_session_for_sample_failure(child, protocol)
                    self._enter_vim_insert_mode(child)
                else:
                    self._recover_vim_state(child)
                if (
                    self._tmux_attach_mode()
                    and fail_trial_limit > 0
                    and fail_total >= fail_trial_limit
                ):
                    raise ValueError(
                        f"too many TIMEOUT failures ({fail_total}) in vim"
                    )
                if self._tmux_attach_mode() and fail_limit > 0 and fail_streak >= fail_limit:
                    raise ValueError(
                        f"too many consecutive TIMEOUTs ({fail_streak}) in vim"
                    )
                continue
            fail_streak = 0
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.send("\x1b")
        child.sendline(":q!")
        self._expect_prompt(child, protocol=protocol)
        return latencies, child

    def _measure_nano(
        self,
        child: pexpect.spawn,
        protocol: str,
        warmup: int,
        iterations: int,
        report_cb: Optional[Callable[[int, float], None]] = None,
        fail_cb: Optional[Callable[[int, Exception], None]] = None,
    ) -> tuple[List[float], pexpect.spawn]:
        self._enter_nano_mode(child)
        erase_per_probe = self._tmux_attach_mode()
        fail_streak = 0
        fail_limit = max(0, self.args.tmux_fail_streak_limit)
        fail_total = 0
        fail_trial_limit = max(0, self.args.tmux_trial_fail_limit)

        for _ in range(warmup):
            try:
                self._probe_once(
                    child,
                    erase_after_echo=erase_per_probe,
                )
            except pexpect.TIMEOUT:
                self._recover_nano_state(child)
                continue

        latencies: List[float] = []
        for i in range(iterations):
            try:
                lat = self._probe_once(
                    child,
                    erase_after_echo=erase_per_probe,
                )
            except pexpect.TIMEOUT as exc:
                fail_streak += 1
                fail_total += 1
                if fail_cb:
                    fail_cb(i + 1, exc)
                if self.args.reopen_on_failure:
                    child = self._reopen_session_for_sample_failure(child, protocol)
                    self._enter_nano_mode(child)
                else:
                    self._recover_nano_state(child)
                if (
                    self._tmux_attach_mode()
                    and fail_trial_limit > 0
                    and fail_total >= fail_trial_limit
                ):
                    raise ValueError(
                        f"too many TIMEOUT failures ({fail_total}) in nano"
                    )
                if self._tmux_attach_mode() and fail_limit > 0 and fail_streak >= fail_limit:
                    raise ValueError(
                        f"too many consecutive TIMEOUTs ({fail_streak}) in nano"
                    )
                continue
            fail_streak = 0
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("x")
        child.send("n")
        self._expect_prompt(child, protocol=protocol)
        return latencies, child

    def _run_trial(
        self,
        child: pexpect.spawn,
        protocol: str,
        workload: str,
        trial_id: int,
    ) -> tuple[List[float], pexpect.spawn]:
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
                flush=True
            )

        def fail_cb(s_idx: int, exc: Exception) -> None:
            self.failures.append(
                FailureRecord(
                    protocol=protocol,
                    workload=workload,
                    round_id=trial_id,
                    sample_id=s_idx,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {s_idx:>3}/{self.args.iterations}:"
                f" FAIL ({type(exc).__name__})",
                flush=True
            )

        if workload == "interactive_shell":
            latencies, child = self._measure_interactive_shell(
                child,
                protocol=protocol,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
                fail_cb=fail_cb,
            )
        elif workload == "vim":
            latencies, child = self._measure_vim(
                child,
                protocol=protocol,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
                fail_cb=fail_cb,
            )
        elif workload == "nano":
            latencies, child = self._measure_nano(
                child,
                protocol=protocol,
                warmup=self.args.warmup_rounds,
                iterations=self.args.iterations,
                report_cb=report_cb,
                fail_cb=fail_cb,
            )
        else:
            raise ValueError(f"Unsupported workload: {workload}")

        return latencies, child

    def _run_session_group(self, protocol: str, workload: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" opening session...",
                    flush=True,
                )
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][workload].append(setup_ms)
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms"
                )

                try:
                    _, child = self._run_trial(
                        child, protocol, workload, trial_id
                    )
                except (pexpect.TIMEOUT, pexpect.EOF, ValueError) as exc:
                    self.failures.append(
                        FailureRecord(
                            protocol=protocol,
                            workload=workload,
                            round_id=trial_id,
                            sample_id=-1,
                            error_type=type(exc).__name__,
                            error_message=str(exc),
                        )
                    )
                    print(
                        f"[{protocol:>4}/{workload:<18}]"
                        f" trial {trial_id:>2}: FAIL"
                        f" ({type(exc).__name__}: {exc})"
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
        expected_n = self.args.trials * self.args.iterations
        n = len(data)
        fail_n = max(0, expected_n - n)
        success_rate = (100.0 * n / expected_n) if expected_n else 0.0

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
            "[spawn -> first shell prompt, PS1 export excluded]"
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
    p = argparse.ArgumentParser(description="W3 Interactive Editing benchmark")
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
        "--warmup-rounds", type=int, default=10,
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
        "--probe-chars", default=PROBE_CHAR_ALPHABET,
        help="Alphanumeric character pool for random single-character probes",
    )
    p.add_argument(
        "--probe-search-window", type=int, default=0,
        help="Bytes scanned when matching probe echo (0 = full buffer search)",
    )
    p.add_argument(
        "--editor-cleanup-batch", type=int, default=32,
        help="Typed chars between non-measured cleanup operations in interactive_shell",
    )
    p.add_argument(
        "--tmux-fail-streak-limit", type=int, default=8,
        help=(
            "Abort a tmux-mode trial early after this many consecutive "
            "TIMEOUTs (0 disables the limit)"
        ),
    )
    p.add_argument(
        "--tmux-trial-fail-limit", type=int, default=35,
        help=(
            "Abort a tmux-mode trial early after this many total TIMEOUT "
            "failures (0 disables the limit)"
        ),
    )
    p.add_argument(
        "--tmux-probe-max-gap", type=int, default=256,
        help=(
            "Max arbitrary bytes allowed between probe token characters in "
            "tmux-mode matching"
        ),
    )
    p.add_argument(
        "--tmux-search-window", type=int, default=32768,
        help=(
            "searchwindowsize for tmux-mode expect/expect_exact matches "
            "(0 = full buffer search); "
            "larger values are more tolerant but slower"
        ),
    )
    p.add_argument(
        "--output-dir", default="w3_results",
        help="Directory for JSON/CSV outputs",
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
        "--mosh-predict", default="always",
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
        help="Reopen session immediately after each failed measured sample",
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
    if args.editor_cleanup_batch <= 0:
        parser.error("--editor-cleanup-batch must be > 0")
    if args.probe_search_window < 0:
        parser.error("--probe-search-window must be >= 0")
    if args.tmux_fail_streak_limit < 0:
        parser.error("--tmux-fail-streak-limit must be >= 0")
    if args.tmux_trial_fail_limit < 0:
        parser.error("--tmux-trial-fail-limit must be >= 0")
    if args.tmux_probe_max_gap <= 0:
        parser.error("--tmux-probe-max-gap must be > 0")
    if args.tmux_search_window < 0:
        parser.error("--tmux-search-window must be >= 0")

    bench = W3Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
