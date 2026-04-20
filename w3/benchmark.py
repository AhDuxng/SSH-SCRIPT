from __future__ import annotations

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
from typing import Callable, Dict, List, Optional, Pattern, Tuple

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

_WARMUP_FLOOR = 10




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
        """Primary metric: network-only latency, excludes local decision time."""
        return self.obs_rtt_ms + self.act_rtt_ms

    @property
    def control_step_wall_ms(self) -> float:
        """Full wall-clock: includes local decision time."""
        return (self.t_act_recv_ns - self.t_obs_send_ns) / 1e6

class Benchmark:
    METRIC_ALIASES: Dict[str, str] = {
        "keystroke_latency":   "keystroke_latency",
        "control_step_latency": "control_step_latency",
        "line_echo":            "line_echo",
        "session_setup":        "session_setup",
    }

    def __init__(self, args: object) -> None:
        self.args = args
        self.requested_metrics: List[str] = list(args.metrics)
        self.args.metrics = [self._canonical_metric_name(m) for m in args.metrics]

        if self.args.warmup_samples < _WARMUP_FLOOR:
            print(
                f"[warn] warmup_samples={self.args.warmup_samples} is below the "
                f"recommended minimum of {_WARMUP_FLOOR}. Raising to {_WARMUP_FLOOR}."
            )
            self.args.warmup_samples = _WARMUP_FLOOR

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
        self._stream_state: Dict[int, Dict[str, object]] = {}
        self._key_ack_regex_cache: Dict[str, Pattern[str]] = {}
        self._key_sync_counter = 0

    @classmethod
    def _canonical_metric_name(cls, metric: str) -> str:
        return cls.METRIC_ALIASES.get(metric, metric)

    @staticmethod
    def _strip_ansi_keep_newlines(text: str) -> str:
        clean = ANSI_ONLY_RE.sub("", text)
        return clean.replace("\r", "").replace("\x00", "").replace("\x08", "")

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
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
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

    def _session_command(self, protocol: str, predict_mode: str = "adaptive") -> str:
        """
        Build the session command.

        predict_mode controls Mosh's local-echo speculation:
          "adaptive"  — Mosh default; speculative local rendering enabled.
          "never"     — Disables all speculation; PTY output matches the
                        server state exactly.  Required for timing measurements
                        because speculative renders appear in the PTY stream
                        without a corresponding server confirmation, causing
                        the stream reader to fire prematurely and then miss
                        the real server-confirmed line (BUG-6).
        """
        if protocol == "ssh":
            return shlex.join(self._ssh_base_args() + [self.target])
        if protocol == "mosh":
            ssh_cmd = shlex.join(self._ssh_base_args())
            parts = ["mosh", f"--ssh={ssh_cmd}"]
            effective_predict = predict_mode
            if getattr(self.args, "mosh_predict", "adaptive") != "adaptive":
                effective_predict = self.args.mosh_predict
            parts += ["--predict", effective_predict]
            parts.append(self.target)
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

    def _spawn(self, protocol: str, predict_mode: str = "adaptive") -> pexpect.spawn:
        child = pexpect.spawn(
            self._session_command(protocol, predict_mode=predict_mode),
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
        self._reset_stream_reader(child)
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

    def _open_session(
        self,
        protocol: str,
        predict_mode: str = "adaptive",
    ) -> Tuple[pexpect.spawn, float]:
        t0 = time.perf_counter_ns()
        child = self._spawn(protocol, predict_mode=predict_mode)
        try:
            self._await_shell(child)

            remote_setup = getattr(self.args, "remote_setup", "~/w3_tmux_setup.sh")
            child.sendline(f"bash {remote_setup}")
            try:
                self._wait_exact_line(child, "__W3_PANE0_READY__", timeout_s=self.args.timeout)
            except Exception as exc:
                raise SessionOpenError(
                    f"Timeout waiting for __W3_PANE0_READY__. {self._buf(child)}"
                ) from exc

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
            self._drain_output_until_quiet(child, quiet_s=0.03, budget_s=0.20)
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
            self._stream_state.pop(id(child), None)
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
        if metric not in self.results.get(protocol, {}):
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
        self._reset_stream_reader(child)
        ready = f"W3RDY{protocol[:2].upper()}{trial_id:03d}Z"
        ack   = f"W3ACK{protocol[:2].upper()}{trial_id:03d}A"
        bye   = f"W3BYE{protocol[:2].upper()}{trial_id:03d}Z"
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
        child.sendline("printf 'W3BACK\n'")
        self._expect_literal(child, "W3BACK", timeout=self.args.timeout)
        self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)
        self._reset_stream_reader(child)

    def _start_keystroke_helper(
        self, child: pexpect.spawn, protocol: str, trial_id: int
    ) -> Tuple[str, str]:
        self._reset_stream_reader(child)
        ready = f"W3KRY{protocol[:2].upper()}{trial_id:03d}Z"
        ack   = f"W3KEY{protocol[:2].upper()}{trial_id:03d}A"
        bye   = f"W3KBY{protocol[:2].upper()}{trial_id:03d}Z"
        frame = f"@@@__W3KFRAME__{ack}"
        self._key_sync_counter += 1
        sync = f"__W3KSYNC__{protocol[:2].upper()}{trial_id:03d}{self._key_sync_counter:04d}__"
        armed = f"W3KARM{protocol[:2].upper()}{trial_id:03d}Z"
        helper = (
            "import os,sys,tty,termios\n"
            "rdy=os.environ['W3R']\n"
            "ack=os.environ['W3A']\n"
            "bye=os.environ['W3B']\n"
            "frm=os.environ['W3F']\n"
            "syn=os.environ['W3S'].encode()\n"
            "arm=os.environ['W3Q']\n"
            "fi=sys.stdin.fileno()\n"
            "fo=sys.stdout.fileno()\n"
            "old=termios.tcgetattr(fi)\n"
            "try:\n"
            "    tty.setraw(fi)\n"
            "    os.write(fo,(rdy+'\r\n').encode())\n"
            "    win=bytearray()\n"
            "    while True:\n"
            "        b=os.read(fi,1)\n"
            "        if not b or b==b'\x03':\n"
            "            os.write(fo,(bye+'\r\n').encode())\n"
            "            break\n"
            "        win += b\n"
            "        if len(win) > len(syn):\n"
            "            del win[:-len(syn)]\n"
            "        if bytes(win) == syn:\n"
            "            os.write(fo,(arm+'\r\n').encode())\n"
            "            break\n"
            "    c=1\n"
            "    while True:\n"
            "        b=os.read(fi,1)\n"
            "        if not b or b==b'\x03':\n"
            "            os.write(fo,(bye+'\r\n').encode())\n"
            "            break\n"
            "        rec = frm+f'{c:04d}:'+format(b[0],'02x')+';@@@\\n'\n"
            "        os.write(fo, rec.encode())\n"
            "        os.write(fo, rec.encode())\n"
            "        c += 1\n"
            "finally:\n"
            "    termios.tcsetattr(fi,termios.TCSADRAIN,old)\n"
        )
        cmd = (
            f"W3R={shlex.quote(ready)} "
            f"W3A={shlex.quote(ack)} "
            f"W3B={shlex.quote(bye)} "
            f"W3F={shlex.quote(frame)} "
            f"W3S={shlex.quote(sync)} "
            f"W3Q={shlex.quote(armed)} "
            f"python3 -u -c {shlex.quote(helper)}"
        )
        child.sendline(cmd)
        self._expect_literal(child, ready, timeout=self.args.timeout)
        self._drain_output_until_quiet(child, quiet_s=0.03, budget_s=0.20)
        child.send(sync)
        self._expect_literal(child, armed, timeout=self.args.timeout)
        self._arm_keystroke_parser(child, ack)
        self._drain_output_until_quiet(child, quiet_s=0.03, budget_s=0.20)
        return ack, bye


    def _stop_keystroke_helper(self, child: pexpect.spawn, bye: str) -> None:
        """Send Ctrl-C to terminate the raw-mode keystroke helper."""
        child.send("\x03")
        self._expect_literal(child, bye, timeout=self.args.timeout)
        self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)
        self._reset_stream_reader(child)

    _STREAM_MAX_CHARS = 32768
    _KEY_ACK_BUF_MAX = 8192
    _OUTPUT_DRAIN_CHUNK = 4096

    def _reset_stream_reader(self, child: pexpect.spawn) -> None:
        self._stream_state[id(child)] = {
            "partial": "",
            "pending": [],
            "key_ack_buf": "",
            "key_ack_pending": [],
            "key_ack_re": None,
        }

    def _key_ack_regex(self, ack_prefix: str) -> Pattern[str]:
        pat = self._key_ack_regex_cache.get(ack_prefix)
        if pat is None:
            pat = re.compile(
                rf"@@@__W3KFRAME__{re.escape(ack_prefix)}(\d{{4}}):([0-9a-f]{{2}});@@@"
            )
            self._key_ack_regex_cache[ack_prefix] = pat
        return pat

    def _arm_keystroke_parser(self, child: pexpect.spawn, ack_prefix: str) -> None:
        state = self._stream_reader_state(child)
        state["key_ack_buf"] = ""
        state["key_ack_pending"] = []
        state["key_ack_re"] = self._key_ack_regex(ack_prefix)

    def _extract_keystroke_frames(self, child: pexpect.spawn, clean_chunk: str) -> None:
        state = self._stream_reader_state(child)
        key_re = state.get("key_ack_re")
        if key_re is None:
            return

        buf = str(state.get("key_ack_buf", "")) + clean_chunk
        pending = state["key_ack_pending"]
        last_end = 0
        for m in key_re.finditer(buf):
            pending.append((int(m.group(1)), m.group(2), m.group(0)))
            last_end = m.end()

        if last_end:
            buf = buf[last_end:]
        if len(buf) > self._KEY_ACK_BUF_MAX:
            buf = buf[-self._KEY_ACK_BUF_MAX:]
        state["key_ack_buf"] = buf

    def _pop_next_keystroke_ack(
        self,
        child: pexpect.spawn,
        expected_seq: int,
    ) -> Optional[Tuple[int, str, str]]:
        state = self._stream_reader_state(child)
        pending = state["key_ack_pending"]

        while pending:
            seq, hx, raw = pending.pop(0)
            if seq < expected_seq:
                continue
            return int(seq), str(hx), str(raw)
        return None

    def _stream_reader_state(self, child: pexpect.spawn) -> Dict[str, object]:
        state = self._stream_state.get(id(child))
        if state is None:
            self._reset_stream_reader(child)
            state = self._stream_state[id(child)]
        return state

    def _drain_output_until_quiet(
        self,
        child: pexpect.spawn,
        quiet_s: float = 0.05,
        budget_s: float = 0.25,
    ) -> None:
        """Best-effort drain of noisy background output without failing if the stream stays busy."""
        deadline = time.monotonic() + max(0.0, budget_s)
        quiet_deadline = time.monotonic() + max(0.0, quiet_s)

        while time.monotonic() < deadline:
            timeout = max(0.01, min(quiet_deadline - time.monotonic(), deadline - time.monotonic()))
            try:
                chunk = child.read_nonblocking(
                    size=self._OUTPUT_DRAIN_CHUNK,
                    timeout=timeout,
                )
            except pexpect.TIMEOUT:
                if time.monotonic() >= quiet_deadline:
                    break
                continue
            except pexpect.EOF:
                break

            if not chunk:
                continue

            self._enqueue_stream_chunk(child, chunk)
            quiet_deadline = time.monotonic() + max(0.0, quiet_s)

    def _stream_tail(self, child: pexpect.spawn, limit: int = 300) -> str:
        state = self._stream_reader_state(child)
        pending = state["pending"]  
        partial = state["partial"]  

        tail_parts: List[str] = []
        if pending:
            tail_parts.extend(pending[-3:])
        if partial:
            tail_parts.append(str(partial))
        tail = "\n".join(str(x) for x in tail_parts if str(x))
        return tail[-limit:]

    def _enqueue_stream_chunk(self, child: pexpect.spawn, chunk: str) -> None:
        state = self._stream_reader_state(child)
        pending = state["pending"]  
        clean_chunk = self._strip_ansi_keep_newlines(chunk)
        partial = str(state["partial"]) + clean_chunk

        self._extract_keystroke_frames(child, clean_chunk)

        if len(partial) > self._STREAM_MAX_CHARS:
            partial = partial[-self._STREAM_MAX_CHARS:]

        pieces = partial.split("\n")
        state["partial"] = pieces.pop() if pieces else ""

        for piece in pieces:
            candidate = piece.strip()
            if candidate:
                pending.append(candidate)

        if len(pending) > 4096:
            del pending[:-4096]

    def _pop_matching_stream_line(
        self,
        child: pexpect.spawn,
        predicate: Callable[[str], bool],
    ) -> Optional[str]:
        state = self._stream_reader_state(child)
        pending = state["pending"]  

        while pending:
            candidate = pending.pop(0)
            if predicate(candidate):
                return candidate
        return None

    def _wait_for_output_line(
        self,
        child: pexpect.spawn,
        predicate: Callable[[str], bool],
        timeout_s: float,
        what: str,
    ) -> str:
        deadline = time.monotonic() + timeout_s

        while True:
            matched = self._pop_matching_stream_line(child, predicate)
            if matched is not None:
                return matched

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail_candidate = self._stream_tail(child)
                if tail_candidate and predicate(tail_candidate):
                    return tail_candidate
                raise pexpect.TIMEOUT(
                    f"{what} not received within {timeout_s:.1f}s. "
                    f"clean_tail={tail_candidate!r}"
                )

            try:
                chunk = child.read_nonblocking(
                    size=4096,
                    timeout=min(0.5, max(0.05, remaining)),
                )
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise SessionOpenError(
                    f"EOF while waiting for {what}. {self._buf(child)}"
                ) from exc

            if not chunk:
                continue

            self._enqueue_stream_chunk(child, chunk)

    def _wait_exact_line(
        self, child: pexpect.spawn, expected: str, timeout_s: float
    ) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            state = self._stream_reader_state(child)
            matched = self._pop_matching_stream_line(child, lambda line: expected in line)
            if matched is not None:
                return
            if expected in str(state.get("partial", "")):
                return
            
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail = self._stream_tail(child)
                raise pexpect.TIMEOUT(
                    f"exact line {expected!r} not received within {timeout_s:.1f}s. "
                    f"clean_tail={tail!r}"
                )

            try:
                chunk = child.read_nonblocking(
                    size=4096,
                    timeout=min(0.5, max(0.05, remaining)),
                )
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise SessionOpenError(
                    f"EOF while waiting for {expected!r}. {self._buf(child)}"
                ) from exc

            if not chunk:
                continue
            self._enqueue_stream_chunk(child, chunk)

    def _wait_line_prefix(
        self, child: pexpect.spawn, prefix: str, timeout_s: float
    ) -> str:
        """
        Wait for a line that contains `prefix`; return that line.
        """
        return self._wait_for_output_line(
            child,
            predicate=lambda line: prefix in line,
            timeout_s=timeout_s,
            what=f"line starting with {prefix!r}",
        )

    def _wait_keystroke_ack(
        self,
        child: pexpect.spawn,
        expected_seq: int,
        timeout_s: float,
    ) -> Tuple[int, str, str]:
        deadline = time.monotonic() + timeout_s

        while True:
            matched = self._pop_next_keystroke_ack(child, expected_seq)
            if matched is not None:
                return matched

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail_candidate = self._stream_tail(child)
                raise pexpect.TIMEOUT(
                    f"keystroke ACK seq>={expected_seq} not received within {timeout_s:.1f}s. "
                    f"clean_tail={tail_candidate!r}"
                )

            try:
                chunk = child.read_nonblocking(
                    size=4096,
                    timeout=min(0.5, max(0.05, remaining)),
                )
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise SessionOpenError(
                    f"EOF while waiting for keystroke ACK. {self._buf(child)}"
                ) from exc

            if not chunk:
                continue

            self._enqueue_stream_chunk(child, chunk)

    def _measure_echo(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        ack_prefix: str,
    ) -> Tuple[str, float]:
        
        token = self._token(protocol, trial_id, sample_id)
        ack_line = ack_prefix + token
        echo_timeout = float(getattr(self.args, "echo_timeout", self.args.timeout))

        t0 = time.perf_counter_ns()
        child.sendline(token)
        self._wait_exact_line(child, ack_line, echo_timeout)
        t1 = time.perf_counter_ns()

        return token, (t1 - t0) / 1e6

    def _measure_keystroke_latency(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        ack_prefix: str,
        expected_seq: int,
    ) -> Tuple[str, float, int]:

        key_byte = random.choice(string.ascii_letters)
        sent_hex = format(ord(key_byte), "02x")
        key_timeout = float(getattr(self.args, "echo_timeout", self.args.timeout))

        token = self._token(protocol, trial_id, sample_id)
        self._drain_output_until_quiet(child, quiet_s=0.01, budget_s=0.05)
        t0 = time.perf_counter_ns()
        child.send(key_byte)

        deadline = time.monotonic() + key_timeout
        mismatches: List[str] = []
        next_expected = expected_seq
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                detail = "; ".join(mismatches[-3:])
                if detail:
                    raise pexpect.TIMEOUT(
                        f"keystroke ACK seq>={expected_seq} with byte {sent_hex!r} not received within {key_timeout:.1f}s. {detail}"
                    )
                raise pexpect.TIMEOUT(
                    f"keystroke ACK seq>={expected_seq} with byte {sent_hex!r} not received within {key_timeout:.1f}s"
                )

            seq, ack_hex, ack_raw = self._wait_keystroke_ack(
                child, next_expected, min(remaining, key_timeout)
            )
            if ack_hex == sent_hex:
                t1 = time.perf_counter_ns()
                return token, (t1 - t0) / 1e6, seq + 1

            mismatches.append(f"seq {seq}: got {ack_hex!r} via {ack_raw!r}")
            next_expected = seq + 1


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

        expected_obs = state * 3 + 7
        obs_marker = f"__W3OBS__ {token} {expected_obs}"
        cmd1 = f"printf '\\n__W3OBS__ {token} %d\\n' $(({state}*3+7))"

        t_obs_send = time.perf_counter_ns()
        child.sendline(cmd1)
        clean_obs = self._wait_line_prefix(child, obs_marker, timeout_s)
        t_obs_recv = time.perf_counter_ns()

        m = re.search(rf"__W3OBS__\s+{re.escape(token)}\s+(-?\d+)", clean_obs)
        if not m:
            raise RuntimeError(f"Cannot parse observation for token={token}")
        obs = int(m.group(1))

        if obs % 2 == 0:
            action, next_state = "INC", obs + 1
        else:
            action, next_state = "DEC", obs - 1

        act_marker = f"__W3ACT__ {token} {action} {next_state}"
        cmd2 = f"printf '\\n__W3ACT__ {token} {action} {next_state}\\n'"

        t_act_send = time.perf_counter_ns()
        child.sendline(cmd2)
        self._wait_line_prefix(child, act_marker, timeout_s)
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

    def _reopen_trial_session(
        self,
        protocol: str,
        child: Optional[pexpect.spawn],
        trial_id: int,
        need_echo_helper: bool,
        predict_mode: str = "adaptive",
    ) -> Tuple[pexpect.spawn, Optional[str], Optional[str]]:
        if child is not None:
            self._safe_close(child)
        reopened_child, _ = self._open_session(protocol, predict_mode=predict_mode)
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
                initial_predict = "never" if protocol == "mosh" else "adaptive"

                child, setup_ms = self._open_session(protocol, predict_mode=initial_predict)
                current_predict = initial_predict
                setup_ok = True
                self._record_ok(
                    protocol, "session_setup", trial_id, 1, False, "__W3_SETUP__", setup_ms
                )
                print(f"[{protocol:>4}/setup      ] trial {trial_id:>2}:  OK  {setup_ms:.1f} ms")

                
                if "keystroke_latency" in self.args.metrics:
                    key_ack_prefix, key_bye_marker = self._start_keystroke_helper(
                        child, protocol, trial_id
                    )
                    helper_seq = 1
                    try:
                        for i in range(1, self.args.warmup_samples + 1):
                            sid = -i
                            try:
                                tok, lat, helper_seq = self._measure_keystroke_latency(
                                    child, protocol, trial_id, sid, key_ack_prefix, helper_seq,
                                )
                                self._record_ok(
                                    protocol, "keystroke_latency", trial_id, sid, True, tok, lat
                                )
                                print(
                                    f"[{protocol:>4}/key  warm {i:>3}/{self.args.warmup_samples}]"
                                    f"  {lat:.2f} ms"
                                )
                            except Exception as exc:
                                self._record_fail(
                                    protocol, "keystroke_latency", trial_id, sid, True, exc, child
                                )
                                print(
                                    f"[{protocol:>4}/key  warm {i:>3}     ]  FAIL"
                                    f"  {type(exc).__name__}: {exc}"
                                )
                                if not self.args.reopen_on_failure:
                                    raise
                                try:
                                    self._stop_keystroke_helper(child, key_bye_marker)
                                except Exception:
                                    pass
                                key_ack_prefix, key_bye_marker = self._start_keystroke_helper(
                                    child, protocol, trial_id
                                )
                                helper_seq = 1
                                continue

                        for sid in range(1, self.args.samples_per_trial + 1):
                            try:
                                tok, lat, helper_seq = self._measure_keystroke_latency(
                                    child, protocol, trial_id, sid, key_ack_prefix, helper_seq,
                                )
                                self._record_ok(
                                    protocol, "keystroke_latency", trial_id, sid, False, tok, lat
                                )
                                print(
                                    f"[{protocol:>4}/key  meas {sid:>3}/{self.args.samples_per_trial}]"
                                    f"  {lat:.2f} ms"
                                )
                            except Exception as exc:
                                self._record_fail(
                                    protocol, "keystroke_latency", trial_id, sid, False, exc, child
                                )
                                print(
                                    f"[{protocol:>4}/key  meas {sid:>3}     ]  FAIL"
                                    f"  {type(exc).__name__}: {exc}"
                                )
                                if not self.args.reopen_on_failure:
                                    raise
                                try:
                                    self._stop_keystroke_helper(child, key_bye_marker)
                                except Exception:
                                    pass
                                key_ack_prefix, key_bye_marker = self._start_keystroke_helper(
                                    child, protocol, trial_id
                                )
                                helper_seq = 1
                                continue
                    finally:
                        try:
                            self._stop_keystroke_helper(child, key_bye_marker)
                        except Exception:
                            pass

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
                            self._record_ok(
                                protocol, "control_step_latency", trial_id, sid, True, tok, lat
                            )
                            print(
                                f"[{protocol:>4}/ctl  warm {i:>3}/{self.args.warmup_samples}]"
                                f"  net={lat:.2f} ms"
                                f"  (obs={csr.obs_rtt_ms:.2f}"
                                f" proc={csr.processing_overhead_ms:.2f}"
                                f" act={csr.act_rtt_ms:.2f}"
                                f" wall={csr.control_step_wall_ms:.2f})"
                            )
                        except Exception as exc:
                            self._record_fail(
                                protocol, "control_step_latency", trial_id, sid, True, exc, child
                            )
                            print(
                                f"[{protocol:>4}/ctl  warm {i:>3}     ]  FAIL"
                                f"  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, _, _ = self._reopen_trial_session(
                                protocol, child, trial_id,
                                need_echo_helper=False,
                                predict_mode=current_predict,
                            )
                            continue

                    for sid in range(1, self.args.samples_per_trial + 1):
                        try:
                            tok, lat, agent_state, csr = self._measure_control_step_latency(
                                child, protocol, trial_id, sid, agent_state,
                            )
                            self.control_step_records.append(csr)
                            self._record_ok(
                                protocol, "control_step_latency", trial_id, sid, False, tok, lat
                            )
                            print(
                                f"[{protocol:>4}/ctl  meas {sid:>3}/{self.args.samples_per_trial}]"
                                f"  net={lat:.2f} ms"
                                f"  (obs={csr.obs_rtt_ms:.2f}"
                                f" proc={csr.processing_overhead_ms:.2f}"
                                f" act={csr.act_rtt_ms:.2f}"
                                f" wall={csr.control_step_wall_ms:.2f})"
                            )
                        except Exception as exc:
                            self._record_fail(
                                protocol, "control_step_latency", trial_id, sid, False, exc, child
                            )
                            print(
                                f"[{protocol:>4}/ctl  meas {sid:>3}     ]  FAIL"
                                f"  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, _, _ = self._reopen_trial_session(
                                protocol, child, trial_id,
                                need_echo_helper=False,
                                predict_mode=current_predict,
                            )
                            continue

                if "line_echo" in self.args.metrics:
                    ack_prefix, bye_marker = self._start_helper(child, protocol, trial_id)
                    for i in range(1, self.args.warmup_samples + 1):
                        sid = -i
                        try:
                            tok, lat = self._measure_echo(
                                child, protocol, trial_id, sid, ack_prefix
                            )
                            self._record_ok(
                                protocol, "line_echo", trial_id, sid, True, tok, lat
                            )
                            print(
                                f"[{protocol:>4}/echo warm {i:>3}/{self.args.warmup_samples}]"
                                f"  {lat:.2f} ms"
                            )
                        except Exception as exc:
                            self._record_fail(
                                protocol, "line_echo", trial_id, sid, True, exc, child
                            )
                            print(
                                f"[{protocol:>4}/echo warm {i:>3}     ]  FAIL"
                                f"  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, ack_prefix, bye_marker = self._reopen_trial_session(
                                protocol, child, trial_id,
                                need_echo_helper=True,
                                predict_mode=current_predict,
                            )
                            continue

                    for sid in range(1, self.args.samples_per_trial + 1):
                        try:
                            tok, lat = self._measure_echo(
                                child, protocol, trial_id, sid, ack_prefix
                            )
                            self._record_ok(
                                protocol, "line_echo", trial_id, sid, False, tok, lat
                            )
                            print(
                                f"[{protocol:>4}/echo meas {sid:>3}/{self.args.samples_per_trial}]"
                                f"  {lat:.2f} ms"
                            )
                        except Exception as exc:
                            self._record_fail(
                                protocol, "line_echo", trial_id, sid, False, exc, child
                            )
                            print(
                                f"[{protocol:>4}/echo meas {sid:>3}     ]  FAIL"
                                f"  {type(exc).__name__}: {exc}"
                            )
                            if not self.args.reopen_on_failure:
                                raise
                            child, ack_prefix, bye_marker = self._reopen_trial_session(
                                protocol, child, trial_id,
                                need_echo_helper=True,
                                predict_mode=current_predict,
                            )
                            continue

            except Exception as exc:
                if not setup_ok:
                    self._record_fail(
                        protocol, "session_setup", trial_id, 1, False, exc, child
                    )
                    print(
                        f"[{protocol:>4}/setup      ] trial {trial_id:>2}:  FAIL"
                        f"  {type(exc).__name__}: {exc}"
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
        """
        Linear interpolation percentile — Hyndman & Fan (1996) type-7,
        equivalent to numpy.percentile default.
        """
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
        if metric in {"line_echo", "control_step_latency", "keystroke_latency"}:
            return self.args.trials * self.args.samples_per_trial
        if metric == "session_setup":
            return self.args.trials
        return 0

    def _summary_row(self, protocol: str, metric: str) -> SummaryRow:
        data = self.results[protocol][metric]
        recorded_failures = sum(
            1 for f in self.failures
            if f.protocol == protocol and f.metric == metric and not f.is_warmup
        )
        n = len(data)
        budget = self._metric_budget(protocol, metric)
        missing = max(0, budget - (n + recorded_failures))
        failures = recorded_failures + missing
        total = max(budget, n + recorded_failures) if budget > 0 else (n + failures)
        rate = 100.0 * n / total if total else 0.0

        if n == 0:
            return SummaryRow(
                protocol, metric, 0, failures, rate,
                None, None, None, None, None, None, None, None,
            )

        mean   = statistics.mean(data)
        median = statistics.median(data)
        stdev  = statistics.stdev(data) if n > 1 else 0.0
        ci95   = 1.96 * stdev / math.sqrt(n) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol, metric=metric, n=n, failures=failures,
            success_rate_pct=rate, min_ms=min(data),
            mean_ms=mean, median_ms=median, stdev_ms=stdev,
            p95_ms=self._pct(data, 95), p99_ms=self._pct(data, 99),
            max_ms=max(data), ci95_half_width_ms=ci95,
        )

    def summaries(self) -> List[SummaryRow]:
        return [self._summary_row(p, m) for p in self.args.protocols for m in self.args.metrics]

    def _processing_overhead_stats(self) -> Dict[str, Dict]:
        by_proto: Dict[str, List[float]] = {}
        for csr in self.control_step_records:
            if csr.is_warmup:
                continue
            by_proto.setdefault(csr.protocol, []).append(csr.processing_overhead_ms)
        out: Dict[str, Dict] = {}
        for proto, vals in by_proto.items():
            if not vals:
                continue
            out[proto] = {
                "n":          len(vals),
                "mean_ms":    statistics.mean(vals),
                "median_ms":  statistics.median(vals),
                "max_ms":     max(vals),
                "p99_ms":     self._pct(vals, 99) or 0.0,
                "note": (
                    "Local Python decision time (t_act_send - t_obs_recv). "
                    "Excluded from control_step_latency_ms; reflects client CPU speed."
                ),
            }
        return out

    def print_report(self) -> None:
        w = 155
        print("\n" + "=" * w)
        print(
            f"{'Protocol':<8} | {'Metric':<22} | {'N':>5} | {'Fail':>5} | "
            f"{'OK%':>6} | {'Min':>8} | {'Mean':>8} | {'Median':>8} | "
            f"{'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95±':>9}"
        )
        print("-" * w)
        fmt_ms = lambda v: f"{v:.2f}" if v is not None else "N/A"
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.metric:<22} | {row.n:>5} | "
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
                "started_at_utc":  self.started_at,
                "target":          self.target,
                "client_source_ip": self.args.source_ip,
                "protocols":       self.args.protocols,
                "requested_metrics": self.requested_metrics,
                "metrics":         self.args.metrics,
                "metric_aliases":  self.METRIC_ALIASES,
                "trials":          self.args.trials,
                "samples_per_trial": self.args.samples_per_trial,
                "warmup_samples":  self.args.warmup_samples,
                "warmup_floor":    _WARMUP_FLOOR,
                "timeout_sec":     self.args.timeout,
                "echo_timeout_sec": getattr(self.args, "echo_timeout", self.args.timeout),
                "pty_cols":        self.args.pty_cols,
                "pty_rows":        self.args.pty_rows,
                "random_seed":     self.args.seed,
                "mosh_predict_for_measurements": "never",
                "mosh_predict_note": (
                    "Mosh latency measurements use --predict=never to disable "
                    "speculative local rendering.  Without this, the PTY stream "
                    "contains locally-predicted output that may arrive in a "
                    "different order or be coalesced differently than the "
                    "server-confirmed frames, causing the stream reader to miss "
                    "marker lines (BUG-6)."
                ),
                "topology": {
                    "client": self.args.source_ip or "default-route",
                    "server": self.args.host,
                },
                "client_system": {
                    "python":   sys.version.split()[0],
                    "platform": platform.platform(),
                    "hostname": platform.node(),
                },
                "remote_system": asdict(self.remote_meta),
                "metric_notes": {
                    "session_setup_ms": (
                        "Time-to-usable-shell: from pexpect.spawn() until the remote shell "
                        "is fully configured (PS1, stty, bracketed-paste). "
                        "Not a pure transport handshake metric."
                    ),
                    "control_step_latency_ms": (
                        "PRIMARY metric. obs_rtt_ms + act_rtt_ms. "
                        "Excludes local Python decision time (processing_overhead_ms). "
                        "Measured with mosh --predict=never."
                    ),
                    "control_step_wall_ms": (
                        "Full wall-clock: t_act_recv - t_obs_send. "
                        "= control_step_latency_ms + processing_overhead_ms. "
                        "Exported in control_step_details.csv for transparency."
                    ),
                    "processing_overhead_ms": (
                        "Local Python decision time: t_act_send - t_obs_recv. "
                        "Excluded from control_step_latency_ms; reflects client CPU only."
                    ),
                    "keystroke_latency_ms": (
                        "Per-byte ACK RTT via raw-mode remote Python helper. "
                        "From child.send(byte) to receipt of a framed ACK record. "
                        "Framed ACK parsing tolerates chunk coalescing and resyncs on seq jumps. "
                        "Measured with mosh --predict=never."
                    ),
                    "line_echo_ms": (
                        "Application-level line RTT "
                        "(Winstein & Balakrishnan 2012, SIGCOMM §4). "
                        "From sendline(token) to exact-line ACK from line-mode helper."
                    ),
                    "ping_rtt_ms": (
                        "ICMP baseline covariate, pre-session per trial. "
                        "Not concurrent with app measurements."
                    ),
                    "percentile_method": "Hyndman & Fan (1996) type-7 linear interpolation.",
                    "stdev_note":   "Sample standard deviation (ddof=1).",
                    "ci95_note":    "1.96 × stdev / sqrt(n).",
                    "success_rate_denominator": (
                        "Fixed budget: trials × samples_per_trial for per-sample metrics; "
                        "trials for session_setup."
                    ),
                },
                "processing_overhead_summary": self._processing_overhead_stats(),
                "skipped_protocols": self.protocol_skip_reasons,
            },
            "summary": [asdict(r) for r in self.summaries()],
        }
        jpath = out / "summary.json"
        jpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        # summary.csv
        cpath = out / "summary.csv"
        with cpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol", "metric", "n", "failures", "success_rate_pct",
                "min_ms", "mean_ms", "median_ms", "stdev_ms",
                "p95_ms", "p99_ms", "max_ms", "ci95_half_width_ms", "ping_rtt_mean_ms",
            ])
            for row in self.summaries():
                rtts = [x for x in self.ping_rtts.get(row.protocol, []) if x is not None]
                ping_mean = statistics.mean(rtts) if rtts else None
                w.writerow([
                    row.protocol, row.metric, row.n, row.failures,
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

        # samples.csv
        spath = out / "samples.csv"
        with spath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol", "metric", "trial_id", "sample_id",
                "is_warmup", "token", "latency_ms",
            ])
            for r in self.records:
                w.writerow([
                    r.protocol, r.metric, r.trial_id, r.sample_id,
                    r.is_warmup, r.token, f"{r.latency_ms:.6f}",
                ])

        # control_step_details.csv
        dpath = out / "control_step_details.csv"
        with dpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol", "trial_id", "sample_id", "is_warmup", "token",
                "obs_rtt_ms", "processing_overhead_ms", "act_rtt_ms",
                "control_step_latency_ms", "control_step_wall_ms",
                "t_obs_send_ns", "t_obs_recv_ns", "t_act_send_ns", "t_act_recv_ns",
            ])
            for csr in self.control_step_records:
                w.writerow([
                    csr.protocol, csr.trial_id, csr.sample_id, csr.is_warmup, csr.token,
                    f"{csr.obs_rtt_ms:.6f}",
                    f"{csr.processing_overhead_ms:.6f}",
                    f"{csr.act_rtt_ms:.6f}",
                    f"{csr.control_step_latency_ms:.6f}",
                    f"{csr.control_step_wall_ms:.6f}",
                    csr.t_obs_send_ns, csr.t_obs_recv_ns,
                    csr.t_act_send_ns, csr.t_act_recv_ns,
                ])

        # failures.csv
        fpath = out / "failures.csv"
        with fpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "protocol", "metric", "trial_id", "sample_id",
                "is_warmup", "error_type", "error_message",
            ])
            for r in self.failures:
                w.writerow([
                    r.protocol, r.metric, r.trial_id, r.sample_id,
                    r.is_warmup, r.error_type, r.error_message,
                ])

        # ping_rtts.csv
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
        print("  samples.csv               - every raw measurement (warmup + real)")
        print("  control_step_details.csv  - obs/proc/act/wall breakdown per step")
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