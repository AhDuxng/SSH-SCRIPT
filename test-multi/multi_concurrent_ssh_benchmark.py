#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import shlex
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_PROTOCOLS = ["ssh", "ssh3", "mosh"]
DEFAULT_COMMAND = "uname -a"
DEFAULT_SSH3_PATH = "/ssh3-term"


@dataclass
class SampleRecord:
    protocol: str
    request_id: int
    command: str
    scheduled_offset_ms: float
    start_offset_ms: float
    latency_ms: float
    return_code: Optional[int]
    stdout_bytes: int
    stderr_bytes: int
    success: bool
    error_type: str = ""
    error_message: str = ""


@dataclass
class SummaryRow:
    protocol: str
    requests: int
    successes: int
    failures: int
    success_rate_pct: float
    min_ms: Optional[float]
    mean_ms: Optional[float]
    median_ms: Optional[float]
    stdev_ms: Optional[float]
    p95_ms: Optional[float]
    p99_ms: Optional[float]
    max_ms: Optional[float]
    total_wall_ms: float
    achieved_starts_per_sec: float


def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def build_command(args: argparse.Namespace, protocol: str) -> list[str]:
    target = f"{args.user}@{args.host}"

    ssh_common = [
        "ssh",
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "ConnectionAttempts=1",
    ]
    if args.source_ip:
        ssh_common += ["-b", args.source_ip]
    if args.strict_host_key_checking:
        ssh_common += ["-o", "StrictHostKeyChecking=yes"]
    else:
        ssh_common += [
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]
    if args.batch_mode:
        ssh_common += ["-o", "BatchMode=yes"]
    if args.identity_file:
        ssh_common += ["-i", args.identity_file]
    if args.ssh_option:
        for option in args.ssh_option:
            ssh_common += ["-o", option]

    if protocol == "ssh":
        return ssh_common + [target, args.command]

    if protocol == "mosh":
        cmd = ["mosh", f"--ssh={shlex.join(ssh_common)}"]
        if args.mosh_predict and args.mosh_predict != "adaptive":
            cmd += ["--predict", args.mosh_predict]
        return cmd + [target, args.command]

    if protocol == "ssh3":
        cmd = ["ssh3"]
        if args.identity_file:
            cmd += ["-privkey", args.identity_file]
        if args.ssh3_insecure:
            cmd += ["-insecure"]
        if args.ssh3_option:
            for option in args.ssh3_option:
                cmd += shlex.split(option)
        return cmd + [f"{target}{args.ssh3_path}", args.command]

    raise ValueError(f"Unsupported protocol: {protocol}")


