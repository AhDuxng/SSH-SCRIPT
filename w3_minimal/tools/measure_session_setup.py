#!/usr/bin/env python3
import csv
import os
import random
import re
import shlex
import sys
import time
from pathlib import Path

import pexpect


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from config import bool_cfg, load_env, split_args, split_csv


PROTOCOLS = ("ssh", "ssh3", "mosh")
ANSI_SEQ = r"(?:\x1b\[\??[0-9;]*[a-zA-Z])"
INITIAL_PROMPT_RE = re.compile(r"[#$>](?:" + ANSI_SEQ + r"|\s)*\s*$", re.MULTILINE)
FIELDS = [
    "run_id", "block_id", "trial_order", "trial_id", "protocol",
    "status", "session_setup_ms", "note",
]


# Tao cac tuy chon SSH dung chung cho SSH va bootstrap cua Mosh.
def ssh_common(cfg, include_tty=False):
    cmd = [cfg.get("SSH_BIN", "ssh")]
    if include_tty:
        cmd.append("-tt")
    cmd.extend(["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"])
    if bool_cfg(cfg, "SETUP_BATCH_MODE", "1"):
        cmd.extend(["-o", "BatchMode=yes"])
    identity = cfg.get("SSH_IDENTITY_FILE", "").strip()
    if identity:
        cmd.extend(["-i", os.path.expanduser(identity)])
    port = cfg.get("SERVER_PORT", "").strip()
    if port:
        cmd.extend(["-p", port])
    return cmd


# Tao lenh mo mot phien moi, khong dung ControlMaster hay tai nen.
def session_command(cfg, protocol):
    target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
    if protocol == "ssh":
        return [*ssh_common(cfg, include_tty=True), target]

    if protocol == "ssh3":
        cmd = [cfg.get("SSH3_BIN", "ssh3")]
        if bool_cfg(cfg, "SSH3_INSECURE", "0"):
            cmd.append("-insecure")
        identity = cfg.get("SSH3_PRIVKEY", "").strip()
        if identity:
            cmd.extend(["-privkey", os.path.expanduser(identity)])
        cmd.extend(split_args(cfg.get("SSH3_EXTRA_ARGS", "")))
        port = cfg.get("SSH3_PORT", "443").strip()
        path = cfg.get("SSH3_PATH", "/ssh3-term").strip()
        cmd.append(f"{target}:{port}{path}")
        return cmd

    if protocol == "mosh":
        bootstrap = shlex.join(ssh_common(cfg, include_tty=False))
        cmd = [cfg.get("MOSH_BIN", "mosh"), f"--ssh={bootstrap}"]
        predict = cfg.get("MOSH_PREDICT", "").strip()
        if predict:
            cmd.extend(["--predict", predict])
        cmd.extend(split_args(cfg.get("MOSH_EXTRA_ARGS", "")))
        cmd.append(target)
        return cmd

    raise ValueError(f"unsupported protocol: {protocol}")


# Dong phien sau moi mau setup.
def close_session(child):
    try:
        child.sendline("exit")
        child.expect(pexpect.EOF, timeout=3)
    except Exception:
        child.close(force=True)


# Do dung logic test-w1: sau spawn den shell prompt dau tien.
def measure_once(cfg, protocol, timeout):
    command = session_command(cfg, protocol)
    child = pexpect.spawn(
        command[0], command[1:], encoding="utf-8", codec_errors="ignore",
        timeout=timeout, env={**os.environ, "TERM": cfg.get("TERMINAL_TYPE", "xterm-256color")},
    )
    child.setwinsize(50, 200)
    try:
        start_ns = time.perf_counter_ns()
        child.expect(INITIAL_PROMPT_RE, timeout=timeout)
        return (time.perf_counter_ns() - start_ns) / 1_000_000.0
    finally:
        close_session(child)


# Tao thu tu complete-block ngau nhien de giam sai lech theo thoi gian.
def build_schedule(protocols, trials, seed):
    rng = random.Random(seed)
    schedule = []
    order = 0
    for block in range(1, trials + 1):
        block_protocols = list(protocols)
        rng.shuffle(block_protocols)
        for protocol in block_protocols:
            order += 1
            schedule.append((block, order, protocol))
    return schedule


# Chay cac phien doc lap va ghi tung mau setup vao CSV.
def run(cfg):
    protocols = split_csv(cfg.get("SETUP_PROTOCOLS", cfg.get("PROTOCOLS", "ssh,ssh3,mosh")))
    unknown = sorted(set(protocols) - set(PROTOCOLS))
    if unknown:
        raise ValueError(f"unknown setup protocols: {unknown}")
    trials = int(cfg.get("SETUP_TRIALS", cfg.get("TRIALS_PER_COMBINATION", "5")))
    timeout = float(cfg.get("SETUP_TIMEOUT", "20"))
    cooldown = float(cfg.get("SETUP_COOLDOWN_SECONDS", cfg.get("INTER_TRIAL_DELAY_SECONDS", "3")))
    seed = int(cfg.get("RANDOM_SEED", "20260724"))
    run_id = cfg.get("RUN_ID", "").strip() or time.strftime("%Y%m%dT%H%M%S")
    result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
    result_dir.mkdir(parents=True, exist_ok=True)
    samples_path = result_dir / "setup_samples.csv"
    summary_path = result_dir / "setup_summary.csv"
    schedule = build_schedule(protocols, trials, seed)

    with samples_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for index, (block, order, protocol) in enumerate(schedule):
            trial_id = f"setup_{protocol}_r{block:02d}"
            print(f"[SETUP] order={order:03d}/{len(schedule):03d} trial={trial_id}", flush=True)
            status, latency, note = "success", "", ""
            try:
                latency = f"{measure_once(cfg, protocol, timeout):.3f}"
            except pexpect.TIMEOUT as exc:
                status, note = "timeout", str(exc)
            except pexpect.EOF as exc:
                status, note = "eof", str(exc)
            except Exception as exc:
                status, note = "failure", repr(exc)
            writer.writerow({
                "run_id": run_id, "block_id": block, "trial_order": order,
                "trial_id": trial_id, "protocol": protocol, "status": status,
                "session_setup_ms": latency, "note": note,
            })
            handle.flush()
            print(f"[SETUP-LIVE] trial={trial_id} status={status} setup_ms={latency or '-'}", flush=True)
            if cooldown > 0 and index + 1 < len(schedule):
                time.sleep(cooldown)

    from analyze_setup import load_setup_samples, summarize_setup, write_summary

    write_summary(summary_path, summarize_setup(load_setup_samples(samples_path)))
    print(f"Saved session setup samples to {samples_path}")
    print(f"Saved session setup summary to {summary_path}")


# Doc config va khoi chay benchmark setup doc lap.
def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.env"
    run(load_env(config_path))


if __name__ == "__main__":
    main()
