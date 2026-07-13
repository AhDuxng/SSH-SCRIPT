#!/usr/bin/env python3
import csv
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
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


def make_token(sample_idx: int) -> str:
    # Alphanumeric only: safe to type into shell, Vim insert mode, and Nano.
    return f"W3TOK{uuid.uuid4().hex[:12].upper()}{sample_idx:06d}"


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
        port_id = self.port or "default"
        self.cm_path = f"/tmp/w3_cm_{self.user}_{self.host}_{port_id}"

    def ssh_port_args(self) -> list:
        return ["-p", self.port] if self.port else []

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

    def interactive_cmd(self):
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
            tmpl = self.cfg.get("SSH3_INTERACTIVE_TEMPLATE", "ssh3 {user}@{host}")
            cmd = tmpl.format(user=self.user, host=self.host, port=self.port)
            return cmd, True

        if self.protocol == "mosh":
            mosh = self.cfg.get("MOSH_BIN", "mosh")
            return [mosh, f"{self.user}@{self.host}", "--", "bash", "--noprofile", "--norc", "-i"], False

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
            tmpl = self.cfg.get("SSH3_COMMAND_TEMPLATE", "ssh3 {user}@{host} -- {cmd}")
            cmd = tmpl.format(user=self.user, host=self.host, port=self.port, cmd=q(remote_cmd))
            return cmd, True

        raise ValueError("mosh has no independent background session channels")

    def spawn_interactive(self):
        cmd, shell = self.interactive_cmd()
        if shell:
            child = pexpect.spawn("/bin/bash", ["-lc", cmd], encoding="utf-8", timeout=5)
        else:
            child = pexpect.spawn(cmd[0], cmd[1:], encoding="utf-8", timeout=5)
        child.delaybeforesend = 0
        if self.cfg.get("SHOW_TERMINAL_OUTPUT", "0") == "1":
            child.logfile_read = sys.stdout
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

    pr = ProtocolRunner(cfg, protocol)
    pr.start_master_if_needed()
    bg_procs = []
    bg_failures = 0
    child = None

    try:
        child = pr.spawn_interactive()
        if protocol == "mosh":
            pr.start_mosh_background_inside_terminal(child, target, profile, bgs)
        else:
            bg_procs, bg_failures = pr.start_background_channels(target, profile, bgs)

        try:
            pr.prepare_target(child, target)
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
            token = make_token(i)
            t0 = time.perf_counter()
            status = "unknown"
            latency_ms = ""
            stall = 0
            note = ""

            try:
                child.send(token)
                idx = child.expect([re.escape(token), pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
                if idx == 0:
                    dt = (time.perf_counter() - t0) * 1000.0
                    latency_ms = f"{dt:.3f}"
                    stall = int(dt > stall_ms)
                    status = "success"
                elif idx == 1:
                    status = "timeout"
                else:
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
                "token": token,
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
            except Exception:
                pass
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
