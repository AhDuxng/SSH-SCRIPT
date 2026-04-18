from __future__ import annotations

"""
benchmark_submission_ready.py

Submission-ready SSH/Mosh/SSH3 benchmark with paper-safe metric definitions.

Primary reported metric:
    control_step_latency_ms
        Protocol-mediated latency of one observe->act step, excluding local
        Python decision time. Formally:

            control_step_latency_ms = obs_rtt_ms + act_rtt_ms

        where:
            obs_rtt_ms = t_obs_recv - t_obs_send
            act_rtt_ms = t_act_recv - t_act_send

        This is the fairest protocol-comparison metric because it excludes
        client CPU time that is unrelated to SSH/Mosh/SSH3.

Additional exported transparency metrics:
    processing_overhead_ms
        Local Python decision time between receiving the observation and
        sending the action command:

            processing_overhead_ms = t_act_send - t_obs_recv

    control_step_wall_ms
        Full wall-clock duration of the step:

            control_step_wall_ms = t_act_recv - t_obs_send

        Therefore:
            control_step_wall_ms = control_step_latency_ms + processing_overhead_ms

Other metrics:
    line_echo_ms
        Application-level terminal RTT from sendline(token) to ACK receipt from
        the remote Python helper.

    session_setup_ms
        Time-to-usable-shell from pexpect.spawn() until the remote shell is
        configured and ready for measurements.

    ping_rtt_ms
        ICMP baseline covariate measured once per trial before opening the
        protocol session.
"""

import csv
import json
import math
import platform
import random
import re
import shlex
import statistics
import string
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect. Install with: pip install pexpect"
    ) from exc

from constants import ANSI_NOISE
from exceptions import PreflightError, SessionOpenError
from models import FailureRecord, RemoteMeta, SampleRecord, SummaryRow

ANSI_STRIP_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
    r"|[\r\n\x00\x08]"
)
ANSI_ONLY_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
)


@dataclass
class ControlStepRecord:
    protocol: str
    trial_id: int
    sample_id: int
    is_warmup: bool
    token: str
    t_obs_send_ns: int
    t_obs_recv_ns: int
    t_act_send_ns: int
    t_act_recv_ns: int

    @property
    def obs_rtt_ms(self) -> float:
        return (self.t_obs_recv_ns - self.t_obs_send_ns) / 1e6

    @property
    def processing_overhead_ms(self) -> float:
        return (self.t_act_send_ns - self.t_obs_recv_ns) / 1e6

    @property
    def act_rtt_ms(self) -> float:
        return (self.t_act_recv_ns - self.t_act_send_ns) / 1e6

    @property
    def control_step_latency_ms(self) -> float:
        return self.obs_rtt_ms + self.act_rtt_ms

    @property
    def control_step_wall_ms(self) -> float:
        return (self.t_act_recv_ns - self.t_obs_send_ns) / 1e6


