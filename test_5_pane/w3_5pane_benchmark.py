#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
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
DEFAULT_PROMPT = "__W3_PROMPT__#"
DEFAULT_SSH3_PATH = "/ssh3-term"
DEFAULT_TMUX_SESSION = "w3bench5"
DEFAULT_TMUX_SETUP_SCRIPT = str(Path(__file__).with_name("w3_tmux_setup.sh"))
DEFAULT_REMOTE_TMUX_SETUP = "/tmp/w3_tmux_setup.sh"

PROBE_TOKEN_PREFIX = "W3_PROBE_FIXED_Q9J5V2K7M4T8X1"
DEFAULT_PROBE_TAIL_LEN = 12

_ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
_ECHO_GAP = rf"(?:{_ANSI_SEQ}|[\r\n\b])*"
_ANSI_RE = re.compile(_ANSI_SEQ)
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


class W3Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.prompt_marker = args.prompt.rstrip()
        if not self.prompt_marker:
            raise ValueError("Prompt must contain at least one non-space character")
        self.prompt_re = self._build_prompt_re(self.prompt_marker)
        self.probe_prefix = PROBE_TOKEN_PREFIX
        self.probe_seq = 0
        self.records: List[SampleRecord] = []
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
        return re.compile("".join(parts) + rf"(?:{_ANSI_SEQ}|\s)*")

    def _next_probe_token(self) -> str:
        self.probe_seq += 1
        return f"{self.probe_prefix}_{self.probe_seq:08d}"

    def _expect_prompt(self, child: pexpect.spawn) -> None:
        # TUI redraw (especially over mosh) may split prompt bytes with ANSI updates.
        child.expect(self.prompt_re, timeout=self.args.timeout)

    def _expect_prompt_resync(self, child: pexpect.spawn, phase: str) -> None:
        attempts = self.args.prompt_resync_attempts
        last_error: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            try:
                self._expect_prompt(child)
                return
            except pexpect.TIMEOUT as exc:
                last_error = exc
                if attempt >= attempts:
                    raise pexpect.TIMEOUT(
                        f"{phase}: prompt not observed after {attempts} attempts"
                    ) from exc
                # Force another prompt emission attempt in noisy tmux redraws.
                child.sendcontrol("c")
                child.sendline("")
        if last_error:
            raise last_error

    @staticmethod
    def _strip_ansi_and_ctrl(text: str) -> str:
        text = _ANSI_RE.sub("", text)
        text = text.replace("\r", "").replace("\n", "").replace("\b", "")
        return text

    def _expect_probe_echo(self, child: pexpect.spawn, token: str) -> None:
        targets = [token]
        tail_len = min(len(token), self.args.probe_tail_len)
        tail = token[-tail_len:]
        if tail and tail != token:
            targets.append(tail)
        self._expect_subsequence_any(
            child,
            targets,
            phase="probe_echo",
            timeout=float(self.args.timeout),
        )

    def _expect_subsequence_any(
        self,
        child: pexpect.spawn,
        targets: List[str],
        phase: str,
        timeout: float,
    ) -> str:
        if not targets:
            raise ValueError(f"{phase}: targets must not be empty")

        deadline = time.monotonic() + timeout

        # tmux can interleave redraw text from other panes between chars.
        # Match target as ordered subsequence on ANSI-stripped stream.
        normalized_targets = [t for t in targets if t]
        if not normalized_targets:
            raise ValueError(f"{phase}: all targets are empty")

        progress = [0] * len(normalized_targets)
        max_progress = [0] * len(normalized_targets)

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                progress_note = ", ".join(
                    f"{t}:{m}/{len(t)}"
                    for t, m in zip(normalized_targets, max_progress)
                )
                raise pexpect.TIMEOUT(
                    f"{phase}: none of targets observed before timeout: "
                    f"{normalized_targets!r}; progress={progress_note}"
                )
            try:
                chunk = child.read_nonblocking(
                    size=self.args.probe_read_size,
                    timeout=min(self.args.probe_poll_timeout, remaining),
                )
            except pexpect.TIMEOUT:
                continue

            normalized = self._strip_ansi_and_ctrl(chunk)
            if not normalized:
                continue

            for ch in normalized:
                for i, target in enumerate(normalized_targets):
                    cur = progress[i]
                    if ch == target[cur]:
                        cur += 1
                        progress[i] = cur
                        if cur > max_progress[i]:
                            max_progress[i] = cur
                        if cur >= len(target):
                            return target
                    elif ch == target[0]:
                        # Restart matching from this char for robustness.
                        progress[i] = 1
                        if max_progress[i] < 1:
                            max_progress[i] = 1

    @staticmethod
    def _ensure_vim_insert_mode(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.send("i")

    @staticmethod
    def _erase_probe_token(child: pexpect.spawn, token: str) -> None:
        if token:
            child.send("\x7f" * len(token))

    @staticmethod
    def _drain_pending_output(child: pexpect.spawn, max_reads: int = 8) -> None:
        for _ in range(max_reads):
            try:
                child.read_nonblocking(size=4096, timeout=0)
            except (pexpect.TIMEOUT, pexpect.EOF):
                break

    def _probe_once(self, child: pexpect.spawn, erase_after_echo: bool = False) -> float:
        probe_token = self._next_probe_token()
        self._drain_pending_output(child)
        start_ns = time.perf_counter_ns()
        child.send(probe_token)
        self._expect_probe_echo(child, probe_token)
        end_ns = time.perf_counter_ns()
        if erase_after_echo:
            self._erase_probe_token(child, probe_token)
        return (end_ns - start_ns) / 1_000_000.0

    @staticmethod
    def _recover_nano_state(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.sendcontrol("l")

    @staticmethod
    def _recover_vim_state(child: pexpect.spawn) -> None:
        child.send("\x1b")
        child.sendcontrol("l")
        child.send("i")

    def _resolve_tmux_setup_script_path(self) -> Path:
        direct = Path(self.args.tmux_setup_script)
        if direct.exists():
            return direct

        local_name = Path(self.args.tmux_setup_script).name
        beside_script = Path(__file__).with_name(local_name)
        if beside_script.exists():
            return beside_script

        raise ValueError(
            "tmux setup script not found: "
            f"{self.args.tmux_setup_script} (also tried {beside_script})"
        )

    def _upload_tmux_setup_script(self, child: pexpect.spawn) -> None:
        local_path = self._resolve_tmux_setup_script_path()
        script_content = local_path.read_text(encoding="utf-8")

        token_base = "__W3_TMUX_SETUP_EOF__"
        token = token_base
        counter = 1
        while token in script_content:
            token = f"{token_base}_{counter}"
            counter += 1

        remote_path = self.args.remote_tmux_setup
        child.sendline(f"cat > {shlex.quote(remote_path)} <<'{token}'")
        if not script_content.endswith("\n"):
            script_content += "\n"
        child.send(script_content)
        child.sendline(token)
        self._expect_prompt(child)

        child.sendline(f"chmod +x {shlex.quote(remote_path)}")
        self._expect_prompt(child)

    def _setup_tmux_5pane(self, child: pexpect.spawn) -> None:
        self._upload_tmux_setup_script(child)
        session = self.args.tmux_session
        session_q = shlex.quote(session)
        remote_path = shlex.quote(self.args.remote_tmux_setup)
        child.sendline(f"NO_ATTACH=1 {remote_path} {session_q}")
        self._expect_prompt(child)

        # Keep benchmark input pinned to pane 0 and avoid accidental broadcast.
        child.sendline(f"tmux select-pane -t {shlex.quote(f'{session}:0.0')}")
        self._expect_prompt(child)
        child.sendline(
            "tmux set-window-option"
            f" -t {shlex.quote(f'{session}:0')}"
            " synchronize-panes off"
        )
        self._expect_prompt(child)

    def _attach_tmux_5pane(self, child: pexpect.spawn) -> None:
        child.sendline(f"tmux attach -d -t {shlex.quote(self.args.tmux_session)}")
        ready_marker = f"__W3_ATTACH_READY__{time.time_ns()}__"
        child.sendline(f"printf '{ready_marker}\\n'")
        self._expect_subsequence_any(
            child,
            [ready_marker],
            phase="tmux_attach_ready",
            timeout=self.args.attach_timeout,
        )

    def _detach_tmux(self, child: pexpect.spawn) -> None:
        child.send("\x02")
        child.send("d")
        self._expect_prompt(child)

    def _kill_tmux_session(self, child: pexpect.spawn) -> None:
        child.sendline(
            f"tmux kill-session -t {shlex.quote(self.args.tmux_session)}"
            " 2>/dev/null || true"
        )
        self._expect_prompt(child)

    def _probe_vim_once(self, child: pexpect.spawn) -> float:
        self._ensure_vim_insert_mode(child)
        return self._probe_once(child, erase_after_echo=True)

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

    def _open_session(self, protocol: str) -> tuple:
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
        )

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1={shlex.quote(self.args.prompt)}")
        self._expect_prompt(child)

        return child, setup_ms

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=self.args.close_timeout)
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
            self._probe_once(child)

        latencies: List[float] = []
        for i in range(iterations):
            lat = self._probe_once(child)
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("c")
        child.sendcontrol("c")
        self._expect_prompt_resync(child, "interactive_shell_exit")
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
        self._expect_subsequence_any(
            child,
            ["-- INSERT --", "INSERT"],
            phase="vim_start",
            timeout=self.args.app_start_timeout,
        )

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
        if self.args.check_vim_exit_prompt:
            self._expect_prompt_resync(child, "vim_exit")
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
        self._expect_subsequence_any(
            child,
            ["GNU nano", "^G Help"],
            phase="nano_start",
            timeout=self.args.app_start_timeout,
        )

        if self.args.nano_settle_seconds > 0:
            time.sleep(self.args.nano_settle_seconds)

        def probe_nano_with_recovery() -> float:
            last_exc: Optional[Exception] = None
            for _ in range(self.args.nano_probe_retries):
                try:
                    return self._probe_once(child, erase_after_echo=True)
                except pexpect.TIMEOUT as exc:
                    last_exc = exc
                    self._recover_nano_state(child)
            if last_exc:
                raise last_exc
            raise pexpect.TIMEOUT("nano probe failed without exception detail")

        for _ in range(warmup):
            probe_nano_with_recovery()

        latencies: List[float] = []
        for i in range(iterations):
            lat = probe_nano_with_recovery()
            latencies.append(lat)
            if report_cb:
                report_cb(i + 1, lat)

        child.sendcontrol("x")
        child.send("n")
        self._expect_prompt_resync(child, "nano_exit")
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
            self.records.append(SampleRecord(protocol, workload, trial_id, s_idx, lat))
            print(
                f"[{protocol:>4}/{workload:<18}]"
                f" trial {trial_id:>2}"
                f" measure {s_idx:>3}/{self.args.iterations}:"
                f" {lat:.2f} ms",
                flush=True,
            )

        def run_workload() -> List[float]:
            if workload == "interactive_shell":
                return self._measure_interactive_shell(
                    child,
                    warmup=self.args.warmup_rounds,
                    iterations=self.args.iterations,
                    report_cb=report_cb,
                )
            if workload == "vim":
                return self._measure_vim(
                    child,
                    warmup=self.args.warmup_rounds,
                    iterations=self.args.iterations,
                    report_cb=report_cb,
                )
            if workload == "nano":
                return self._measure_nano(
                    child,
                    warmup=self.args.warmup_rounds,
                    iterations=self.args.iterations,
                    report_cb=report_cb,
                )
            raise ValueError(f"Unsupported workload: {workload}")

        if not self.args.tmux_load:
            return run_workload()

        attached = False
        self._setup_tmux_5pane(child)
        try:
            self._attach_tmux_5pane(child)
            attached = True
            return run_workload()
        finally:
            if attached:
                try:
                    self._detach_tmux(child)
                except Exception:
                    pass
            if not self.args.tmux_keep_session:
                try:
                    self._kill_tmux_session(child)
                except Exception:
                    pass

    def _run_session_group(self, protocol: str, workload: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            child: Optional[pexpect.spawn] = None
            try:
                child, setup_ms = self._open_session(protocol)
                self.session_setups[protocol][workload].append(setup_ms)
                print(
                    f"[{protocol:>4}/{workload:<18}]"
                    f" trial {trial_id:>2}/{self.args.trials}"
                    f" session_setup={setup_ms:.1f} ms"
                )

                try:
                    self._run_trial(child, protocol, workload, trial_id)
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
                    if self.args.reopen_on_failure:
                        if child is not None:
                            self._close_session(child)
                        child, _ = self._open_session(protocol)
            finally:
                if child is not None:
                    self._close_session(child)

    def run(self) -> None:
        random.seed(self.args.seed)
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
        fail_n = sum(
            1 for f in self.failures if f.protocol == protocol and f.workload == workload
        )
        n = len(data)
        total = n + fail_n
        success_rate = (100.0 * n / total) if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, workload, 0, fail_n, success_rate,
                None, None, None, None, None, None, None, None,
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

        line_csv = outdir / "w3_5pane_line_log.csv"
        setup_csv = outdir / "w3_5pane_session_setup.csv"

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
            writer.writerow(["protocol", "workload", "trial_id", "session_setup_ms"])
            for p in self.args.protocols:
                for w in self.args.workloads:
                    for trial_id, ms in enumerate(self.session_setups[p][w], start=1):
                        writer.writerow([p, w, trial_id, f"{ms:.6f}"])

        print(f"Saved line log CSV    : {line_csv}")
        print(f"Saved session setup   : {setup_csv}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W3 Interactive Editing benchmark (5-pane)")
    p.add_argument(
        "--host",
        default="192.168.8.102",
        help="Target host IP or hostname",
    )
    p.add_argument(
        "--user",
        default="trungnt",
        help="Remote username",
    )
    p.add_argument(
        "--source-ip",
        default="192.168.8.100",
        help="Client source IP for SSH / Mosh where supported",
    )
    p.add_argument(
        "--identity-file",
        default=str(Path.home() / ".ssh" / "id_rsa"),
        help="SSH private key path",
    )
    p.add_argument(
        "--protocols",
        nargs="+",
        default=DEFAULT_PROTOCOLS,
        choices=DEFAULT_PROTOCOLS,
    )
    p.add_argument(
        "--workloads",
        nargs="+",
        default=DEFAULT_WORKLOADS,
        choices=DEFAULT_WORKLOADS,
    )
    p.add_argument(
        "--trials",
        type=int,
        default=15,
        help="Independent sessions per protocol/workload pair",
    )
    p.add_argument(
        "--iterations",
        type=int,
        default=100,
        help="Recorded samples per trial",
    )
    p.add_argument(
        "--warmup-rounds",
        type=int,
        default=5,
        help="Warmup samples per trial (not recorded)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="pexpect timeout in seconds",
    )
    p.add_argument(
        "--prompt-resync-attempts",
        type=int,
        default=4,
        help="Retries when waiting prompt in noisy tmux redraw phases",
    )
    p.add_argument(
        "--probe-read-size",
        type=int,
        default=4096,
        help="read_nonblocking size when scanning probe token",
    )
    p.add_argument(
        "--probe-poll-timeout",
        type=float,
        default=0.25,
        help="per-read timeout while waiting probe token (seconds)",
    )
    p.add_argument(
        "--probe-tail-len",
        type=int,
        default=DEFAULT_PROBE_TAIL_LEN,
        help="allow matching only the trailing N chars of probe token",
    )
    p.add_argument(
        "--app-start-timeout",
        type=float,
        default=30.0,
        help="timeout waiting startup markers for vim/nano (seconds)",
    )
    p.add_argument(
        "--nano-settle-seconds",
        type=float,
        default=0.2,
        help="small settle delay after nano startup before probing",
    )
    p.add_argument(
        "--nano-probe-retries",
        type=int,
        default=3,
        help="retries per nano probe sample before declaring timeout",
    )
    p.add_argument(
        "--attach-timeout",
        type=float,
        default=30.0,
        help="timeout waiting tmux attach handshake marker (seconds)",
    )
    p.add_argument(
        "--close-timeout",
        type=float,
        default=3.0,
        help="max wait before forcing session close (seconds)",
    )
    p.add_argument(
        "--check-vim-exit-prompt",
        action="store_true",
        help="strictly require shell prompt detection right after :q! in vim",
    )
    p.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed",
    )
    p.add_argument(
        "--output-dir",
        default="w3_5pane_results",
        help="Directory for CSV outputs",
    )
    p.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Unique shell prompt marker used after session is ready",
    )
    p.add_argument(
        "--ssh3-path",
        default=DEFAULT_SSH3_PATH,
        help="SSH3 terminal path suffix",
    )
    p.add_argument(
        "--ssh3-insecure",
        action="store_true",
        help="Pass -insecure to ssh3",
    )
    p.add_argument(
        "--batch-mode",
        action="store_true",
        help="Enable BatchMode for SSHv2 / Mosh bootstrap SSH",
    )
    p.add_argument(
        "--strict-host-key-checking",
        action="store_true",
        help="Keep strict host key checking enabled",
    )
    p.add_argument(
        "--mosh-predict",
        default="adaptive",
        choices=["adaptive", "always", "never"],
        help="Mosh prediction mode",
    )
    p.add_argument(
        "--remote-vim-file",
        default="/tmp/w3_vim_bench.txt",
        help="Remote file path used for the vim workload",
    )
    p.add_argument(
        "--remote-nano-file",
        default="/tmp/w3_nano_bench.txt",
        help="Remote file path used for the nano workload",
    )
    p.add_argument(
        "--tmux-load",
        action="store_true",
        help="Enable 5-pane tmux background load for all workloads",
    )
    p.add_argument(
        "--tmux-session",
        default=DEFAULT_TMUX_SESSION,
        help="tmux session name used for background load",
    )
    p.add_argument(
        "--tmux-keep-session",
        action="store_true",
        help="Keep tmux session after workload for inspection",
    )
    p.add_argument(
        "--tmux-setup-script",
        default=DEFAULT_TMUX_SETUP_SCRIPT,
        help="Local tmux setup script for background load",
    )
    p.add_argument(
        "--remote-tmux-setup",
        default=DEFAULT_REMOTE_TMUX_SETUP,
        help="Remote path for the tmux setup script",
    )
    p.add_argument(
        "--shuffle-pairs",
        action="store_true",
        help="Shuffle protocol/workload execution order",
    )
    p.add_argument(
        "--reopen-on-failure",
        action="store_true",
        help="Reopen session after each failed measured sample",
    )
    p.add_argument(
        "--log-pexpect",
        action="store_true",
        help="Deprecated compatibility flag (no-op): pexpect logs are disabled",
    )
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.trials <= 0:
        parser.error("--trials must be > 0")
    if args.iterations <= 0:
        parser.error("--iterations must be > 0")
    if args.warmup_rounds < 0:
        parser.error("--warmup-rounds must be >= 0")
    if args.prompt_resync_attempts <= 0:
        parser.error("--prompt-resync-attempts must be > 0")
    if args.probe_read_size <= 0:
        parser.error("--probe-read-size must be > 0")
    if args.probe_poll_timeout <= 0:
        parser.error("--probe-poll-timeout must be > 0")
    if args.probe_tail_len <= 0:
        parser.error("--probe-tail-len must be > 0")
    if args.app_start_timeout <= 0:
        parser.error("--app-start-timeout must be > 0")
    if args.nano_settle_seconds < 0:
        parser.error("--nano-settle-seconds must be >= 0")
    if args.nano_probe_retries <= 0:
        parser.error("--nano-probe-retries must be > 0")
    if args.attach_timeout <= 0:
        parser.error("--attach-timeout must be > 0")
    if args.close_timeout <= 0:
        parser.error("--close-timeout must be > 0")

    bench = W3Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