async def run_one(
    args: argparse.Namespace,
    protocol: str,
    request_id: int,
    benchmark_start: float,
) -> SampleRecord:
    scheduled_offset = (request_id / args.connections) * args.spread_seconds
    scheduled_start = benchmark_start + scheduled_offset
    await asyncio.sleep(max(0.0, scheduled_start - time.perf_counter()))

    command = build_command(args, protocol)
    start = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=args.timeout,
        )
        end = time.perf_counter()
        return SampleRecord(
            protocol=protocol,
            request_id=request_id,
            command=args.command,
            scheduled_offset_ms=scheduled_offset * 1000,
            start_offset_ms=(start - benchmark_start) * 1000,
            latency_ms=(end - start) * 1000,
            return_code=proc.returncode,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
            success=proc.returncode == 0,
            error_type="" if proc.returncode == 0 else "return_code",
            error_message="" if proc.returncode == 0 else str(proc.returncode),
        )
    except asyncio.TimeoutError:
        end = time.perf_counter()
        if "proc" in locals() and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return SampleRecord(
            protocol=protocol,
            request_id=request_id,
            command=args.command,
            scheduled_offset_ms=scheduled_offset * 1000,
            start_offset_ms=(start - benchmark_start) * 1000,
            latency_ms=(end - start) * 1000,
            return_code=None,
            stdout_bytes=0,
            stderr_bytes=0,
            success=False,
            error_type="timeout",
            error_message=f">{args.timeout}s",
        )
    except Exception as exc:
        end = time.perf_counter()
        return SampleRecord(
            protocol=protocol,
            request_id=request_id,
            command=args.command,
            scheduled_offset_ms=scheduled_offset * 1000,
            start_offset_ms=(start - benchmark_start) * 1000,
            latency_ms=(end - start) * 1000,
            return_code=None,
            stdout_bytes=0,
            stderr_bytes=0,
            success=False,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def summarize(
    protocol: str,
    records: list[SampleRecord],
    wall_seconds: float,
    spread_seconds: float,
) -> SummaryRow:
    successes = [record for record in records if record.success]
    latencies = [record.latency_ms for record in successes]
    failures = len(records) - len(successes)
    start_window = max(spread_seconds, 0.001)

    return SummaryRow(
        protocol=protocol,
        requests=len(records),
        successes=len(successes),
        failures=failures,
        success_rate_pct=(len(successes) / len(records) * 100.0) if records else 0.0,
        min_ms=min(latencies) if latencies else None,
        mean_ms=statistics.mean(latencies) if latencies else None,
        median_ms=statistics.median(latencies) if latencies else None,
        stdev_ms=statistics.stdev(latencies) if len(latencies) > 1 else 0.0 if latencies else None,
        p95_ms=percentile(latencies, 95),
        p99_ms=percentile(latencies, 99),
        max_ms=max(latencies) if latencies else None,
        total_wall_ms=wall_seconds * 1000,
        achieved_starts_per_sec=len(records) / start_window,
    )


def write_csv(path: Path, records: list[SampleRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(records[0]).keys()))
        writer.writeheader()
        for record in records:
            writer.writerow(asdict(record))


def write_summary_csv(path: Path, rows: list[SummaryRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


async def run_protocol(args: argparse.Namespace, protocol: str) -> tuple[list[SampleRecord], SummaryRow]:
    print(
        f"=== {protocol}: launching {args.connections} requests over "
        f"{args.spread_seconds:.3f}s ===",
        flush=True,
    )
    start = time.perf_counter() + args.start_delay
    tasks = [
        asyncio.create_task(run_one(args, protocol, request_id, start))
        for request_id in range(args.connections)
    ]
    records = await asyncio.gather(*tasks)
    wall_seconds = time.perf_counter() - start
    return records, summarize(protocol, records, wall_seconds, args.spread_seconds)


async def main_async(args: argparse.Namespace) -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    all_records: list[SampleRecord] = []
    summaries: list[SummaryRow] = []

    for protocol in args.protocols:
        records, summary = await run_protocol(args, protocol)
        all_records.extend(records)
        summaries.append(summary)
        print_summary(summary)

    if all_records:
        write_csv(output_dir / f"multi_connection_samples_{run_id}.csv", all_records)
    if summaries:
        write_summary_csv(output_dir / f"multi_connection_summary_{run_id}.csv", summaries)
    (output_dir / f"multi_connection_summary_{run_id}.json").write_text(
        json.dumps([asdict(row) for row in summaries], indent=2),
        encoding="utf-8",
    )
    print(f"\nResults written to: {output_dir}", flush=True)


def fmt(value: Optional[float]) -> str:
    return "NA" if value is None else f"{value:.2f}"


def print_summary(row: SummaryRow) -> None:
    print(
        f"{row.protocol}: success={row.successes}/{row.requests} "
        f"({row.success_rate_pct:.1f}%), "
        f"mean={fmt(row.mean_ms)}ms, p95={fmt(row.p95_ms)}ms, "
        f"p99={fmt(row.p99_ms)}ms, wall={row.total_wall_ms:.0f}ms",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create 100/500 concurrent SSH, SSH3, or Mosh one-shot command executions "
            "within a 1-second launch window."
        )
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--source-ip", default="")
    parser.add_argument("--identity-file", default="")
    parser.add_argument("--protocols", nargs="+", default=DEFAULT_PROTOCOLS)
    parser.add_argument("--connections", type=int, default=100)
    parser.add_argument("--spread-seconds", type=float, default=1.0)
    parser.add_argument("--start-delay", type=float, default=1.0)
    parser.add_argument("--command", default=DEFAULT_COMMAND)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--output-dir", default="multi_results")
    parser.add_argument("--ssh3-path", default=DEFAULT_SSH3_PATH)
    parser.add_argument("--ssh3-insecure", action="store_true")
    parser.add_argument(
        "--mosh-predict",
        default="always",
        choices=["adaptive", "always", "never"],
        help="Mosh prediction mode.",
    )
    parser.add_argument("--batch-mode", action="store_true")
    parser.add_argument("--strict-host-key-checking", action="store_true")
    parser.add_argument(
        "--ssh-option",
        action="append",
        default=[],
        help="Extra OpenSSH -o option, e.g. MaxAuthTries=1. Can be repeated.",
    )
    parser.add_argument(
        "--ssh3-option",
        action="append",
        default=[],
        help="Extra ssh3 CLI option string. Can be repeated.",
    )
    args = parser.parse_args()

    if args.connections <= 0:
        parser.error("--connections must be > 0")
    if args.spread_seconds <= 0:
        parser.error("--spread-seconds must be > 0")
    unsupported = sorted(set(args.protocols) - {"ssh", "ssh3", "mosh"})
    if unsupported:
        parser.error(f"Unsupported protocols: {', '.join(unsupported)}")
    return args


def main() -> None:
    asyncio.run(main_async(parse_args()))


if __name__ == "__main__":
    main()