class Benchmark:
    METRIC_ALIASES = {
        "keystroke_latency": "control_step_latency",
        "control_step_latency": "control_step_latency",
        "line_echo": "line_echo",
        "session_setup": "session_setup",
    }

    def __init__(self, args: object) -> None:
        self.args = args
        self.requested_metrics = list(args.metrics)
        self.args.metrics = [self._canonical_metric_name(m) for m in args.metrics]
        self.target = f"{args.user}@{args.host}"
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records: List[SampleRecord] = []
        self.control_step_records: List[ControlStepRecord] = []
        self.failures: List[FailureRecord] = []
        self.protocol_skip_reasons: Dict[str, str] = {}
        self.ping_rtts: Dict[str, List[Optional[float]]] = {p: [] for p in args.protocols}
        self.remote_meta = RemoteMeta()
        self.results: Dict[str, Dict[str, List[float]]] = {
            protocol: {metric: [] for metric in args.metrics}
            for protocol in args.protocols
        }
        self._pattern_cache: Dict[str, re.Pattern] = {}

    @classmethod
    def _canonical_metric_name(cls, metric: str) -> str:
        return cls.METRIC_ALIASES.get(metric, metric)

    @staticmethod
    def _strip_ansi_keep_newlines(text: str) -> str:
        clean = ANSI_ONLY_RE.sub("", text)
        return clean.replace("\r", "").replace("\x00", "").replace("\x08", "")
    def _strip_prompt_prefixes(self, line: str) -> str:
        prompt = str(getattr(self.args, "prompt", "") or "").strip()
        candidate = line.strip()
        while prompt and candidate.startswith(prompt):
            candidate = candidate[len(prompt):].lstrip()
        return candidate


    def _literal_pattern(self, literal: str) -> re.Pattern:
        pat = self._pattern_cache.get(literal)
        if pat is None:
            pat = re.compile(
                ANSI_NOISE.join(re.escape(ch) for ch in literal),
                re.DOTALL,
            )
            self._pattern_cache[literal] = pat
        return pat

    def _expect_literal(
        self,
        child: pexpect.spawn,
        literal: str,
        timeout: Optional[float] = None,
    ) -> None:
        t = timeout if timeout is not None else self.args.timeout
        child.expect(self._literal_pattern(literal), timeout=t)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        return ANSI_STRIP_RE.sub("", text)

    def _buf(self, child: pexpect.spawn, limit: int = 300) -> str:
        raw = (getattr(child, "before", "") or "")[-limit:]
        clean = self._strip_ansi(raw)[-limit:]
        return f"raw={raw!r} clean={clean!r}"

    def _token(self, protocol: str, trial_id: int, sample_id: int) -> str:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        return f"W3T{protocol[:2].upper()}{trial_id:03d}{sample_id:04d}{rand}Z"

    def _ping_rtt_ms(self) -> Optional[float]:
        cmd = ["ping", "-c", "1", "-W", "3"]
        if getattr(self.args, "source_ip", None):
            cmd += ["-I", self.args.source_ip]
        cmd.append(self.args.host)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                if "time=" in line:
                    for part in line.split():
                        if part.startswith("time="):
                            return float(part.split("=")[1])
        except Exception:
            pass
        return None

    def _ssh_base_args(self) -> List[str]:
        args = ["ssh", "-tt"]
        if self.args.source_ip:
            args += ["-b", self.args.source_ip]
        if self.args.strict_host_key_checking:
            args += ["-o", "StrictHostKeyChecking=yes"]
        else:
            args += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
        if self.args.identity_file:
            args += ["-i", self.args.identity_file]
        if self.args.batch_mode:
            args += ["-o", "BatchMode=yes"]
        args += ["-o", "ControlMaster=no", "-o", "ControlPath=none"]
        return args

    def _session_command(self, protocol: str) -> str:
        if protocol == "ssh":
            return shlex.join(self._ssh_base_args() + [self.target])
        if protocol == "mosh":
            ssh_cmd = shlex.join(self._ssh_base_args())
            parts = ["mosh", f"--ssh={ssh_cmd}"]
            if self.args.mosh_predict != "adaptive":
                parts += ["--predict", self.args.mosh_predict]
            parts += [self.target]
            return shlex.join(parts)
        if protocol == "ssh3":
            parts = ["ssh3"]
            if self.args.identity_file:
                parts += ["-privkey", self.args.identity_file]
            if self.args.ssh3_insecure:
                parts += ["-insecure"]
            parts.append(f"{self.target}{self.args.ssh3_path}")
            return shlex.join(parts)
        raise ValueError(f"Unknown protocol: {protocol!r}")

    def _spawn(self, protocol: str) -> pexpect.spawn:
        child = pexpect.spawn(
            self._session_command(protocol),
            encoding="utf-8",
            codec_errors="ignore",
            timeout=self.args.timeout,
            maxread=65535,
        )
        try:
            child.setwinsize(self.args.pty_rows, self.args.pty_cols)
        except Exception:
            pass
        if self.args.log_pexpect:
            log_path = Path(self.args.output_dir) / f"pexpect_{protocol}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child.logfile_read = open(log_path, "a", encoding="utf-8")
        return child

    _SHELL_READY_PATTERNS = [
        r"Are you sure you want to continue connecting \(yes/no(?:/\[fingerprint\])?\)\?",
        r"Do you want to add this certificate to .*known_hosts \(yes/no\)\?",
        r"\[Pp\]assword:",
        r"Permission denied",
        r"Connection refused",
        r"No route to host",
        r"Connection timed out",
        r"Could not resolve hostname",
        r"Cannot assign requested address",
        r"Network is unreachable",
        r"closed by remote host",
        r"[$#] ?$",
        r"\x1b\[[0-9;?]*[A-Za-z]",
        pexpect.EOF,
        pexpect.TIMEOUT,
    ]

    _FATAL_MESSAGES = [
        "Permission denied",
        "Connection refused",
        "No route to host",
        "Connection timed out",
        "Could not resolve hostname",
        "Cannot assign requested address",
        "Network is unreachable",
        "Connection closed by remote host",
    ]

    def _await_shell(self, child: pexpect.spawn) -> None:
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
            idx = child.expect(self._SHELL_READY_PATTERNS, timeout=remaining)

            if idx == 0:
                child.sendline("yes")
                continue
            if idx == 1:
                if self.args.ssh3_trust_on_first_use or self.args.ssh3_insecure:
                    child.sendline("yes")
                    continue
                raise SessionOpenError(
                    "SSH3 cert prompt: rerun with --ssh3-insecure or --ssh3-trust-on-first-use"
                )
            if idx == 2:
                raise SessionOpenError("Password prompt: key auth not working")
            if 3 <= idx <= 10:
                raise SessionOpenError(self._FATAL_MESSAGES[idx - 3])
            if idx == 13:
                raise SessionOpenError(f"EOF while waiting for shell. {self._buf(child)}")
            if idx == 14:
                raise SessionOpenError(f"Timeout waiting for shell. {self._buf(child)}")

            child.sendline("printf '__W3PROBE__\\n'")
            probe = child.expect(
                [
                    self._literal_pattern("__W3PROBE__"),
                    r"\[Pp\]assword:",
                    "Permission denied",
                    pexpect.EOF,
                    pexpect.TIMEOUT,
                ],
                timeout=max(1.0, min(8.0, deadline - time.monotonic())),
            )
            if probe == 0:
                return
            if probe == 1:
                raise SessionOpenError("Password prompt during probe")
            if probe == 2:
                raise SessionOpenError("Permission denied during probe")
            if probe == 3:
                raise SessionOpenError(f"EOF during probe. {self._buf(child)}")
            raise SessionOpenError(f"Timeout during probe. {self._buf(child)}")

        raise SessionOpenError(f"Overall timeout. {self._buf(child)}")

    def _open_session(self, protocol: str) -> Tuple[pexpect.spawn, float]:
        t0 = time.perf_counter_ns()
        child = self._spawn(protocol)
        try:
            self._await_shell(child)
            setup_marker = "__W3SETUP__"
            child.sendline(
                "unset PROMPT_COMMAND 2>/dev/null || true; "
                "bind 'set enable-bracketed-paste off' 2>/dev/null || true; "
                f"export PS1={shlex.quote(self.args.prompt)}; "
                f"printf '{setup_marker}\\n'"
            )
            self._expect_literal(child, setup_marker)
            self._expect_literal(child, self.args.prompt)
            child.sendline(
                f"stty -echo -echoctl cols {self.args.pty_cols} rows {self.args.pty_rows} 2>/dev/null || true"
            )
            self._expect_literal(child, self.args.prompt)
            t1 = time.perf_counter_ns()
            return child, (t1 - t0) / 1e6
        except Exception:
            self._safe_close(child)
            raise

    def _close_session(self, child: pexpect.spawn) -> None:
        try:
            if child.isalive():
                child.sendline("exit")
                child.expect(pexpect.EOF, timeout=min(6, self.args.timeout))
        except Exception:
            child.close(force=True)
        finally:
            lf = getattr(child, "logfile_read", None)
            if lf:
                try:
                    lf.close()
                except Exception:
                    pass

    def _safe_close(self, child: pexpect.spawn) -> None:
        try:
            self._close_session(child)
        except Exception:
            pass

    def _record_ok(
        self,
        protocol: str,
        metric: str,
        trial_id: int,
        sample_id: int,
        is_warmup: bool,
        token: str,
        latency_ms: float,
    ) -> None:
        self.records.append(
            SampleRecord(protocol, metric, trial_id, sample_id, is_warmup, token, latency_ms)
        )
        if is_warmup:
            return
        if metric not in self.results[protocol]:
            return
        self.results[protocol][metric].append(latency_ms)

    def _record_fail(
        self,
        protocol: str,
        metric: str,
        trial_id: int,
        sample_id: int,
        is_warmup: bool,
        exc: Exception,
        child: Optional[pexpect.spawn] = None,
    ) -> None:
        if metric != "session_setup" and metric not in self.results.get(protocol, {}):
            return
        extra = f" | {self._buf(child)}" if child is not None else ""
        self.failures.append(
            FailureRecord(
                protocol=protocol,
                metric=metric,
                trial_id=trial_id,
                sample_id=sample_id,
                is_warmup=is_warmup,
                error_type=type(exc).__name__,
                error_message=f"{exc}{extra}",
            )
        )

    def _remote_cmd(self, child: pexpect.spawn, cmd: str) -> str:
        child.sendline(cmd)
        self._expect_literal(child, self.args.prompt, timeout=12)
        clean = self._strip_ansi(child.before or "")
        lines = [l.strip() for l in clean.splitlines() if l.strip() and l.strip() != cmd]
        return lines[-1] if lines else "unknown"

    def _collect_remote_meta(self, child: pexpect.spawn) -> None:
        self.remote_meta.kernel = self._remote_cmd(child, "uname -r")
        self.remote_meta.mosh_version = self._remote_cmd(child, "mosh --version 2>&1 | head -1")
        self.remote_meta.ssh_version = self._remote_cmd(child, "ssh -V 2>&1 | head -1")
        self.remote_meta.ssh3_version = self._remote_cmd(
            child,
            "command -v ssh3 >/dev/null 2>&1 && ssh3 -version 2>&1 | head -1 || printf 'not-installed\\n'",
        )
        self.remote_meta.python_version = self._remote_cmd(child, "python3 --version 2>&1")

    def _preflight_protocol(self, protocol: str) -> None:
        child, _ = self._open_session(protocol)
        try:
            child.sendline(
                "command -v python3 >/dev/null 2>&1 && printf '__W3HAS_PY3__\\n' || printf '__W3NO_PY3__\\n'"
            )
            idx = child.expect(
                [self._literal_pattern("__W3HAS_PY3__"), self._literal_pattern("__W3NO_PY3__")],
                timeout=self.args.timeout,
            )
            self._expect_literal(child, self.args.prompt)
            if idx == 1:
                raise PreflightError("python3 not found on remote host")
            if self.remote_meta.kernel == "unknown":
                self._collect_remote_meta(child)
        finally:
            self._close_session(child)

    def _start_helper(self, child: pexpect.spawn, protocol: str, trial_id: int) -> Tuple[str, str]:
        ready = f"W3RDY{protocol[:2].upper()}{trial_id:03d}Z"
        ack = f"W3ACK{protocol[:2].upper()}{trial_id:03d}A"
        bye = f"W3BYE{protocol[:2].upper()}{trial_id:03d}Z"
        helper = (
            "import os,sys\n"
            "rdy=os.environ['W3R']\n"
            "ack=os.environ['W3A']\n"
            "bye=os.environ['W3B']\n"
            "print(rdy,flush=True)\n"
            "for ln in sys.stdin:\n"
            "    ln=ln.rstrip('\\n')\n"
            "    if ln=='W3EXIT':\n"
            "        print(bye,flush=True)\n"
            "        break\n"
            "    print(ack+ln,flush=True)\n"
        )
        cmd = (
            f"W3R={shlex.quote(ready)} "
            f"W3A={shlex.quote(ack)} "
            f"W3B={shlex.quote(bye)} "
            f"python3 -u -c {shlex.quote(helper)}"
        )
        child.sendline(cmd)
        self._expect_literal(child, ready, timeout=self.args.timeout)
        return ack, bye

    def _stop_helper(self, child: pexpect.spawn, bye: str) -> None:
        child.sendline("W3EXIT")
        self._expect_literal(child, bye, timeout=self.args.timeout)
        child.sendline("printf 'W3BACK\\n'")
        self._expect_literal(child, "W3BACK", timeout=self.args.timeout)
        self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)

    def _wait_for_output_line(
        self,
        child: pexpect.spawn,
        predicate: Callable[[str], bool],
        timeout_s: float,
        what: str,
    ) -> str:
        deadline = time.monotonic() + timeout_s
        clean_buffer = ""
        max_chars = 32768

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail = clean_buffer[-300:]
                raise pexpect.TIMEOUT(
                    f"{what} not received within {timeout_s:.1f}s. clean_tail={tail!r}"
                )

            try:
                chunk = child.read_nonblocking(
                    size=4096,
                    timeout=min(0.5, max(0.05, remaining)),
                )
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise SessionOpenError(f"EOF while waiting for {what}. {self._buf(child)}") from exc

            if not chunk:
                continue

            clean_buffer += self._strip_ansi_keep_newlines(chunk)
            if len(clean_buffer) > max_chars:
                clean_buffer = clean_buffer[-max_chars:]

            lines = clean_buffer.split("\n")
            clean_buffer = lines.pop() if lines else ""
            for line in lines:
                candidate = self._strip_prompt_prefixes(line)
                if candidate and predicate(candidate):
                    return candidate

    def _wait_ack_via_stream(
        self,
        child: pexpect.spawn,
        ack_marker: str,
        timeout_s: float,
    ) -> None:
        self._wait_for_output_line(
            child,
            predicate=lambda line: line == ack_marker,
            timeout_s=timeout_s,
            what=f"ACK {ack_marker!r}",
        )

    def _wait_marker_via_stream(
        self,
        child: pexpect.spawn,
        marker_prefix: str,
        timeout_s: float,
    ) -> str:
        return self._wait_for_output_line(
            child,
            predicate=lambda line: line.startswith(marker_prefix),
            timeout_s=timeout_s,
            what=f"marker starting with {marker_prefix!r}",
        )

    def _measure_echo(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        ack_prefix: str,
    ) -> Tuple[str, float]:
        token = self._token(protocol, trial_id, sample_id)
        ack_marker = ack_prefix + token
        echo_timeout = float(getattr(self.args, "echo_timeout", self.args.timeout))

        t0 = time.perf_counter_ns()
        child.sendline(token)
        self._wait_ack_via_stream(child, ack_marker, echo_timeout)
        t1 = time.perf_counter_ns()

        return token, (t1 - t0) / 1e6

    def _measure_control_step_latency(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        state: int,
    ) -> Tuple[str, float, int, ControlStepRecord]:
        token = self._token(protocol, trial_id, sample_id)
        timeout_s = float(getattr(self.args, "echo_timeout", self.args.timeout))

        obs_marker = f"__W3OBS__ {token} "
        cmd1 = f"printf '\n__W3OBS__ {token} %d\n' $(({state}*3+7))"
        t_obs_send = time.perf_counter_ns()
        child.sendline(cmd1)
        clean_obs = self._wait_marker_via_stream(child, obs_marker, timeout_s)
        t_obs_recv = time.perf_counter_ns()

        m = re.search(rf"__W3OBS__\s+{re.escape(token)}\s+(-?\d+)", clean_obs)
        if not m:
            raise RuntimeError(f"Cannot parse observation for token={token}")
        obs = int(m.group(1))

        if obs % 2 == 0:
            action = "INC"
            next_state = obs + 1
        else:
            action = "DEC"
            next_state = obs - 1

        act_marker = f"__W3ACT__ {token} {action} {next_state}"
        cmd2 = f"printf '\n__W3ACT__ {token} {action} {next_state}\n'"
        t_act_send = time.perf_counter_ns()
        child.sendline(cmd2)
        self._wait_marker_via_stream(child, act_marker, timeout_s)
        t_act_recv = time.perf_counter_ns()

        csr = ControlStepRecord(
            protocol=protocol,
            trial_id=trial_id,
            sample_id=sample_id,
            is_warmup=False,
            token=token,
            t_obs_send_ns=t_obs_send,
            t_obs_recv_ns=t_obs_recv,
            t_act_send_ns=t_act_send,
            t_act_recv_ns=t_act_recv,
        )
        return token, csr.control_step_latency_ms, next_state, csr

    def _measure_keystroke_latency(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        state: int,
    ) -> Tuple[str, float, int, ControlStepRecord]:
        return self._measure_control_step_latency(
            child, protocol, trial_id, sample_id, state
        )

    def _reopen_trial_session(
        self,
        protocol: str,
        child: Optional[pexpect.spawn],
        trial_id: int,
        need_echo_helper: bool,
    ) -> Tuple[pexpect.spawn, Optional[str], Optional[str]]:
        if child is not None:
            self._safe_close(child)
        reopened_child, _ = self._open_session(protocol)
        ack_prefix: Optional[str] = None
        bye_marker: Optional[str] = None
        if need_echo_helper:
            ack_prefix, bye_marker = self._start_helper(reopened_child, protocol, trial_id)
        return reopened_child, ack_prefix, bye_marker

    def _run_protocol(self, protocol: str) -> None:
        for trial_id in range(1, self.args.trials + 1):
            rtt = self._ping_rtt_ms()
            self.ping_rtts[protocol].append(rtt)
            rtt_s = f"{rtt:.2f} ms" if rtt is not None else "N/A"
            print(f"[{protocol:>4}] trial {trial_id:>2}/{self.args.trials}  ping(ICMP)={rtt_s}")

            child: Optional[pexpect.spawn] = None
            ack_prefix: Optional[str] = None
            bye_marker: Optional[str] = None
            setup_ok = False

            try:
                child, setup_ms = self._open_session(protocol)
                setup_ok = True
                self._record_ok(protocol, "session_setup", trial_id, 1, False, "__W3_SETUP__", setup_ms)
                print(f"[{protocol:>4}/setup      ] trial {trial_id:>2}:  OK  {setup_ms:.1f} ms")

                if "control_step_latency" in self.args.metrics:
                    agent_state = trial_id
                    for i in range(1, self.args.warmup_samples + 1):
                        sid = -i
                        try:
                            tok, lat, agent_state, csr = self._measure_control_step_latency(
                                child, protocol, trial_id, sid, agent_state,
                            )
                            csr.is_warmup = True
                            self.control_step_records.append(csr)
                            self._record_ok(protocol, "control_step_latency", trial_id, sid, True, tok, lat)
                            print(
                                f"[{protocol:>4}/ctl  warm {i:>3}/{self.args.warmup_samples}]"
                                f"  net={lat:.2f} ms"
                                f"  (obs={csr.obs_rtt_ms:.2f} proc={csr.processing_overhead_ms:.2f} act={csr.act_rtt_ms:.2f} wall={csr.control_step_wall_ms:.2f})"
                            )
                        except Exception as exc:
                            self._record_fail(protocol, "control_step_latency", trial_id, sid, True, exc, child)
                            print(
                                f"[{protocol:>4}/ctl  warm {i:>3}     ]  FAIL  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, _, _ = self._reopen_trial_session(
                                protocol, child, trial_id, need_echo_helper=False
                            )
                            continue

                    for sid in range(1, self.args.samples_per_trial + 1):
                        try:
                            tok, lat, agent_state, csr = self._measure_control_step_latency(
                                child, protocol, trial_id, sid, agent_state,
                            )
                            self.control_step_records.append(csr)
                            self._record_ok(protocol, "control_step_latency", trial_id, sid, False, tok, lat)
                            print(
                                f"[{protocol:>4}/ctl  meas {sid:>3}/{self.args.samples_per_trial}]"
                                f"  net={lat:.2f} ms"
                                f"  (obs={csr.obs_rtt_ms:.2f} proc={csr.processing_overhead_ms:.2f} act={csr.act_rtt_ms:.2f} wall={csr.control_step_wall_ms:.2f})"
                            )
                        except Exception as exc:
                            self._record_fail(protocol, "control_step_latency", trial_id, sid, False, exc, child)
                            print(
                                f"[{protocol:>4}/ctl  meas {sid:>3}     ]  FAIL  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, _, _ = self._reopen_trial_session(
                                protocol, child, trial_id, need_echo_helper=False
                            )
                            continue

                if "line_echo" in self.args.metrics:
                    ack_prefix, bye_marker = self._start_helper(child, protocol, trial_id)
                    for i in range(1, self.args.warmup_samples + 1):
                        sid = -i
                        try:
                            tok, lat = self._measure_echo(child, protocol, trial_id, sid, ack_prefix)
                            self._record_ok(protocol, "line_echo", trial_id, sid, True, tok, lat)
                            print(f"[{protocol:>4}/echo warm {i:>3}/{self.args.warmup_samples}]  {lat:.2f} ms")
                        except Exception as exc:
                            self._record_fail(protocol, "line_echo", trial_id, sid, True, exc, child)
                            print(
                                f"[{protocol:>4}/echo warm {i:>3}     ]  FAIL  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, ack_prefix, bye_marker = self._reopen_trial_session(
                                protocol, child, trial_id, need_echo_helper=True
                            )
                            continue

                    for sid in range(1, self.args.samples_per_trial + 1):
                        try:
                            tok, lat = self._measure_echo(child, protocol, trial_id, sid, ack_prefix)
                            self._record_ok(protocol, "line_echo", trial_id, sid, False, tok, lat)
                            print(f"[{protocol:>4}/echo meas {sid:>3}/{self.args.samples_per_trial}]  {lat:.2f} ms")
                        except Exception as exc:
                            self._record_fail(protocol, "line_echo", trial_id, sid, False, exc, child)
                            print(
                                f"[{protocol:>4}/echo meas {sid:>3}     ]  FAIL  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, ack_prefix, bye_marker = self._reopen_trial_session(
                                protocol, child, trial_id, need_echo_helper=True
                            )
                            continue

            except Exception as exc:
                if not setup_ok:
                    self._record_fail(protocol, "session_setup", trial_id, 1, False, exc, child)
                    print(
                        f"[{protocol:>4}/setup      ] trial {trial_id:>2}:  FAIL  {type(exc).__name__}: {exc}"
                    )

            finally:
                if child is not None:
                    if bye_marker is not None:
                        try:
                            self._stop_helper(child, bye_marker)
                        except Exception:
                            pass
                    self._safe_close(child)

    @staticmethod
    def _pct(data: List[float], p: float) -> Optional[float]:
        if not data:
            return None
        if len(data) == 1:
            return data[0]
        s = sorted(data)
        k = (len(s) - 1) * p / 100.0
        lo = math.floor(k)
        hi = math.ceil(k)
        return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)

    def _metric_budget(self, protocol: str, metric: str) -> int:
        if protocol in self.protocol_skip_reasons:
            return 0
        if metric in {"line_echo", "control_step_latency"}:
            return self.args.trials * self.args.samples_per_trial
        if metric == "session_setup":
            return self.args.trials
        return 0

    def _summary_row(self, protocol: str, metric: str) -> SummaryRow:
        data = self.results[protocol][metric]
        recorded_failures = sum(
            1
            for f in self.failures
            if f.protocol == protocol and f.metric == metric and not f.is_warmup
        )
        n = len(data)
        budget = self._metric_budget(protocol, metric)
        missing = max(0, budget - (n + recorded_failures))
        failures = recorded_failures + missing
        total = budget if budget > 0 else (n + failures)
        rate = 100.0 * n / total if total else 0.0

        if n == 0:
            return SummaryRow(protocol, metric, 0, failures, rate, None, None, None, None, None, None, None, None)

        mean = statistics.mean(data)
        median = statistics.median(data)
        stdev = statistics.stdev(data) if n > 1 else 0.0
        ci95 = 1.96 * stdev / math.sqrt(n) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol,
            metric=metric,
            n=n,
            failures=failures,
            success_rate_pct=rate,
            min_ms=min(data),
            mean_ms=mean,
            median_ms=median,
            stdev_ms=stdev,
            p95_ms=self._pct(data, 95),
            p99_ms=self._pct(data, 99),
            max_ms=max(data),
            ci95_half_width_ms=ci95,
        )

    def summaries(self) -> List[SummaryRow]:
        return [self._summary_row(p, m) for p in self.args.protocols for m in self.args.metrics]

    def _processing_overhead_stats(self) -> Dict[str, Dict[str, float]]:
        by_proto: Dict[str, List[float]] = {}
        for csr in self.control_step_records:
            if csr.is_warmup:
                continue
            by_proto.setdefault(csr.protocol, []).append(csr.processing_overhead_ms)

        out: Dict[str, Dict[str, float]] = {}
        for proto, vals in by_proto.items():
            if not vals:
                continue
            out[proto] = {
                "n": len(vals),
                "mean_ms": statistics.mean(vals),
                "median_ms": statistics.median(vals),
                "max_ms": max(vals),
                "p99_ms": self._pct(vals, 99) or 0.0,
            }
        return out

    def print_report(self) -> None:
        w = 150
        print("\n" + "=" * w)
        print(
            f"{'Protocol':<8} | {'Metric':<17} | {'N':>5} | {'Fail':>5} | "
            f"{'OK%':>6} | {'Min':>8} | {'Mean':>8} | {'Median':>8} | "
            f"{'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95+-':>9}"
        )
        print("-" * w)
        fmt_ms = lambda v: f"{v:.2f}" if v is not None else "N/A"
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.metric:<17} | {row.n:>5} | "
                f"{row.failures:>5} | {row.success_rate_pct:>6.1f}% | "
                f"{fmt_ms(row.min_ms):>8} | {fmt_ms(row.mean_ms):>8} | {fmt_ms(row.median_ms):>8} | "
                f"{fmt_ms(row.stdev_ms):>8} | {fmt_ms(row.p95_ms):>8} | {fmt_ms(row.p99_ms):>8} | "
                f"{fmt_ms(row.max_ms):>8} | {fmt_ms(row.ci95_half_width_ms):>8}"
            )
        print("=" * w)
        proc = self._processing_overhead_stats()
        if proc:
            print("\nProcessing overhead (excluded from control_step_latency_ms):")
            for proto, s in proc.items():
                print(
                    f"  {proto}: mean={s['mean_ms']:.3f} ms  median={s['median_ms']:.3f} ms"
                    f"  p99={s['p99_ms']:.3f} ms  max={s['max_ms']:.3f} ms  (n={int(s['n'])})"
                )
        if self.protocol_skip_reasons:
            print("\nSkipped protocols:")
            for p, reason in self.protocol_skip_reasons.items():
                print(f"  {p}: {reason}")

    def export(self) -> None:
        out = Path(self.args.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        payload = {
            "meta": {
                "started_at_utc": self.started_at,
                "target": self.target,
                "client_source_ip": self.args.source_ip,
                "protocols": self.args.protocols,
                "requested_metrics": self.requested_metrics,
                "metrics": self.args.metrics,
                "metric_aliases": self.METRIC_ALIASES,
                "trials": self.args.trials,
                "samples_per_trial": self.args.samples_per_trial,
                "warmup_samples": self.args.warmup_samples,
                "timeout_sec": self.args.timeout,
                "echo_timeout_sec": getattr(self.args, "echo_timeout", self.args.timeout),
                "pty_cols": self.args.pty_cols,
                "pty_rows": self.args.pty_rows,
                "random_seed": self.args.seed,
                "mosh_predict": self.args.mosh_predict,
                "topology": {
                    "client": self.args.source_ip or "default-route",
                    "server": self.args.host,
                },
                "client_system": {
                    "python": sys.version.split()[0],
                    "platform": platform.platform(),
                    "hostname": platform.node(),
                },
                "remote_system": asdict(self.remote_meta),
                "metric_notes": {
                    "session_setup_ms": (
                        "Time-to-usable-shell from pexpect.spawn() until the remote shell is fully configured and ready. "
                        "This includes setup commands after login; it is not a pure transport handshake metric."
                    ),
                    "control_step_latency_ms": (
                        "Primary protocol-comparison metric. Defined as obs_rtt_ms + act_rtt_ms, excluding local Python decision time. "
                        "Equivalent to (t_obs_recv - t_obs_send) + (t_act_recv - t_act_send)."
                    ),
                    "control_step_wall_ms": (
                        "Full wall-clock duration of a control step, including local decision time. "
                        "Equivalent to t_act_recv - t_obs_send. Exported in control_step_details.csv for transparency, not used as the primary protocol metric."
                    ),
                    "processing_overhead_ms": (
                        "Local client-side decision time between receiving the observation and sending the action command. "
                        "Equivalent to t_act_send - t_obs_recv. Excluded from control_step_latency_ms because it reflects local CPU/runtime overhead rather than the protocol under test."
                    ),
                    "keystroke_latency_ms": (
                        "Backward-compatible alias for control_step_latency_ms. Prefer control_step_latency_ms in papers, plots, and tables."
                    ),
                    "line_echo_ms": (
                        "Application-level terminal RTT from sendline(token) to ACK receipt from the remote helper process. "
                        "This includes remote helper I/O overhead and should not be described as pixel-level or per-keystroke latency."
                    ),
                    "success_rate_pct_note": (
                        "Fixed-budget denominator. Per-sample metrics use trials x samples_per_trial; session_setup uses trials. Missing observations count as failures."
                    ),
                    "stdev_ms_note": "Sample standard deviation using statistics.stdev with ddof=1 semantics.",
                    "ci95_note": "95% CI half-width computed as 1.96 x stdev / sqrt(n).",
                    "percentile_method": "Linear interpolation percentile based on sorted samples.",
                    "ping_rtt_ms": (
                        "ICMP baseline covariate measured once per trial before opening the session. It is not directly comparable to application-layer RTTs."
                    ),
                },
                "processing_overhead_summary": self._processing_overhead_stats(),
                "skipped_protocols": self.protocol_skip_reasons,
            },
            "summary": [asdict(r) for r in self.summaries()],
        }
        jpath = out / "summary.json"
        jpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        cpath = out / "summary.csv"
        with cpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol",
                "metric",
                "n",
                "failures",
                "success_rate_pct",
                "min_ms",
                "mean_ms",
                "median_ms",
                "stdev_ms",
                "p95_ms",
                "p99_ms",
                "max_ms",
                "ci95_half_width_ms",
                "ping_rtt_mean_ms",
            ])
            for row in self.summaries():
                rtts = [x for x in self.ping_rtts.get(row.protocol, []) if x is not None]
                ping_mean = statistics.mean(rtts) if rtts else None
                w.writerow([
                    row.protocol,
                    row.metric,
                    row.n,
                    row.failures,
                    f"{row.success_rate_pct:.3f}",
                    "" if row.min_ms is None else f"{row.min_ms:.6f}",
                    "" if row.mean_ms is None else f"{row.mean_ms:.6f}",
                    "" if row.median_ms is None else f"{row.median_ms:.6f}",
                    "" if row.stdev_ms is None else f"{row.stdev_ms:.6f}",
                    "" if row.p95_ms is None else f"{row.p95_ms:.6f}",
                    "" if row.p99_ms is None else f"{row.p99_ms:.6f}",
                    "" if row.max_ms is None else f"{row.max_ms:.6f}",
                    "" if row.ci95_half_width_ms is None else f"{row.ci95_half_width_ms:.6f}",
                    "" if ping_mean is None else f"{ping_mean:.6f}",
                ])

        spath = out / "samples.csv"
        with spath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "metric", "trial_id", "sample_id", "is_warmup", "token", "latency_ms"])
            for r in self.records:
                w.writerow([r.protocol, r.metric, r.trial_id, r.sample_id, r.is_warmup, r.token, f"{r.latency_ms:.6f}"])

        dpath = out / "control_step_details.csv"
        with dpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol",
                "trial_id",
                "sample_id",
                "is_warmup",
                "token",
                "obs_rtt_ms",
                "processing_overhead_ms",
                "act_rtt_ms",
                "control_step_latency_ms",
                "control_step_wall_ms",
                "t_obs_send_ns",
                "t_obs_recv_ns",
                "t_act_send_ns",
                "t_act_recv_ns",
            ])
            for csr in self.control_step_records:
                w.writerow([
                    csr.protocol,
                    csr.trial_id,
                    csr.sample_id,
                    csr.is_warmup,
                    csr.token,
                    f"{csr.obs_rtt_ms:.6f}",
                    f"{csr.processing_overhead_ms:.6f}",
                    f"{csr.act_rtt_ms:.6f}",
                    f"{csr.control_step_latency_ms:.6f}",
                    f"{csr.control_step_wall_ms:.6f}",
                    csr.t_obs_send_ns,
                    csr.t_obs_recv_ns,
                    csr.t_act_send_ns,
                    csr.t_act_recv_ns,
                ])

        fpath = out / "failures.csv"
        with fpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "metric", "trial_id", "sample_id", "is_warmup", "error_type", "error_message"])
            for r in self.failures:
                w.writerow([r.protocol, r.metric, r.trial_id, r.sample_id, r.is_warmup, r.error_type, r.error_message])

        ppath = out / "ping_rtts.csv"
        with ppath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "trial_id", "ping_rtt_ms"])
            for proto, rtts in self.ping_rtts.items():
                for i, rtt in enumerate(rtts, 1):
                    w.writerow([proto, i, f"{rtt:.3f}" if rtt is not None else ""])

        print(f"\nOutputs written to {out}/")
        print("  summary.json              - metadata + per-protocol statistics")
        print("  summary.csv               - consolidated protocol/metric comparison table")
        print("  samples.csv               - every primary raw measurement")
        print("  control_step_details.csv  - obs/proc/act/wall breakdown per control step")
        print("  failures.csv              - every failure with context")
        print("  ping_rtts.csv             - ICMP network-layer covariate per trial")

    def run(self) -> None:
        random.seed(self.args.seed)
        protocols = list(self.args.protocols)
        if self.args.shuffle_protocols:
            random.shuffle(protocols)

        if self.args.preflight:
            approved = []
            for p in protocols:
                print(f"[preflight/{p}] checking ...")
                try:
                    self._preflight_protocol(p)
                    print(f"[preflight/{p}] OK")
                    approved.append(p)
                except Exception as exc:
                    self.protocol_skip_reasons[p] = str(exc)
                    print(f"[preflight/{p}] SKIP - {type(exc).__name__}: {exc}")
            protocols = approved

        for p in protocols:
            print(f"\n{'-' * 60}\nProtocol: {p}\n{'-' * 60}")
            self._run_protocol(p)
