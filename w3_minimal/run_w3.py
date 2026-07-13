#!/usr/bin/env python3
import csv
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    import pexpect
except ImportError:
    print("Missing dependency: pexpect. Install with: python3 -m pip install pexpect", file=sys.stderr)
    sys.exit(1)

PROFILES = {
    "c0_only": [],
    "c0_bg2": ["log", "ping"],
    "c0_bg4": ["log", "ping", "sysmon", "output"],
    "c0_bg4_heavy": ["log", "ping", "sysmon", "output_heavy"],
}

DEFAULT_TARGETS = "shell,vim,nano"
DEFAULT_PROBE_SEQUENCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def load_env(path: str) -> dict:
    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def q(s: str) -> str:
    return shlex.quote(str(s))


def split_csv(value: str) -> list:
    return [p.strip() for p in value.split(",") if p.strip()]


def split_args(value: str) -> list:
    return shlex.split(value) if value.strip() else []


def bool_cfg(cfg: dict, name: str, default: str = "0") -> bool:
    return cfg.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def qjoin(cmd) -> str:
    if isinstance(cmd, str):
        return cmd
    return shlex.join([str(part) for part in cmd])


class ProtocolRunner:
    def __init__(self, cfg: dict, protocol: str):
        self.cfg = cfg
        self.protocol = protocol
        self.user = cfg["SERVER_USER"]
        self.host = cfg["SERVER_HOST"]
        self.port = cfg.get("SERVER_PORT", "").strip()
        self.remote = cfg.get("REMOTE_WORKLOAD", "/tmp/w3_remote_workloads.sh")
        self.log_dir = Path(cfg.get("LOG_DIR", "logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir = Path(cfg.get("RESULT_DIR", "results"))
        self.result_dir.mkdir(parents=True, exist_ok=True)
        port_id = self.port or "default"
        self.cm_path = f"/tmp/w3_cm_{self.user}_{self.host}_{port_id}"
        self.audit_path = self.result_dir / "ssh3_audit.csv"
        self.audit_fields = [
            "ts", "protocol", "target", "profile", "role", "channel_name",
            "launcher_pid", "process_pids", "udp_sockets", "cmd", "log_path",
            "multiplex_hint", "note",
        ]
        self.stream_audit_path = self.result_dir / "ssh3_stream_audit.csv"
        self.stream_audit_fields = [
            "ts", "protocol", "target", "profile", "log_path",
            "stream_ids", "channel_ids", "matched_lines", "note",
        ]
        if not self.audit_path.exists():
            with open(self.audit_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.audit_fields).writeheader()
        if not self.stream_audit_path.exists():
            with open(self.stream_audit_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.stream_audit_fields).writeheader()

    def ssh_port_args(self) -> list:
        return ["-p", self.port] if self.port else []

    def mosh_args(self) -> list:
        args = []
        predict = self.cfg.get("MOSH_PREDICT", "").strip()
        if predict:
            args.extend(["--predict", predict])
        if self.port:
            args.append(f"--ssh=ssh -p {self.port}")
        args.extend(split_args(self.cfg.get("MOSH_EXTRA_ARGS", "")))
        return args

    def ssh3_extra_args(self, target: str, profile: str, role: str) -> list:
        args = []
        if bool_cfg(self.cfg, "SSH3_INSECURE", "0"):
            args.append("-insecure")
        privkey = self.cfg.get("SSH3_PRIVKEY", "").strip()
        if privkey:
            args.extend(["-privkey", os.path.expanduser(privkey)])
        if bool_cfg(self.cfg, "SSH3_VERBOSE", "1"):
            args.append("-v")
        keylog_template = self.cfg.get("SSH3_KEYLOG_TEMPLATE", "").strip()
        if keylog_template:
            keylog_path = keylog_template.format(
                protocol=self.protocol,
                target=target,
                profile=profile,
                role=role,
                pid=os.getpid(),
                ts=int(time.time()),
            )
            args.extend(["-keylog", keylog_path])
        args.extend(split_args(self.cfg.get("SSH3_EXTRA_ARGS", "")))
        return args

    def format_ssh3_template(self, template: str, target: str, profile: str, role: str, cmd: str = "") -> str:
        extra_args = shlex.join(self.ssh3_extra_args(target, profile, role))
        return template.format(
            user=self.user,
            host=self.host,
            port=self.port,
            cmd=cmd,
            ssh3_extra_args=extra_args,
            ssh3_path=self.cfg.get("SSH3_PATH", ""),
        )

    def child_pids(self, pid: int) -> list:
        seen = set()
        pending = [pid]
        out = []
        while pending:
            parent = pending.pop()
            try:
                res = subprocess.run(
                    ["pgrep", "-P", str(parent)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                continue
            for raw in res.stdout.split():
                try:
                    child_pid = int(raw)
                except ValueError:
                    continue
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                out.append(child_pid)
                pending.append(child_pid)
        return out

    def process_pids(self, launcher_pid: int) -> list:
        pids = [launcher_pid]
        pids.extend(self.child_pids(launcher_pid))
        return pids

    def udp_sockets_for_pids(self, pids: list) -> str:
        rows = []
        for pid in pids:
            try:
                res = subprocess.run(
                    ["lsof", "-nP", "-a", "-p", str(pid), "-iUDP"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                continue
            for line in res.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9:
                    rows.append(f"pid={pid} {' '.join(parts[8:])}")
        if rows:
            return " | ".join(rows)

        try:
            res = subprocess.run(
                ["ss", "-H", "-u", "-p", "-n"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            return ""
        wanted = {f"pid={pid}," for pid in pids}
        for line in res.stdout.splitlines():
            if any(pid_text in line for pid_text in wanted):
                rows.append(line.strip())
        return " | ".join(rows)

    def write_audit(self, protocol: str, target: str, profile: str, role: str, channel_name: str, launcher_pid: int, cmd, log_path: str = "", note: str = ""):
        if protocol != "ssh3" and not bool_cfg(self.cfg, "AUDIT_ALL_PROTOCOLS", "0"):
            return
        pids = self.process_pids(launcher_pid)
        sockets = self.udp_sockets_for_pids(pids)
        hint = "unknown"
        if protocol == "ssh3":
            unique_udp = {item.strip() for item in sockets.split("|") if item.strip()}
            if len(unique_udp) > 1:
                hint = "separate_udp_sockets_likely_separate_quic_connections"
            elif len(unique_udp) == 1:
                hint = "one_udp_socket_seen_for_this_launcher"
            else:
                hint = "no_udp_socket_observed"
        row = {
            "ts": time.time(),
            "protocol": protocol,
            "target": target,
            "profile": profile,
            "role": role,
            "channel_name": channel_name,
            "launcher_pid": launcher_pid,
            "process_pids": "+".join(str(p) for p in pids),
            "udp_sockets": sockets,
            "cmd": qjoin(cmd),
            "log_path": log_path,
            "multiplex_hint": hint,
            "note": note,
        }
        with open(self.audit_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.audit_fields).writerow(row)

    def write_connection_summary(self, target: str, profile: str, child_pid: int, bg_procs: list):
        if self.protocol != "ssh3":
            return
        launcher_pids = [child_pid]
        launcher_pids.extend(p.pid for p, _ in bg_procs)
        all_pids = []
        for pid in launcher_pids:
            all_pids.extend(self.process_pids(pid))
        seen_pids = []
        for pid in all_pids:
            if pid not in seen_pids:
                seen_pids.append(pid)
        sockets = self.udp_sockets_for_pids(seen_pids)
        unique_udp = {item.strip() for item in sockets.split("|") if item.strip()}
        if len(unique_udp) > 1:
            hint = "multiple_udp_sockets_observed_likely_not_single_quic_connection"
        elif len(unique_udp) == 1:
            hint = "single_udp_socket_observed_for_all_launchers"
        else:
            hint = "no_udp_socket_observed"
        row = {
            "ts": time.time(),
            "protocol": self.protocol,
            "target": target,
            "profile": profile,
            "role": "trial_summary",
            "channel_name": "all",
            "launcher_pid": child_pid,
            "process_pids": "+".join(str(p) for p in seen_pids),
            "udp_sockets": sockets,
            "cmd": "trial_summary",
            "log_path": "",
            "multiplex_hint": hint,
            "note": f"launcher_pids={'+'.join(str(p) for p in launcher_pids)} unique_udp_socket_rows={len(unique_udp)}",
        }
        with open(self.audit_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.audit_fields).writerow(row)

    def scan_stream_debug(self, log_path: Path) -> dict:
        max_bytes = int(self.cfg.get("SSH3_STREAM_SCAN_BYTES", "200000"))
        stream_ids = set()
        channel_ids = set()
        matches = []
        note = ""
        stream_re = re.compile(r"(?i)\b(?:stream|streamid|stream id|quic stream)\D{0,24}(\d+)")
        channel_re = re.compile(r"(?i)\b(?:channel|channelid|channel id)\D{0,24}(\d+)")
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                try:
                    size = log_path.stat().st_size
                    if size > max_bytes:
                        f.seek(size - max_bytes)
                except Exception:
                    pass
                for line in f:
                    for m in stream_re.finditer(line):
                        stream_ids.add(m.group(1))
                    for m in channel_re.finditer(line):
                        channel_ids.add(m.group(1))
                    if (stream_re.search(line) or channel_re.search(line)) and len(matches) < 20:
                        matches.append(line.strip()[:240])
        except Exception as exc:
            note = repr(exc)
        return {
            "stream_ids": "+".join(sorted(stream_ids, key=lambda x: int(x) if x.isdigit() else x)),
            "channel_ids": "+".join(sorted(channel_ids, key=lambda x: int(x) if x.isdigit() else x)),
            "matched_lines": " || ".join(matches),
            "note": note,
        }

    def write_stream_audit(self, target: str, profile: str):
        if self.protocol != "ssh3":
            return
        paths = []
        paths.extend(self.log_dir.glob(f"ssh3_{target}_{profile}*_debug.log"))
        paths.extend(self.log_dir.glob(f"ssh3_{target}_{profile}_ch*.log"))
        paths.extend(self.log_dir.glob(f"{self.protocol}_{target}_{profile}_ch*.log"))
        seen = set()
        with open(self.stream_audit_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.stream_audit_fields)
            for log_path in paths:
                if log_path in seen or not log_path.exists():
                    continue
                seen.add(log_path)
                parsed = self.scan_stream_debug(log_path)
                writer.writerow({
                    "ts": time.time(),
                    "protocol": self.protocol,
                    "target": target,
                    "profile": profile,
                    "log_path": str(log_path),
                    "stream_ids": parsed["stream_ids"],
                    "channel_ids": parsed["channel_ids"],
                    "matched_lines": parsed["matched_lines"],
                    "note": parsed["note"],
                })

    def start_master_if_needed(self):
        if self.protocol != "ssh":
            return
        ssh = self.cfg.get("SSH_BIN", "ssh")
        # One SSHv2 TCP connection, then multiple session channels via ControlMaster.
        cmd = [
            ssh, "-MNf",
            "-o", "ControlMaster=yes",
            "-o", "ControlPersist=120s",
            "-o", f"ControlPath={self.cm_path}",
            *self.ssh_port_args(),
            f"{self.user}@{self.host}",
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(0.3)

    def stop_master_if_needed(self):
        if self.protocol != "ssh":
            return
        ssh = self.cfg.get("SSH_BIN", "ssh")
        cmd = [ssh, "-S", self.cm_path, "-O", "exit", *self.ssh_port_args(), f"{self.user}@{self.host}"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    def interactive_cmd(self, target: str = "session", profile: str = "interactive"):
        if self.protocol == "ssh":
            ssh = self.cfg.get("SSH_BIN", "ssh")
            return [
                ssh, "-tt",
                "-o", f"ControlPath={self.cm_path}",
                *self.ssh_port_args(),
                f"{self.user}@{self.host}",
                "bash --noprofile --norc -i",
            ], False

        if self.protocol == "ssh3":
            tmpl = self.cfg.get("SSH3_INTERACTIVE_TEMPLATE", "ssh3 {ssh3_extra_args} {user}@{host}{ssh3_path}")
            cmd = self.format_ssh3_template(tmpl, target, profile, "interactive")
            return cmd, True

        if self.protocol == "mosh":
            mosh = self.cfg.get("MOSH_BIN", "mosh")
            return [
                mosh,
                *self.mosh_args(),
                f"{self.user}@{self.host}",
                "--",
                "bash",
                "--noprofile",
                "--norc",
                "-i",
            ], False

        raise ValueError(f"unknown protocol: {self.protocol}")

    def bg_cmd(self, bg_name: str, target: str, profile: str):
        if bg_name == "output_heavy":
            remote_cmd = f"bash {q(self.remote)} output heavy"
        else:
            remote_cmd = f"bash {q(self.remote)} {q(bg_name)}"
        remote_cmd = (
            f"W3_TARGET={q(target)} W3_PROTOCOL={q(self.protocol)} "
            f"W3_PROFILE={q(profile)} {remote_cmd}"
        )

        if self.protocol == "ssh":
            ssh = self.cfg.get("SSH_BIN", "ssh")
            return [
                ssh, "-T",
                "-o", f"ControlPath={self.cm_path}",
                *self.ssh_port_args(),
                f"{self.user}@{self.host}",
                remote_cmd,
            ], False

        if self.protocol == "ssh3":
            # For true SSH3 multiplexing, replace this template with an instrumented ssh3 client
            # that opens several session channels inside one SSH3 conversation.
            tmpl = self.cfg.get("SSH3_COMMAND_TEMPLATE", "ssh3 {ssh3_extra_args} {user}@{host}{ssh3_path} {cmd}")
            cmd = self.format_ssh3_template(tmpl, target, profile, bg_name, cmd=q(remote_cmd))
            return cmd, True

        raise ValueError("mosh has no independent background session channels")

    def spawn_interactive(self, target: str, profile: str):
        cmd, shell = self.interactive_cmd(target, profile)
        log_path = ""
        if shell:
            child = pexpect.spawn("/bin/bash", ["-lc", cmd], encoding="utf-8", timeout=5)
        else:
            child = pexpect.spawn(cmd[0], cmd[1:], encoding="utf-8", timeout=5)
        child.delaybeforesend = 0
        if self.protocol == "ssh3" and bool_cfg(self.cfg, "SSH3_CAPTURE_DEBUG", "1"):
            log_path = str(self.log_dir / f"ssh3_{target}_{profile}_interactive_debug.log")
            child.logfile_read = open(log_path, "w", encoding="utf-8", errors="ignore")
        elif self.cfg.get("SHOW_TERMINAL_OUTPUT", "0") == "1":
            child.logfile_read = sys.stdout
        if self.protocol == "ssh3":
            time.sleep(0.25)
        self.write_audit(self.protocol, target, profile, "interactive", "c0", child.pid, cmd, log_path)
        # Make shell readiness less dependent on prompt format.
        child.sendline("printf W3; printf READY; printf '\\n'")
        try:
            child.expect("W3READY", timeout=5)
        except Exception:
            pass
        return child

    def start_background_channels(self, target: str, profile: str, bgs: list):
        procs = []
        failures = 0
        for idx, bg in enumerate(bgs, start=1):
            if self.protocol == "mosh":
                continue
            cmd, shell = self.bg_cmd(bg, target, profile)
            log_path = self.log_dir / f"{self.protocol}_{target}_{profile}_ch{idx}_{bg}.log"
            log_f = open(log_path, "w", encoding="utf-8", errors="ignore")
            p = subprocess.Popen(cmd, shell=shell, stdout=log_f, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
            time.sleep(0.25)
            if p.poll() is not None:
                failures += 1
            self.write_audit(self.protocol, target, profile, f"background_ch{idx}", bg, p.pid, cmd, str(log_path))
            procs.append((p, log_f))
        return procs, failures

    def start_mosh_background_inside_terminal(self, child, target: str, profile: str, bgs: list):
        # Mosh is not multi-channel. This only creates terminal background load for baseline.
        for bg in bgs:
            env = f"W3_TARGET={q(target)} W3_PROTOCOL=mosh W3_PROFILE={q(profile)}"
            if bg in ("log", "ping", "sysmon"):
                cmd = f"{env} bash {q(self.remote)} {bg} >/tmp/w3_mosh_{target}_{profile}_{bg}.log 2>&1 &"
            elif bg == "output_heavy":
                cmd = f"{env} bash {q(self.remote)} output heavy &"
            else:
                cmd = f"{env} bash {q(self.remote)} output normal &"
            child.sendline(cmd)
            time.sleep(0.2)

    def target_file(self, target: str) -> str:
        run_id = f"{self.protocol}{target}{os.getpid()}{int(time.time())}"
        return f"/tmp/w3latency{run_id}.txt"

    def require_remote_bin(self, child, bin_name: str, target: str):
        ok = f"W3{target.upper()}BINOK"
        missing = f"W3{target.upper()}BINMISS"
        suffix = f"{target.upper()}BIN"
        child.sendline(
            f"if command -v {q(bin_name)} >/dev/null 2>&1; then "
            f"printf W3; printf {suffix}OK; "
            f"else printf W3; printf {suffix}MISS; fi; printf '\\n'"
        )
        idx = child.expect([ok, missing, pexpect.TIMEOUT, pexpect.EOF], timeout=5)
        if idx == 1:
            raise RuntimeError(f"{target} binary not found: {bin_name}")
        if idx == 2:
            raise TimeoutError(f"timeout while checking {target} binary: {bin_name}")
        if idx == 3:
            raise EOFError(f"connection closed while checking {target} binary: {bin_name}")

    def prepare_target(self, child, target: str):
        target = target.lower()
        delay = float(self.cfg.get("EDITOR_START_DELAY", "0.80"))

        if target == "shell":
            child.sendline("printf W3; printf SHELLREADY; printf '\\n'")
            child.expect("W3SHELLREADY", timeout=5)
            return

        if target == "vim":
            vim_bin = self.cfg.get("VIM_BIN", "vim")
            self.require_remote_bin(child, vim_bin, target)
            edit_file = self.target_file(target)
            tmpl = self.cfg.get("VIM_COMMAND", "{vim} -Nu NONE -n -i NONE {file}")
            child.sendline(tmpl.format(vim=q(vim_bin), file=q(edit_file)))
            time.sleep(delay)
            child.send("i")
            self.verify_editor_echo(child, target)
            return

        if target == "nano":
            nano_bin = self.cfg.get("NANO_BIN", "nano")
            self.require_remote_bin(child, nano_bin, target)
            edit_file = self.target_file(target)
            tmpl = self.cfg.get("NANO_COMMAND", "{nano} -t {file}")
            child.sendline(tmpl.format(nano=q(nano_bin), file=q(edit_file)))
            time.sleep(delay)
            self.verify_editor_echo(child, target)
            return

        raise ValueError(f"unknown target: {target}")

    def verify_editor_echo(self, child, target: str):
        ready = f"W3{target.upper()}READY"
        child.send(ready)
        child.expect(ready, timeout=5)

    def cleanup_target(self, child, target: str):
        target = target.lower()
        try:
            if target == "shell":
                child.sendcontrol("c")
            elif target == "vim":
                child.sendcontrol("c")
                time.sleep(0.1)
                child.sendline(":qa!")
            elif target == "nano":
                child.sendcontrol("x")
            time.sleep(0.3)
        except Exception:
            pass

    def stop_procs(self, procs):
        for p, f in procs:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
            try:
                f.close()
            except Exception:
                pass


class ProbeSource:
    def __init__(self, sequence: str):
        if not sequence:
            raise ValueError("PROBE_SEQUENCE must not be empty")
        bad = [ch for ch in sequence if not ch.isalnum()]
        if bad:
            raise ValueError("PROBE_SEQUENCE must contain alphanumeric characters only")
        self.sequence = sequence
        self.index = 0

    def next(self) -> str:
        ch = self.sequence[self.index]
        self.index = (self.index + 1) % len(self.sequence)
        return ch


def drain_pending_output(child, max_reads: int = 8):
    for _ in range(max_reads):
        try:
            child.read_nonblocking(size=4096, timeout=0)
        except (pexpect.TIMEOUT, pexpect.EOF):
            break


def consume_stray_probe(child, probe: str, search_window, max_polls: int = 4):
    for _ in range(max_polls):
        idx = child.expect_exact(
            [probe, pexpect.TIMEOUT, pexpect.EOF],
            timeout=0,
            searchwindowsize=search_window,
        )
        if idx == 0:
            continue
        if idx == 1:
            return
        raise pexpect.EOF("EOF while draining stale probe bytes")


def probe_once_ms(child, probe: str, timeout: float, search_window):
    drain_pending_output(child)
    consume_stray_probe(child, probe, search_window)
    start_ns = time.perf_counter_ns()
    child.send(probe)
    child.expect_exact(
        probe,
        timeout=timeout,
        searchwindowsize=search_window,
    )
    end_ns = time.perf_counter_ns()
    return (end_ns - start_ns) / 1_000_000.0


def write_unavailable_rows(
    cfg: dict,
    protocol: str,
    target: str,
    profile: str,
    bgs: list,
    bg_failures: int,
    writer,
    note: str,
):
    runs = int(cfg.get("RUNS", "100"))
    for i in range(runs):
        writer.writerow({
            "ts": time.time(),
            "protocol": protocol,
            "target": target,
            "profile": profile,
            "sample_idx": i,
            "token": "",
            "status": "target_unavailable",
            "latency_ms": "",
            "stall": 0,
            "background_channels": "+".join(bgs),
            "channel_count": 1 + len(bgs),
            "channel_open_failures": bg_failures,
            "note": note,
        })


def run_trial(cfg: dict, protocol: str, target: str, profile: str, bgs: list, writer):
    runs = int(cfg.get("RUNS", "100"))
    interval = float(cfg.get("TOKEN_INTERVAL", "0.20"))
    timeout = float(cfg.get("TOKEN_TIMEOUT", "2.00"))
    stall_ms = float(cfg.get("STALL_THRESHOLD_MS", "1000"))
    live_progress = cfg.get("LIVE_PROGRESS", "1") == "1"
    probe_source = ProbeSource(cfg.get("PROBE_SEQUENCE", DEFAULT_PROBE_SEQUENCE).strip())
    search_window_cfg = int(cfg.get("PROBE_SEARCH_WINDOW", "0"))
    search_window = None if search_window_cfg == 0 else max(8, search_window_cfg)

    pr = ProtocolRunner(cfg, protocol)
    pr.start_master_if_needed()
    bg_procs = []
    bg_failures = 0
    child = None

    try:
        child = pr.spawn_interactive(target, profile)
        if protocol == "mosh":
            pr.start_mosh_background_inside_terminal(child, target, profile, bgs)
        else:
            bg_procs, bg_failures = pr.start_background_channels(target, profile, bgs)

        try:
            pr.prepare_target(child, target)
            pr.write_connection_summary(target, profile, child.pid, bg_procs)
        except Exception as e:
            note = repr(e)
            write_unavailable_rows(cfg, protocol, target, profile, bgs, bg_failures, writer, note)
            if live_progress:
                print(
                    f"[LIVE] {protocol:5s} {target:5s} {profile:12s} "
                    f"status=target_unavailable note={note}",
                    flush=True,
                )
            return

        for i in range(runs):
            probe = probe_source.next()
            status = "unknown"
            latency_ms = ""
            stall = 0
            note = ""

            try:
                dt = probe_once_ms(child, probe, timeout, search_window)
                latency_ms = f"{dt:.3f}"
                stall = int(dt > stall_ms)
                status = "success"
            except pexpect.TIMEOUT:
                status = "timeout"
            except pexpect.EOF:
                status = "eof"
            except Exception as e:
                status = "failure"
                note = repr(e)

            row = {
                "ts": time.time(),
                "protocol": protocol,
                "target": target,
                "profile": profile,
                "sample_idx": i,
                "token": probe,
                "status": status,
                "latency_ms": latency_ms,
                "stall": stall,
                "background_channels": "+".join(bgs),
                "channel_count": 1 + len(bgs),
                "channel_open_failures": bg_failures,
                "note": note,
            }
            writer.writerow(row)

            if live_progress:
                shown_latency = latency_ms if latency_ms != "" else "-"
                print(
                    f"[LIVE] {protocol:5s} {target:5s} {profile:12s} "
                    f"sample={i+1}/{runs} status={status:14s} "
                    f"latency_ms={shown_latency} stall={stall}",
                    flush=True,
                )

            time.sleep(interval)

    finally:
        if child is not None:
            pr.cleanup_target(child, target)
        pr.stop_procs(bg_procs)
        if child is not None:
            try:
                child.sendline("pkill -f w3_remote_workloads.sh || true")
                child.sendline("exit")
                child.close(force=True)
                logfile = getattr(child, "logfile_read", None)
                if logfile is not None and logfile is not sys.stdout:
                    logfile.close()
            except Exception:
                pass
        pr.write_stream_audit(target, profile)
        pr.stop_master_if_needed()


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    cfg = load_env(cfg_path)
    result_dir = Path(cfg.get("RESULT_DIR", "results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    out_path = result_dir / "samples.csv"

    protocols = split_csv(cfg.get("PROTOCOLS", "ssh,ssh3,mosh"))
    targets = split_csv(cfg.get("TARGETS", DEFAULT_TARGETS))

    fields = [
        "ts", "protocol", "target", "profile", "sample_idx", "token", "status", "latency_ms",
        "stall", "background_channels", "channel_count", "channel_open_failures", "note"
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for protocol in protocols:
            for target in targets:
                for profile, bgs in PROFILES.items():
                    print(
                        f"[RUN] protocol={protocol} target={target} "
                        f"profile={profile} channels={1 + len(bgs)}"
                    )
                    run_trial(cfg, protocol, target, profile, bgs, writer)
                    f.flush()

    print(f"Saved samples to {out_path}")


if __name__ == "__main__":
    main()
