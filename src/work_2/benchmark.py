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
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

try:
    import pexpect
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: pexpect.  Install with: pip install pexpect"
    ) from exc

from constants import ANSI_NOISE
from exceptions import PreflightError, SessionOpenError
from models import FailureRecord, RemoteMeta, SampleRecord, SummaryRow

ANSI_STRIP_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b[@-Z\\-_]"
    r"|[\r\n\x00\x08]"
)


class Benchmark:
    def __init__(self, args: object) -> None:
        self.args        = args
        self.target      = f"{args.user}@{args.host}"
        self.started_at  = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.records:    List[SampleRecord]  = []
        self.failures:   List[FailureRecord] = []
        self.protocol_skip_reasons: Dict[str, str] = {}
        self.ping_rtts:  Dict[str, List[Optional[float]]] = {p: [] for p in args.protocols}
        self.remote_meta = RemoteMeta()
        self.results: Dict[str, Dict[str, List[float]]] = {
            protocol: {metric: [] for metric in args.metrics}
            for protocol in args.protocols
        }
        self._pattern_cache: Dict[str, re.Pattern] = {}

    def _literal_pattern(self, literal: str) -> re.Pattern:
        """Regex that matches literal even with ANSI escape sequences interleaved.
        Mosh injects cursor-positioning codes between characters of long tokens.
        Result is cached — each unique string compiled only once."""
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
        raw   = (getattr(child, "before", "") or "")[-limit:]
        clean = self._strip_ansi(raw)[-limit:]
        return f"raw={raw!r} clean={clean!r}"


    def _token(self, protocol: str, trial_id: int, sample_id: int) -> str:
        rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

        return f"W3T{protocol[:2].upper()}{trial_id:03d}{sample_id:04d}{rand}Z"

    def _ping_rtt_ms(self) -> Optional[float]:
        """One ICMP ping → RTT in ms, or None on failure."""
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", self.args.host],
                capture_output=True, text=True, timeout=5,
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
            args += ["-o", "StrictHostKeyChecking=no",
                     "-o", "UserKnownHostsFile=/dev/null"]
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
            parts   = ["mosh", f"--ssh={ssh_cmd}"]
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
        "Permission denied", "Connection refused", "No route to host",
        "Connection timed out", "Could not resolve hostname",
        "Cannot assign requested address", "Network is unreachable",
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
            if probe == 0: return
            if probe == 1: raise SessionOpenError("Password prompt during probe")
            if probe == 2: raise SessionOpenError("Permission denied during probe")
            if probe == 3: raise SessionOpenError(f"EOF during probe. {self._buf(child)}")
            raise SessionOpenError(f"Timeout during probe. {self._buf(child)}")

        raise SessionOpenError(f"Overall timeout. {self._buf(child)}")


    def _open_session(self, protocol: str) -> Tuple[pexpect.spawn, float]:
        t0    = time.perf_counter_ns()
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
                f"stty -echo -echoctl "
                f"cols {self.args.pty_cols} rows {self.args.pty_rows} "
                f"2>/dev/null || true"
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
                try: lf.close()
                except Exception: pass

    def _safe_close(self, child: pexpect.spawn) -> None:
        try: self._close_session(child)
        except Exception: pass


    def _record_ok(
        self, protocol: str, metric: str,
        trial_id: int, sample_id: int, is_warmup: bool,
        token: str, latency_ms: float,
    ) -> None:
        if is_warmup:
            return
        self.results[protocol][metric].append(latency_ms)
        self.records.append(
            SampleRecord(protocol, metric, trial_id, sample_id,
                         is_warmup, token, latency_ms)
        )

    def _record_fail(
        self, protocol: str, metric: str,
        trial_id: int, sample_id: int, is_warmup: bool,
        exc: Exception, child: Optional[pexpect.spawn] = None,
    ) -> None:
        extra = f" | {self._buf(child)}" if child is not None else ""
        self.failures.append(FailureRecord(
            protocol=protocol, metric=metric,
            trial_id=trial_id, sample_id=sample_id, is_warmup=is_warmup,
            error_type=type(exc).__name__,
            error_message=f"{exc}{extra}",
        ))


    def _remote_cmd(self, child: pexpect.spawn, cmd: str) -> str:
        child.sendline(cmd)
        self._expect_literal(child, self.args.prompt, timeout=12)
        clean = self._strip_ansi(child.before or "")
        lines = [l.strip() for l in clean.splitlines()
                 if l.strip() and l.strip() != cmd]
        return lines[-1] if lines else "unknown"

    def _collect_remote_meta(self, child: pexpect.spawn) -> None:
        self.remote_meta.kernel         = self._remote_cmd(child, "uname -r")
        self.remote_meta.mosh_version   = self._remote_cmd(
            child, "mosh --version 2>&1 | head -1")
        self.remote_meta.ssh_version    = self._remote_cmd(
            child, "ssh -V 2>&1 | head -1")
        self.remote_meta.ssh3_version   = self._remote_cmd(
            child,
            "command -v ssh3 >/dev/null 2>&1 "
            "&& ssh3 -version 2>&1 | head -1 "
            "|| printf 'not-installed\\n'",
        )
        self.remote_meta.python_version = self._remote_cmd(
            child, "python3 --version 2>&1")


    def _preflight_protocol(self, protocol: str) -> None:
        child, _ = self._open_session(protocol)
        try:
            child.sendline(
                "command -v python3 >/dev/null 2>&1 "
                "&& printf '__W3HAS_PY3__\\n' "
                "|| printf '__W3NO_PY3__\\n'"
            )
            idx = child.expect(
                [self._literal_pattern("__W3HAS_PY3__"),
                 self._literal_pattern("__W3NO_PY3__")],
                timeout=self.args.timeout,
            )
            self._expect_literal(child, self.args.prompt)
            if idx == 1:
                raise PreflightError("python3 not found on remote host")
            if self.remote_meta.kernel == "unknown":
                self._collect_remote_meta(child)
        finally:
            self._close_session(child)

    def _start_helper(
        self, child: pexpect.spawn, protocol: str, trial_id: int
    ) -> Tuple[str, str]:
        """Start the Python helper on the server. Returns (ack_prefix, bye_marker)."""
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
        child.sendline("printf 'W3BACK\\n'")
        self._expect_literal(child, "W3BACK", timeout=self.args.timeout)
        self._expect_literal(child, self.args.prompt, timeout=self.args.timeout)

    def _wait_ack_via_stream(
        self,
        child: pexpect.spawn,
        ack_marker: str,
        timeout_s: float,
    ) -> None:
        """Read PTY stream and search ACK in ANSI-stripped text.

        This avoids false TIMEOUTs caused by Mosh redraw/control-sequence
        interleaving that can break regex-based matching in pexpect's searcher.
        """
        deadline = time.monotonic() + timeout_s
        raw_parts: List[str] = []
        max_chars = 32768

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise pexpect.TIMEOUT(f"Timeout waiting ACK marker: {ack_marker}")

            try:
                chunk = child.read_nonblocking(
                    size=4096,
                    timeout=min(0.5, max(0.05, remaining)),
                )
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                raise SessionOpenError(f"EOF while waiting ACK. {self._buf(child)}") from exc

            if not chunk:
                continue

            raw_parts.append(chunk)
            if sum(len(x) for x in raw_parts) > max_chars:
                joined = "".join(raw_parts)[-max_chars:]
                raw_parts = [joined]

            cleaned = self._strip_ansi("".join(raw_parts))
            if ack_marker in cleaned:
                return

    def _measure_echo(
        self,
        child: pexpect.spawn,
        protocol: str,
        trial_id: int,
        sample_id: int,
        ack_prefix: str,
    ) -> Tuple[str, float]:
        """Measure one full application-level RTT.

        Timing starts at sendline(token) and ends when the server-side
        ack_prefix+token is received.  The helper emits the ACK only after
        it reads the token, guaranteeing a full network round-trip is covered.

        Timing is NOT stopped at any local PTY echo — this is critical for
        Mosh, whose local-prediction feature echoes tokens before the server
        receives them.
        """
        token      = self._token(protocol, trial_id, sample_id)
        ack_marker = ack_prefix + token

        t0 = time.perf_counter_ns()
        child.sendline(token)
        self._wait_ack_via_stream(child, ack_marker, self.args.timeout)
        t1 = time.perf_counter_ns()

        return token, (t1 - t0) / 1e6

    def _run_protocol(self, protocol: str) -> None:
        for trial_id in range(1, self.args.trials + 1):

            rtt   = self._ping_rtt_ms()
            self.ping_rtts[protocol].append(rtt)
            rtt_s = f"{rtt:.2f} ms" if rtt is not None else "N/A"
            print(f"[{protocol:>4}] trial {trial_id:>2}/{self.args.trials}"
                  f"  ping={rtt_s}")

            child:      Optional[pexpect.spawn] = None
            ack_prefix: Optional[str]           = None
            bye_marker: Optional[str]           = None
            setup_ok    = False

            try:
                child, setup_ms = self._open_session(protocol)
                setup_ok = True
                self._record_ok(protocol, "session_setup",
                                trial_id, 1, False, "__W3_SETUP__", setup_ms)
                print(f"[{protocol:>4}/setup      ] trial {trial_id:>2}:"
                      f"  OK  {setup_ms:.1f} ms")

                if "line_echo" not in self.args.metrics:
                    continue

                ack_prefix, bye_marker = self._start_helper(
                    child, protocol, trial_id)

                total = self.args.warmup_samples + self.args.samples_per_trial
                for i in range(1, total + 1):
                    is_warmup = i <= self.args.warmup_samples
                    sid = i if is_warmup else (i - self.args.warmup_samples)
                    tag = "warm" if is_warmup else "meas"
                    lim = (self.args.warmup_samples if is_warmup
                           else self.args.samples_per_trial)
                    try:
                        tok, lat = self._measure_echo(
                            child, protocol, trial_id, sid, ack_prefix)
                        self._record_ok(protocol, "line_echo",
                                        trial_id, sid, is_warmup, tok, lat)
                        print(f"[{protocol:>4}/echo {tag}"
                              f" {sid:>3}/{lim}]  {lat:.2f} ms")
                    except Exception as exc:
                        self._record_fail(protocol, "line_echo",
                                          trial_id, sid, is_warmup, exc, child)
                        print(f"[{protocol:>4}/echo {tag} {sid:>3}"
                              f"     ]  FAIL  {type(exc).__name__}: {exc}")
                        if not self.args.reopen_on_failure:
                            raise
                        break

            except Exception as exc:
                if not setup_ok:
                    self._record_fail(protocol, "session_setup",
                                      trial_id, 1, False, exc, child)
                    print(f"[{protocol:>4}/setup      ] trial {trial_id:>2}:"
                          f"  FAIL  {type(exc).__name__}: {exc}")

            finally:
                if child is not None:
                    if bye_marker is not None:
                        try: self._stop_helper(child, bye_marker)
                        except Exception: pass
                    self._safe_close(child)

    @staticmethod
    def _pct(data: List[float], p: float) -> Optional[float]:
        if not data: return None
        if len(data) == 1: return data[0]
        s  = sorted(data)
        k  = (len(s) - 1) * p / 100.0
        lo = math.floor(k)
        hi = math.ceil(k)
        return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)

    def _summary_row(self, protocol: str, metric: str) -> SummaryRow:
        data  = self.results[protocol][metric]
        fails = sum(
            1 for f in self.failures
            if f.protocol == protocol
            and f.metric == metric
            and not f.is_warmup
        )
        n     = len(data)
        total = n + fails
        rate  = 100.0 * n / total if total else 0.0

        if n == 0:
            return SummaryRow(protocol, metric, 0, fails, rate,
                              None, None, None, None, None, None, None, None)

        mean   = statistics.mean(data)
        median = statistics.median(data)
        stdev  = statistics.stdev(data) if n > 1 else 0.0
        ci95   = 1.96 * stdev / math.sqrt(n) if n > 1 else 0.0
        return SummaryRow(
            protocol=protocol, metric=metric, n=n, failures=fails,
            success_rate_pct=rate,
            min_ms=min(data), mean_ms=mean, median_ms=median,
            stdev_ms=stdev,
            p95_ms=self._pct(data, 95), p99_ms=self._pct(data, 99),
            max_ms=max(data), ci95_half_width_ms=ci95,
        )

    def summaries(self) -> List[SummaryRow]:
        return [
            self._summary_row(p, m)
            for p in self.args.protocols
            for m in self.args.metrics
        ]

    def print_report(self) -> None:
        W = 150
        print("\n" + "=" * W)
        print(
            f"{'Protocol':<8} | {'Metric':<14} | {'N':>5} | {'Fail':>4} | "
            f"{'OK%':>6} | {'Min':>8} | {'Mean':>8} | {'Median':>8} | "
            f"{'Std':>8} | {'P95':>8} | {'P99':>8} | {'Max':>8} | {'CI95±':>9}"
        )
        print("-" * W)
        fmt = lambda v: f"{v:8.2f}" if v is not None else "     N/A"
        for row in self.summaries():
            print(
                f"{row.protocol:<8} | {row.metric:<14} | {row.n:>5} | "
                f"{row.failures:>4} | {row.success_rate_pct:>5.1f}% | "
                f"{fmt(row.min_ms)} | {fmt(row.mean_ms)} | {fmt(row.median_ms)} | "
                f"{fmt(row.stdev_ms)} | {fmt(row.p95_ms)} | {fmt(row.p99_ms)} | "
                f"{fmt(row.max_ms)} | {fmt(row.ci95_half_width_ms)}"
            )
        print("=" * W)
        if self.protocol_skip_reasons:
            print("\nSkipped protocols:")
            for p, r in self.protocol_skip_reasons.items():
                print(f"  {p}: {r}")

    def export(self) -> None:
        out = Path(self.args.output_dir)
        out.mkdir(parents=True, exist_ok=True)

        payload = {
            "meta": {
                "started_at_utc":    self.started_at,
                "target":            self.target,
                "client_source_ip":  self.args.source_ip,
                "protocols":         self.args.protocols,
                "metrics":           self.args.metrics,
                "trials":            self.args.trials,
                "samples_per_trial": self.args.samples_per_trial,
                "warmup_samples":    self.args.warmup_samples,
                "timeout_sec":       self.args.timeout,
                "pty_cols":          self.args.pty_cols,
                "pty_rows":          self.args.pty_rows,
                "random_seed":       self.args.seed,
                "mosh_predict":      self.args.mosh_predict,
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
                        "Wall time from pexpect.spawn() until remote shell accepts commands "
                        "(DNS + transport connect + crypto handshake + key auth + "
                        "shell init + PS1 acknowledgement). One sample per trial."
                    ),
                    "line_echo_ms": (
                        "True application-level RTT: sendline(token) to server-side ACK. "
                        "ACK emitted by remote Python helper after it reads the token. "
                        "NOT stopped at local PTY echo — ensures Mosh local-prediction "
                        "does not artificially deflate results."
                    ),
                },
                "skipped_protocols": self.protocol_skip_reasons,
            },
            "summary": [asdict(r) for r in self.summaries()],
        }
        jpath = out / "summary.json"
        jpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        spath = out / "samples.csv"
        with spath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "metric", "trial_id", "sample_id",
                        "is_warmup", "token", "latency_ms"])
            for r in self.records:
                w.writerow([r.protocol, r.metric, r.trial_id, r.sample_id,
                            r.is_warmup, r.token, f"{r.latency_ms:.6f}"])

        fpath = out / "failures.csv"
        with fpath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "metric", "trial_id", "sample_id",
                        "is_warmup", "error_type", "error_message"])
            for r in self.failures:
                w.writerow([r.protocol, r.metric, r.trial_id, r.sample_id,
                            r.is_warmup, r.error_type, r.error_message])

        ppath = out / "ping_rtts.csv"
        with ppath.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["protocol", "trial_id", "ping_rtt_ms"])
            for proto, rtts in self.ping_rtts.items():
                for i, rtt in enumerate(rtts, 1):
                    w.writerow([proto, i,
                                f"{rtt:.3f}" if rtt is not None else ""])

        print(f"\nOutputs written to {out}/")
        print("  summary.json  — metadata + per-protocol statistics")
        print("  samples.csv   — every raw measurement")
        print("  failures.csv  — every failure with context")
        print("  ping_rtts.csv — network baseline per trial")

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
                    print(f"[preflight/{p}] SKIP — {type(exc).__name__}: {exc}")
            protocols = approved

        for p in protocols:
            print(f"\n{'─' * 60}\nProtocol: {p}\n{'─' * 60}")
            self._run_protocol(p)