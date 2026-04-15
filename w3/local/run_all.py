#!/usr/bin/env python3
"""
run_all.py — Sequential W3 benchmark runner.

Runs w3_measure.py for each (protocol x metric) combination.
Network scenario is a CSV label only; apply set_network.sh manually on both
client and server *before* invoking this script.

Usage (equivalent to src/w3/main.py):
    python3 run_all.py \\
        --host 192.168.8.102 --user trungnt \\
        --trials 30 --samples-per-trial 100 --warmup-samples 10 \\
        --scenario low
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="W3 benchmark: sequential ssh + ssh3 measurement",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Connection
    parser.add_argument("--host",          required=True)
    parser.add_argument("--user",          default="trungnt")
    parser.add_argument("--identity-file", default=None,
                        help="SSH identity file (-i)")

    # Remote path to w3_tmux_setup.sh
    parser.add_argument("--remote-setup",
                        default="/home/{user}/w3_tmux_setup.sh",
                        help="Absolute path to w3_tmux_setup.sh on server")

    # Benchmark parameters — identical defaults to src/w3/main.py
    parser.add_argument("--trials",            type=int, default=30)
    parser.add_argument("--samples-per-trial", type=int, default=100)
    parser.add_argument("--warmup-samples",    type=int, default=10)
    parser.add_argument("--timeout",           type=int, default=45,
                        help="pexpect per-operation timeout (s)")
    parser.add_argument("--echo-timeout",      type=int, default=45,
                        help="ACK wait timeout per sample (s)")

    # Protocols / metrics
    parser.add_argument("--protocols", nargs="+", default=["ssh", "ssh3"],
                        choices=["ssh", "ssh3"])
    parser.add_argument("--metrics",   nargs="+",
                        default=["keystroke_latency", "line_echo"],
                        choices=["keystroke_latency", "line_echo"])

    # Scenario label (CSV annotation only; apply tc manually before running)
    parser.add_argument("--scenario", default="default",
                        choices=["default", "low", "medium", "high"],
                        help="Network scenario label written to CSV (tc applied manually)")

    parser.add_argument("--output-dir", default="results")

    args = parser.parse_args()

    remote_setup = args.remote_setup.format(user=args.user)
    os.makedirs(args.output_dir, exist_ok=True)

    # Locate sibling scripts (measure + stats live alongside this file)
    base_dir       = os.path.dirname(os.path.abspath(__file__))
    measure_script = os.path.join(base_dir, "w3_measure.py")
    stats_script   = os.path.join(base_dir, "w3_stats.py")

    # Build connection command strings
    ssh_identity = ["-i", args.identity_file] if args.identity_file else []
    ssh_flags    = " ".join([
        "-tt",
        "-o StrictHostKeyChecking=no",
        "-o ControlPath=none",
        *([f"-i {args.identity_file}"] if args.identity_file else []),
    ])
    commands = {
        "ssh":  f"ssh {ssh_flags} {args.user}@{args.host}",
        "ssh3": (
            f"ssh3 -insecure "
            + (f"-privkey {args.identity_file} " if args.identity_file else "")
            + f"{args.user}@{args.host}/ssh3-term"
        ),
    }

    all_results: list[str] = []

    for metric in args.metrics:
        for proto in args.protocols:
            out_csv = os.path.join(
                args.output_dir,
                f"w3_{proto}_{metric}_{args.scenario}.csv",
            )

            print(f"\n{'='*60}")
            print(f"▶ Protocol={proto.upper()} | Metric={metric} | Scenario={args.scenario}")
            print(f"{'='*60}")

            run_cmd = [
                sys.executable, measure_script,
                "--cmd",               commands[proto],
                "--remote-setup",      remote_setup,
                "--trials",            str(args.trials),
                "--samples-per-trial", str(args.samples_per_trial),
                "--warmup-samples",    str(args.warmup_samples),
                "--timeout",           str(args.timeout),
                "--echo-timeout",      str(args.echo_timeout),
                "--metric",            metric,
                "--proto",             proto,
                "--scenario",          args.scenario,
                "--output",            out_csv,
            ]

            try:
                subprocess.run(run_cmd, check=True)
                all_results.append(out_csv)
            except subprocess.CalledProcessError as exc:
                print(f"[!] Measurement failed for {proto}/{metric}: {exc}", flush=True)
            except KeyboardInterrupt:
                print("\n[!] Interrupted by user.", flush=True)
                sys.exit(1)

    # Summary
    print(f"\n{'='*60}")
    print("▶ SUMMARY  (warmup excluded, ddof=1 stdev — identical to src/w3)")
    print(f"{'='*60}")
    for res in all_results:
        if os.path.exists(res):
            print(f"\n--- {os.path.basename(res)} ---")
            subprocess.run([sys.executable, stats_script, res])


if __name__ == "__main__":
    main()
