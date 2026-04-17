#!/usr/bin/env python3
"""
run_all.py — Sequential W3 benchmark runner.

Runs w3_measure.py for each (protocol × metric) combination, then prints
a summary using w3_stats.py.

Network scenario is a CSV label only; apply set_network.sh manually on both
client and server *before* invoking this script.

Usage:
    python3 run_all.py \
        --host 192.168.8.102 --user pi --password raspberry \
        --trials 30 --samples-per-trial 100 --warmup-samples 10 \
        --scenario low

Equivalent short form (single trial, 200 samples, ssh2 + ssh3, line_echo):
    python3 run_all.py \
        --host 192.168.8.102 --user pi --password raspberry \
        --trials 1 --samples 200 --scenario default
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="W3 benchmark: sequential SSH2 + SSH3 measurement",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Connection ────────────────────────────────────────────────────────
    parser.add_argument("--host",          required=True,
                        help="Remote host IP or hostname")
    parser.add_argument("--user",          default="pi",
                        help="Remote username")
    parser.add_argument("--password",      default=None,
                        help="Remote password (passed through to w3_measure.py)")
    parser.add_argument("--identity-file", default=None,
                        help="SSH identity file path (-i)")

    # ── Remote paths ──────────────────────────────────────────────────────
    parser.add_argument("--remote-setup",
                        default="/home/{user}/w3_tmux_setup.sh",
                        help="Absolute path to w3_tmux_setup.sh on server "
                             "(use {user} placeholder)")

    # ── Benchmark parameters ──────────────────────────────────────────────
    parser.add_argument("--trials",            type=int, default=30)
    parser.add_argument("--samples-per-trial", type=int, default=100,
                        help="Measurement samples per trial")
    # Backward-compat alias
    parser.add_argument("--samples",           type=int, default=None,
                        help="Alias for --samples-per-trial")
    parser.add_argument("--warmup-samples",    type=int, default=10)
    parser.add_argument("--timeout",           type=int, default=45,
                        help="pexpect per-operation timeout (s)")
    parser.add_argument("--echo-timeout",      type=int, default=45,
                        help="ACK wait timeout per sample (s)")

    # ── Protocols / metrics ───────────────────────────────────────────────
    parser.add_argument("--protocols", nargs="+", default=["ssh2", "ssh3"],
                        help="Protocol labels to test")
    parser.add_argument("--metrics",   nargs="+",
                        default=["line_echo", "keystroke_latency"],
                        choices=["line_echo", "keystroke_latency"],
                        help="Metrics to measure")

    # ── Scenario ──────────────────────────────────────────────────────────
    parser.add_argument("--scenario", default="default",
                        choices=["default", "low", "medium", "high"],
                        help="Network scenario label (tc applied manually)")

    # ── Output ────────────────────────────────────────────────────────────
    parser.add_argument("--output-dir", default="results",
                        help="Directory for CSV output files")

    args = parser.parse_args()

    # Resolve alias
    if args.samples is not None:
        args.samples_per_trial = args.samples

    remote_setup = args.remote_setup.format(user=args.user)
    os.makedirs(args.output_dir, exist_ok=True)

    # Locate sibling scripts
    base_dir       = os.path.dirname(os.path.abspath(__file__))
    measure_script = os.path.join(base_dir, "w3_measure.py")
    stats_script   = os.path.join(base_dir, "w3_stats.py")

    for p in (measure_script, stats_script):
        if not os.path.isfile(p):
            raise SystemExit(f"[!] Required script not found: {p}")

    # ── Build connection command strings ──────────────────────────────────
    identity_opts = [f"-i {args.identity_file}"] if args.identity_file else []
    ssh_flags = " ".join([
        "-tt",
        "-o StrictHostKeyChecking=no",
        "-o ControlPath=none",
        *identity_opts,
    ])
    commands: dict[str, str] = {
        # ssh2 maps to OpenSSH
        "ssh2": f"ssh {ssh_flags} {args.user}@{args.host}",
        "ssh":  f"ssh {ssh_flags} {args.user}@{args.host}",
        # ssh3 uses its own client binary
        "ssh3": (
            "ssh3 -insecure "
            + (f"-privkey {args.identity_file} " if args.identity_file else "")
            + f"{args.user}@{args.host}/ssh3-term"
        ),
    }

    all_results: list[str] = []

    for metric in args.metrics:
        for proto in args.protocols:
            if proto not in commands:
                print(
                    f"[!] Unknown protocol '{proto}' — no connection command defined. Skipping.",
                    flush=True,
                )
                continue

            out_csv = os.path.join(
                args.output_dir,
                f"w3_{proto}_{metric}_{args.scenario}.csv",
            )

            print(f"\n{'=' * 62}", flush=True)
            print(
                f"▶  Protocol={proto.upper()}  |  Metric={metric}  |  Scenario={args.scenario}",
                flush=True,
            )
            print(f"{'=' * 62}", flush=True)

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
            if args.password:
                run_cmd += ["--password", args.password]

            try:
                subprocess.run(run_cmd, check=True)
                all_results.append(out_csv)
            except subprocess.CalledProcessError as exc:
                print(
                    f"[!] Measurement failed for {proto}/{metric}: {exc}",
                    flush=True,
                )
            except KeyboardInterrupt:
                print("\n[!] Interrupted by user.", flush=True)
                sys.exit(1)

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'=' * 62}", flush=True)
    print("▶  SUMMARY  (warmup excluded · sample stdev ddof=1)", flush=True)
    print(f"{'=' * 62}", flush=True)

    for res in all_results:
        if os.path.exists(res):
            print(f"\n--- {os.path.basename(res)} ---", flush=True)
            subprocess.run([sys.executable, stats_script, res])
        else:
            print(f"\n--- {os.path.basename(res)} [NOT FOUND] ---", flush=True)


if __name__ == "__main__":
    main()