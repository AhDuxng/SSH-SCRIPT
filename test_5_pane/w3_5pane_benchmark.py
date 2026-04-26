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
from typing import Dict, List, Optional

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc

DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_WORKLOADS = ["interactive_shell", "vim", "nano"]
DEFAULT_PROMPT = "__W3_PROMPT__# "
DEFAULT_SSH3_PATH = "/ssh3-term"

TMUX_SESSION = "w3bench5"
TMUX_SETUP_SCRIPT = "w3_tmux_setup.sh"
REMOTE_TMUX_SETUP = "/tmp/w3_tmux_setup.sh"
PANE_READY_MARKER = "__W3_5PANE_PANE0_READY__"
PANE_POLL_INTERVAL_SEC = 0.05

PROBE_TOKEN = "W3_PROBE_FIXED_Q9J5V2K7M4T8X1"
PROBE_TAIL_LEN = 10

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


class W35PaneBenchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.prompt_re = self._build_probe_echo_re(self.args.prompt)

        self.probe_token = PROBE_TOKEN
        self.probe_tail = self.probe_token[-PROBE_TAIL_LEN:]
        self.probe_echo_re = self._build_probe_echo_re(self.probe_token)
        self.probe_tail_echo_re = self._build_probe_echo_re(self.probe_tail)

        self.records: List[SampleRecord] = []
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

    def _session_command(self, protocol: str) -> str:
        target = self.target
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
        if self.args.log_pexpect:
            log_path = Path(self.args.output_dir) / f"pexpect_{protocol}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child.logfile_read = open(log_path, "a", encoding="utf-8")

        start_ns = time.perf_counter_ns()
        child.expect(_INITIAL_PROMPT_RE, timeout=self.args.timeout)
        setup_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

        child.sendline(f"export PS1='{self.args.prompt}'")
        child.expect(self.prompt_re, timeout=self.args.timeout)

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

    def _run_remote(self, child: pexpect.spawn, command: str) -> str:
        child.sendline(command)
        child.expect(self.prompt_re, timeout=self.args.timeout)
        return child.before

    def _tmux_target(self) -> str:
        return f"{self.args.tmux_session}:0.0"

    def _copy_tmux_setup_to_remote(self, child: pexpect.spawn) -> None:
        local_script = Path(self.args.tmux_setup_script)
        if not local_script.exists():
            raise FileNotFoundError(f"tmux setup script not found: {local_script}")

        script_body = local_script.read_text(encoding="utf-8")
        remote_path = self.args.remote_tmux_setup

        self._run_remote(
            child,
            f"cat > {shlex.quote(remote_path)} << 'W3TMUXEOF'\n"
            f"{script_body}\n"
            "W3TMUXEOF",
        )
        self._run_remote(child, f"chmod +x {shlex.quote(remote_path)}")

    def _start_tmux_session(self, child: pexpect.spawn) -> None:
        session = self.args.tmux_session
        self._run_remote(
            child,
            f"tmux kill-session -t {shlex.quote(session)} 2>/dev/null || true",
        )
        self._run_remote(
            child,
            f"NO_ATTACH=1 bash {shlex.quote(self.args.remote_tmux_setup)}"
            f" {shlex.quote(session)} >/tmp/w3_5pane_tmux.log 2>&1 &",
        )

        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            out = self._run_remote(
                child,
                f"tmux has-session -t {shlex.quote(session)} 2>/dev/null && echo __OK__ || echo __WAIT__",
            )
            if "__OK__" in out:
                break
            time.sleep(PANE_POLL_INTERVAL_SEC)
        else:
            raise RuntimeError(f"tmux session '{session}' did not start in time")

        # Wait until setup script emits readiness marker into pane 0.
        self._wait_pane_contains(child, PANE_READY_MARKER, timeout=self.args.timeout)

    def _stop_tmux_session(self, child: pexpect.spawn) -> None:
        self._run_remote(
            child,
            f"tmux kill-session -t {shlex.quote(self.args.tmux_session)} 2>/dev/null || true",
        )

    def _tmux_send_literal(self, child: pexpect.spawn, text: str) -> None:
        self._run_remote(
            child,
            f"tmux send-keys -t {shlex.quote(self._tmux_target())} -l {shlex.quote(text)}",
        )

    def _tmux_send_key(self, child: pexpect.spawn, key: str) -> None:
        self._run_remote(
            child,
            f"tmux send-keys -t {shlex.quote(self._tmux_target())} {key}",
        )

    def _tmux_send_enter(self, child: pexpect.spawn) -> None:
        self._tmux_send_key(child, "Enter")

    def _capture_pane_text(self, child: pexpect.spawn, lines: int = 120) -> str:
        out = self._run_remote(
            child,
            f"tmux capture-pane -p -t {shlex.quote(self._tmux_target())} -S -{int(lines)}",
        )
        return out

    def _wait_pane_contains(self, child: pexpect.spawn, text: str, timeout: int) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snap = self._capture_pane_text(child)
            if text in snap:
                return
            time.sleep(PANE_POLL_INTERVAL_SEC)
        raise pexpect.TIMEOUT(f"Pane did not contain expected text: {text}")

    def _wait_probe_echo(self, child: pexpect.spawn) -> None:
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            snap = self._capture_pane_text(child)
            if self.probe_echo_re.search(snap) or self.probe_tail_echo_re.search(snap):
                return
            time.sleep(PANE_POLL_INTERVAL_SEC)
        raise pexpect.TIMEOUT("Probe echo not observed in pane capture")

    def _erase_probe_token(self, child: pexpect.spawn, token: str) -> None:
        for _ in token:
            self._tmux_send_key(child, "BSpace")

    def _ensure_vim_insert_mode(self, child: pexpect.spawn) -> None:
        self._tmux_send_key(child, "Escape")
        self._tmux_send_literal(child, "i")

    def _recover_vim_state(self, child: pexpect.spawn) -> None:
        self._tmux_send_key(child, "Escape")
        self._tmux_send_key(child, "C-l")
        self._tmux_send_literal(child, "i")

    def _recover_nano_state(self, child: pexpect.spawn) -> None:
        self._tmux_send_key(child, "C-l")

    def _probe_once(self, child: pexpect.spawn, erase_after_echo: bool = False) -> float:
        start_ns = time.perf_counter_ns()
        self._tmux_send_literal(child, self.probe_token)
        self._wait_probe_echo(child)
        end_ns = time.perf_counter_ns()
        if erase_after_echo:
            self._erase_probe_token(child, self.probe_token)
        return (end_ns - start_ns) / 1_000_000.0

    def _probe_vim_once(self, child: pexpect.spawn) -> float:
        self._ensure_vim_insert_mode(child)
        return self._probe_once(child, erase_after_echo=True)

    def _setup_workload_pane(self, child: pexpect.spawn, workload: str) -> None:
        self._tmux_send_key(child, "C-c")
        self._tmux_send_key(child, "C-c")

        if workload == "interactive_shell":
            self._tmux_send_literal(child, "cat")
            self._tmux_send_enter(child)
            time.sleep(0.1)
            return

        if workload == "vim":
            self._tmux_send_literal(
                child,
                f"vim -Nu NONE -n {shlex.quote(self.args.remote_vim_file)}",
            )
            self._tmux_send_enter(child)
            time.sleep(0.2)
            self._tmux_send_literal(child, "i")
            return

        if workload == "nano":
            self._tmux_send_literal(
                child,
                f"nano --ignorercfiles {shlex.quote(self.args.remote_nano_file)}",
            )
            self._tmux_send_enter(child)
            time.sleep(0.2)
            return

        raise ValueError(f"Unsupported workload: {workload}")

    def _teardown_workload_pane(self, child: pexpect.spawn, workload: str) -> None:
        if workload == "interactive_shell":
            self._tmux_send_key(child, "C-c")
            self._tmux_send_key(child, "C-c")
            return

        if workload == "vim":
            self._tmux_send_key(child, "Escape")
            self._tmux_send_literal(child, ":q!")
            self._tmux_send_enter(child)
            return

        if workload == "nano":
            self._tmux_send_key(child, "C-x")
            self._tmux_send_literal(child, "n")
            return

        raise ValueError(f"Unsupported workload: {workload}")

    def _measure_sample(self, child: pexpect.spawn, workload: str) -> float:
        if workload == "interactive_shell":
            return self._probe_once(child, erase_after_echo=False)

        if workload == "vim":
            try:
                return self._probe_vim_once(child)
            except pexpect.TIMEOUT:
                self._recover_vim_state(child)
                return self._probe_vim_once(child)

        if workload == "nano":
            try:
                return self._probe_once(child, erase_after_echo=True)
            except pexpect.TIMEOUT:
                self._recover_nano_state(child)
                return self._probe_once(child, erase_after_echo=True)

        raise ValueError(f"Unsupported workload: {workload}")

    def _run_trial(self, child: pexpect.spawn, protocol: str, workload: str, trial_id: int) -> None:
        self._setup_workload_pane(child, workload)
        try:
            for _ in range(self.args.warmup_rounds):
                self._measure_sample(child, workload)

            for s_idx in range(1, self.args.iterations + 1):
                lat = self._measure_sample(child, workload)
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
        finally:
            self._teardown_workload_pane(child, workload)

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

                self._copy_tmux_setup_to_remote(child)
                self._start_tmux_session(child)
                self._run_trial(child, protocol, workload, trial_id)

            except (pexpect.TIMEOUT, pexpect.EOF, ValueError, RuntimeError) as exc:
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
                    try:
                        self._stop_tmux_session(child)
                    except Exception:
                        pass
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

        summary_json = outdir / "w3_5pane_summary.json"
        raw_csv = outdir / "w3_5pane_raw_samples.csv"
        failures_csv = outdir / "w3_5pane_failures.csv"
        setup_csv = outdir / "w3_5pane_session_setup.csv"

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "workloads": self.args.workloads,
                "trials": self.args.trials,
                "iterations": self.args.iterations,
                "warmup_rounds": self.args.warmup_rounds,
                "timeout_sec": self.args.timeout,
                "random_seed": self.args.seed,
                "probe_token_mode": "fixed",
                "probe_token": self.probe_token,
                "probe_tail": self.probe_tail,
                "topology": {
                    "client": "192.168.8.100",
                    "server": self.args.host,
                },
                "load_environment": {
                    "type": "tmux_5_pane",
                    "tmux_session": self.args.tmux_session,
                    "pane_0": "interactive measurement target",
                    "pane_1": "heartbeat ~5 lines/s",
                    "pane_2": "burst stdout ~750 lines/s",
                    "pane_3": "ls-loop + clear",
                    "pane_4": "background writer + tail -f",
                },
                "metric_name": "interactive_echo_latency_ms",
                "metric_note": (
                    "Fixed probe token is sent to tmux pane 0. "
                    "Latency = time from send-keys(token) to token echo observation "
                    "via tmux capture-pane polling. This is echo latency, "
                    "NOT physical keyboard-to-screen latency."
                ),
                "session_setup_note": (
                    "setup_ms = time from pexpect.spawn() to first shell prompt "
                    "(regex [#$>]\\s*$). The tmux 5-pane bootstrapping is done "
                    "after this timing window."
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
            writer.writerow([
                "protocol", "workload", "round_id", "sample_id", "error_type", "error_message"
            ])
            for r in self.failures:
                writer.writerow([
                    r.protocol, r.workload, r.round_id, r.sample_id, r.error_type, r.error_message
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
    p = argparse.ArgumentParser(
        description="W3 Interactive benchmark (tmux 5-pane load, pane-0 measurement)"
    )
    p.add_argument("--host", default="192.168.8.102")
    p.add_argument("--user", default="trungnt")
    p.add_argument("--source-ip", default="192.168.8.100")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"))
    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS, choices=DEFAULT_PROTOCOLS)
    p.add_argument("--workloads", nargs="+", default=DEFAULT_WORKLOADS, choices=DEFAULT_WORKLOADS)
    p.add_argument("--trials", type=int, default=15)
    p.add_argument("--iterations", type=int, default=100)
    p.add_argument("--warmup-rounds", type=int, default=5)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-dir", default="w3_5pane_results")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH)
    p.add_argument("--ssh3-insecure", action="store_true")
    p.add_argument("--batch-mode", action="store_true")
    p.add_argument("--strict-host-key-checking", action="store_true")
    p.add_argument("--mosh-predict", default="adaptive", choices=["adaptive", "always", "never"])
    p.add_argument("--remote-vim-file", default="/tmp/w3_vim_bench.txt")
    p.add_argument("--remote-nano-file", default="/tmp/w3_nano_bench.txt")
    p.add_argument("--shuffle-pairs", action="store_true")
    p.add_argument("--log-pexpect", action="store_true")

    p.add_argument("--tmux-session", default=TMUX_SESSION)
    p.add_argument("--tmux-setup-script", default=TMUX_SETUP_SCRIPT)
    p.add_argument("--remote-tmux-setup", default=REMOTE_TMUX_SETUP)

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

    bench = W35PaneBenchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())
