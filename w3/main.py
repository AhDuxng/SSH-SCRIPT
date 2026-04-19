#!/usr/bin/env python3
"""Research-oriented remote terminal benchmark — CLI entry point.

Run from the directory containing this file:
    python3 main.py --host 192.168.8.102 --user trungnt --preflight ...

All benchmark logic lives in benchmark.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmark import Benchmark
from constants import DEFAULT_METRICS, DEFAULT_PROMPT, DEFAULT_PROTOCOLS, DEFAULT_SSH3_PATH


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Terminal protocol benchmark: SSHv2 vs SSHv3 vs Mosh",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument("--host",          default="192.168.8.102")
    p.add_argument("--user",          default="trungnt")
    p.add_argument("--source-ip",     default=None,
                   help="Bind SSH/Mosh client to this source IP")
    p.add_argument("--identity-file", default=str(Path.home() / ".ssh" / "id_rsa"))

    p.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS,
                   choices=DEFAULT_PROTOCOLS)
    p.add_argument("--metrics",   nargs="+", default=DEFAULT_METRICS,
                   choices=DEFAULT_METRICS)
    p.add_argument("--remote-setup", default="~/w3_tmux_setup.sh",
                   help="Path to w3_tmux_setup.sh on remote host")

    p.add_argument("--trials",            type=int, default=30,
                   help="Independent sessions per protocol (>=30 for reliable CI)")
    p.add_argument("--samples-per-trial", type=int, default=100,
                   help="Per-sample measurements per trial (after warmup); applies to both line_echo and keystroke_latency")
    p.add_argument("--warmup-samples",    type=int, default=10,
                   help="Samples excluded from statistics")

    p.add_argument("--timeout", type=int, default=45,
                   help="pexpect per-operation timeout (s); 45 recommended "
                        "to capture tail latency rather than recording failures")
    p.add_argument("--echo-timeout", type=int, default=20,
                   help="Timeout for one line_echo/keystroke ACK wait (seconds)")

    p.add_argument("--pty-cols", type=int, default=220,
                   help="PTY width; wide enough that tokens never wrap")
    p.add_argument("--pty-rows", type=int, default=50)

    p.add_argument("--seed",       type=int, default=42)
    p.add_argument("--output-dir", default="results")
    p.add_argument("--prompt",     default=DEFAULT_PROMPT)

    p.add_argument("--ssh3-path",               default=DEFAULT_SSH3_PATH)
    p.add_argument("--ssh3-insecure",           action="store_true",
                   help="Skip TLS certificate verification for SSH3")
    p.add_argument("--ssh3-trust-on-first-use", action="store_true",
                   help="Auto-accept SSH3 certificate trust prompt")

    p.add_argument("--batch-mode",               action="store_true")
    p.add_argument("--strict-host-key-checking", action="store_true")
    p.add_argument("--mosh-predict", default="never",
                   choices=["adaptive", "always", "never"],
                   help="'never' disables Mosh local-prediction for fair comparison")

    p.add_argument("--preflight",         action="store_true",
                   help="Verify connectivity and python3 before measuring")
    p.add_argument("--shuffle-protocols", action="store_true",
                   help="Randomise protocol order to reduce order bias")
    p.add_argument("--reopen-on-failure", action="store_true",
                   help="On sample failure, skip to next trial rather than aborting")
    p.add_argument("--log-pexpect",       action="store_true",
                   help="Write raw PTY stream to pexpect_<protocol>.log (debug)")
    return p


def main() -> int:
    args = build_parser().parse_args()

    for flag, val in [
        ("--trials",            args.trials),
        ("--samples-per-trial", args.samples_per_trial),
        ("--pty-cols",          args.pty_cols),
        ("--pty-rows",          args.pty_rows),
        ("--timeout",           args.timeout),
        ("--echo-timeout",      args.echo_timeout),
    ]:
        if val <= 0:
            build_parser().error(f"{flag} must be > 0")
    if args.warmup_samples < 0:
        build_parser().error("--warmup-samples must be >= 0")

    bench = Benchmark(args)
    bench.run()
    bench.print_report()
    bench.export()
    return 0


if __name__ == "__main__":
    sys.exit(main())