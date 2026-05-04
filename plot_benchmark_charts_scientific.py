#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Iterable, List

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

DISPLAY_NAMES = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
METRIC_LABELS = {
    "session_setup": "Session setup latency (ms)",
    "line_echo": "Application-level RTT (ms)",
}
METRIC_TITLES = {
    "session_setup": "Session setup latency",
    "line_echo": "Interactive application-level RTT",
}

def choose_paths_with_dialogs(args):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.update()

    initial_data_dir = Path.cwd() / "results"
    if not initial_data_dir.exists():
        initial_data_dir = Path.cwd()

    if not args.results_root:
        selected_results_root = filedialog.askdirectory(
            title="Select results root folder (e.g., results/resultsW3)",
            initialdir=str(initial_data_dir),
        )
        if selected_results_root:
            args.results_root = selected_results_root

    def choose_csv(existing_paths, title):
        if existing_paths:
            return existing_paths
        selected = filedialog.askopenfilenames(
            title=title,
            initialdir=str(initial_data_dir),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not selected:
            root.destroy()
            raise SystemExit(f"Selection canceled for: {title}")
        return list(selected)

    if not args.results_root:
        args.samples = choose_csv(args.samples, "Select one or more research_samples.csv")
        args.failures = choose_csv(args.failures, "Select one or more research_failures.csv")
        args.ping = choose_csv(args.ping, "Select one or more research_ping_rtts.csv")

    should_choose_out_dir = args.gui or args.out == "benchmark_figures"
    if should_choose_out_dir:
        selected_out = filedialog.askdirectory(
            title="Select output folder for figures",
            initialdir=str(Path.cwd()),
        )
        if selected_out:
            args.out = selected_out

    root.destroy()
    return args

def resolve_input_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if p.exists():
        return p
    cwd_candidate = Path.cwd() / p
    if cwd_candidate.exists():
        return cwd_candidate
    matches = list(Path.cwd().rglob(p.name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        match_list = "\n".join(f"  - {m}" for m in matches)
        raise FileNotFoundError(
            f"Ambiguous input path '{raw_path}'. Multiple files named '{p.name}' were found:\n"
            f"{match_list}\n"
            "Please pass the full path to the desired file."
        )
    raise FileNotFoundError(f"Input file not found: '{raw_path}'.")

def parse_args():
    p = argparse.ArgumentParser(description="Publication-style benchmark plotting")
    p.add_argument("--samples", nargs="+", help="one or more paths to research_samples.csv")
    p.add_argument("--failures", nargs="+", help="one or more paths to research_failures.csv")
    p.add_argument("--ping", nargs="+", help="one or more paths to research_ping_rtts.csv")
    p.add_argument("--results-root", help="root folder to recursively find benchmark CSV files")
    p.add_argument("--out", default="benchmark_figures", help="output folder")
    p.add_argument("--gui", action="store_true", help="open file dialogs")
    p.add_argument("--formats", nargs="+", default=["png", "pdf", "svg"], choices=["png", "pdf", "svg"])
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--protocol-order", nargs="+", default=["ssh", "ssh3", "mosh"])
    p.add_argument("--title-prefix", default="")
    return p.parse_args()

def find_csvs_in_results_root(results_root: str):
    root = resolve_input_path(results_root)
    if not root.is_dir():
        raise NotADirectoryError(f"--results-root is not a directory: '{root}'")
    samples_paths = sorted(root.rglob("research_samples.csv"))
    failures_paths = sorted(root.rglob("research_failures.csv"))
    ping_paths = sorted(root.rglob("research_ping_rtts.csv"))
    if not samples_paths:
        raise FileNotFoundError(f"No research_samples.csv found under: '{root}'")
    if not failures_paths:
        raise FileNotFoundError(f"No research_failures.csv found under: '{root}'")
    if not ping_paths:
        raise FileNotFoundError(f"No research_ping_rtts.csv found under: '{root}'")
    return samples_paths, failures_paths, ping_paths

def read_and_tag_csv(paths: List[Path], kind: str) -> pd.DataFrame:
    frames = []
    for idx, path in enumerate(paths, start=1):
        df = pd.read_csv(path)
        df["run_id"] = f"run{idx:03d}"
        df["run_path"] = str(path.parent)
        df["source_file"] = str(path)
        df["csv_kind"] = kind
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def normalize_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().map({"true": True, "false": False}).fillna(False)

def setup_matplotlib():
    plt.rcParams.update({
        "figure.figsize": (6.6, 4.2),
        "savefig.dpi": 300,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linestyle": "--",
        "axes.axisbelow": True,
        "axes.labelsize": 11,
        "axes.titlesize": 12,
        "font.size": 10,
        "legend.fontsize": 9,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "lines.linewidth": 1.8,
        "lines.markersize": 5,
        "axes.spines.top": False,
        "axes.spines.right": False,
    })

def protocol_order_present(df: pd.DataFrame, preferred_order: List[str]) -> List[str]:
    present = list(df["protocol"].dropna().astype(str).unique())
    ordered = [p for p in preferred_order if p in present]
    remaining = [p for p in present if p not in ordered]
    return ordered + sorted(remaining)

def save_figure(fig: plt.Figure, out_dir: Path, basename: str, formats: Iterable[str], dpi: int):
    for fmt in formats:
        fig.savefig(out_dir / f"{basename}.{fmt}", dpi=dpi if fmt == "png" else None, bbox_inches="tight")
    plt.close(fig)

def ci95_half_width(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    n = len(s)
    if n <= 1:
        return 0.0
    return 1.96 * float(s.std(ddof=1)) / math.sqrt(n)

def summarize_trial_means(trial_means: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (protocol, metric), group in trial_means.groupby(["protocol", "metric"], dropna=False):
        s = pd.to_numeric(group["trial_mean_ms"], errors="coerce").dropna()
        rows.append({
            "protocol": protocol,
            "metric": metric,
            "n_trials": int(len(s)),
            "mean_trial_mean_ms": float(s.mean()) if len(s) else np.nan,
            "median_trial_mean_ms": float(s.median()) if len(s) else np.nan,
            "std_trial_mean_ms": float(s.std(ddof=1)) if len(s) > 1 else 0.0,
            "ci95_half_width_ms": ci95_half_width(s),
            "min_trial_mean_ms": float(s.min()) if len(s) else np.nan,
            "max_trial_mean_ms": float(s.max()) if len(s) else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["metric", "protocol"]).reset_index(drop=True)

def summarize_failures(failures: pd.DataFrame) -> pd.DataFrame:
    if failures.empty:
        return pd.DataFrame(columns=["protocol", "metric", "n_failures"])
    failures = failures.copy()
    if "is_warmup" in failures.columns:
        failures["is_warmup"] = normalize_bool(failures["is_warmup"])
        failures = failures.loc[~failures["is_warmup"]].copy()
    return (
        failures.groupby(["protocol", "metric"], dropna=False)
        .size()
        .reset_index(name="n_failures")
        .sort_values(["metric", "protocol"])
        .reset_index(drop=True)
    )

def pretty_protocol(protocol: str) -> str:
    return DISPLAY_NAMES.get(protocol, protocol)

def pretty_title(prefix: str, metric: str) -> str:
    base = METRIC_TITLES.get(metric, metric)
    return f"{prefix}{base}" if prefix else base

def make_trial_mean_boxplot(trial_means, metric, protocols, out_dir, formats, dpi, title_prefix):
    df = trial_means.loc[trial_means["metric"] == metric].copy()
    if df.empty:
        return
    data = [df.loc[df["protocol"] == p, "trial_mean_ms"].dropna().values for p in protocols]
    labels = [pretty_protocol(p) for p in protocols]
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    ax.boxplot(
        data,
        tick_labels=labels,
        widths=0.55,
        showfliers=True,
        patch_artist=False,
        medianprops={"linewidth": 2.0},
        whiskerprops={"linewidth": 1.2},
        capprops={"linewidth": 1.2},
        boxprops={"linewidth": 1.2},
    )
    ax.set_title(pretty_title(title_prefix, metric))
    ax.set_xlabel("Protocol")
    ax.set_ylabel(METRIC_LABELS.get(metric, "Latency (ms)"))
    for i, arr in enumerate(data, start=1):
        if len(arr):
            ax.text(i, float(np.nanmax(arr)), f"n={len(arr)}", ha="center", va="bottom", fontsize=9)
    save_figure(fig, out_dir, f"fig_boxplot_trial_means_{metric}", formats, dpi)

def make_trial_mean_ci_plot(trial_means, metric, protocols, out_dir, formats, dpi, title_prefix):
    df = trial_means.loc[trial_means["metric"] == metric].copy()
    if df.empty:
        return
    stats = (
        df.groupby("protocol", dropna=False)["trial_mean_ms"]
        .agg(["mean", "std", "count"])
        .reindex(protocols)
        .dropna(how="all")
    )
    if stats.empty:
        return
    x = np.arange(len(stats))
    means = stats["mean"].to_numpy(dtype=float)
    ci95 = np.where(
        stats["count"].to_numpy(dtype=float) > 1,
        1.96 * stats["std"].to_numpy(dtype=float) / np.sqrt(stats["count"].to_numpy(dtype=float)),
        0.0,
    )
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    ax.errorbar(x, means, yerr=ci95, fmt="o", capsize=5)
    ax.set_xticks(x, [pretty_protocol(p) for p in stats.index.tolist()])
    ax.set_xlabel("Protocol")
    ax.set_ylabel(METRIC_LABELS.get(metric, "Latency (ms)"))
    ax.set_title(pretty_title(title_prefix, metric) + " (trial means ±95% CI)")
    for xi, mean_val, ci_val, count_val in zip(x, means, ci95, stats["count"].astype(int).tolist()):
        ax.annotate(f"{mean_val:.1f} ± {ci_val:.1f}\n(n={count_val})", (xi, mean_val), textcoords="offset points", xytext=(0, 10), ha="center", fontsize=8)
    save_figure(fig, out_dir, f"fig_mean_ci_{metric}", formats, dpi)

def make_raw_ecdf(samples, metric, protocols, out_dir, formats, dpi, title_prefix):
    df = samples.loc[samples["metric"] == metric].copy()
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    for p in protocols:
        s = np.sort(df.loc[df["protocol"] == p, "latency_ms"].dropna().to_numpy())
        if len(s) == 0:
            continue
        y = np.arange(1, len(s) + 1) / len(s)
        ax.plot(s, y, label=pretty_protocol(p))
    ax.set_title(pretty_title(title_prefix, metric) + " (ECDF)")
    ax.set_xlabel(METRIC_LABELS.get(metric, "Latency (ms)"))
    ax.set_ylabel("Empirical cumulative probability")
    ax.legend(frameon=False)
    save_figure(fig, out_dir, f"fig_ecdf_raw_{metric}", formats, dpi)

def make_ping_timeseries(ping, protocols, out_dir, formats, dpi, title_prefix):
    df = ping.dropna(subset=["ping_rtt_ms"]).copy()
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    for p in protocols:
        g = df.loc[df["protocol"] == p].sort_values(["run_id", "trial_id"]).copy()
        if g.empty:
            continue
        x = np.arange(1, len(g) + 1)
        ax.plot(x, g["ping_rtt_ms"].to_numpy(dtype=float), marker="o", label=pretty_protocol(p))
    title = "Ping RTT across trials"
    if title_prefix:
        title = f"{title_prefix}{title}"
    ax.set_title(title)
    ax.set_xlabel("Trial index across loaded runs")
    ax.set_ylabel("Ping RTT (ms)")
    ax.legend(frameon=False)
    save_figure(fig, out_dir, "fig_ping_timeseries", formats, dpi)

def make_ping_boxplot(ping, protocols, out_dir, formats, dpi, title_prefix):
    df = ping.dropna(subset=["ping_rtt_ms"]).copy()
    if df.empty:
        return
    data = [df.loc[df["protocol"] == p, "ping_rtt_ms"].dropna().to_numpy() for p in protocols]
    labels = [pretty_protocol(p) for p in protocols]
    fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
    ax.boxplot(
        data,
        tick_labels=labels,
        widths=0.55,
        showfliers=True,
        patch_artist=False,
        medianprops={"linewidth": 2.0},
        whiskerprops={"linewidth": 1.2},
        capprops={"linewidth": 1.2},
        boxprops={"linewidth": 1.2},
    )
    title = "Ping RTT distribution"
    if title_prefix:
        title = f"{title_prefix}{title}"
    ax.set_title(title)
    ax.set_xlabel("Protocol")
    ax.set_ylabel("Ping RTT (ms)")
    save_figure(fig, out_dir, "fig_ping_boxplot", formats, dpi)

def make_failure_barplot(failures_summary, protocols, out_dir, formats, dpi, title_prefix):
    if failures_summary.empty:
        return
    metrics = failures_summary["metric"].dropna().unique().tolist()
    for metric in metrics:
        df = failures_summary.loc[failures_summary["metric"] == metric].copy()
        counts = [int(df.loc[df["protocol"] == p, "n_failures"].sum()) for p in protocols]
        if sum(counts) == 0:
            continue
        fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
        x = np.arange(len(protocols))
        ax.bar(x, counts)
        ax.set_xticks(x, [pretty_protocol(p) for p in protocols])
        ax.set_xlabel("Protocol")
        ax.set_ylabel("Failure count")
        ax.set_title(pretty_title(title_prefix, metric) + " failures")
        for xi, c in zip(x, counts):
            ax.text(xi, c, str(c), ha="center", va="bottom")
        save_figure(fig, out_dir, f"fig_failures_{metric}", formats, dpi)

def main():
    args = parse_args()
    missing_required_inputs = not (args.results_root or (args.samples and args.failures and args.ping))
    if args.gui or missing_required_inputs:
        args = choose_paths_with_dialogs(args)

    if args.results_root:
        samples_paths, failures_paths, ping_paths = find_csvs_in_results_root(args.results_root)
    else:
        samples_paths = [resolve_input_path(p) for p in args.samples]
        failures_paths = [resolve_input_path(p) for p in args.failures]
        ping_paths = [resolve_input_path(p) for p in args.ping]

    samples = read_and_tag_csv(samples_paths, "samples")
    failures = read_and_tag_csv(failures_paths, "failures")
    ping = read_and_tag_csv(ping_paths, "ping")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if "is_warmup" in samples.columns:
        samples["is_warmup"] = normalize_bool(samples["is_warmup"])
        samples = samples.loc[~samples["is_warmup"]].copy()

    samples["latency_ms"] = pd.to_numeric(samples["latency_ms"], errors="coerce")
    ping["ping_rtt_ms"] = pd.to_numeric(ping["ping_rtt_ms"], errors="coerce")

    samples = samples.dropna(subset=["latency_ms", "protocol", "metric", "trial_id"]).copy()
    ping = ping.dropna(subset=["protocol", "trial_id"]).copy()

    protocols = protocol_order_present(samples if not samples.empty else ping, args.protocol_order)

    trial_means = (
        samples.groupby(["run_id", "protocol", "metric", "trial_id"], dropna=False)["latency_ms"]
        .mean()
        .reset_index(name="trial_mean_ms")
    )

    trial_mean_summary = summarize_trial_means(trial_means)
    failures_summary = summarize_failures(failures)
    ping_summary = (
        ping.groupby(["protocol"], dropna=False)["ping_rtt_ms"]
        .agg(["count", "mean", "median", "std", "min", "max"])
        .reset_index()
        if not ping.empty else
        pd.DataFrame(columns=["protocol", "count", "mean", "median", "std", "min", "max"])
    )

    trial_mean_summary.to_csv(out_dir / "trial_mean_summary.csv", index=False)
    failures_summary.to_csv(out_dir / "failure_summary.csv", index=False)
    ping_summary.to_csv(out_dir / "ping_summary.csv", index=False)

    setup_matplotlib()

    metrics = [m for m in ["session_setup", "line_echo"] if m in set(samples["metric"].astype(str))]
    for metric in metrics:
        make_trial_mean_boxplot(trial_means, metric, protocols, out_dir, args.formats, args.dpi, args.title_prefix)
        make_trial_mean_ci_plot(trial_means, metric, protocols, out_dir, args.formats, args.dpi, args.title_prefix)
        make_raw_ecdf(samples, metric, protocols, out_dir, args.formats, args.dpi, args.title_prefix)

    make_ping_boxplot(ping, protocols, out_dir, args.formats, args.dpi, args.title_prefix)
    make_ping_timeseries(ping, protocols, out_dir, args.formats, args.dpi, args.title_prefix)
    make_failure_barplot(failures_summary, protocols, out_dir, args.formats, args.dpi, args.title_prefix)

    readme = out_dir / "README_figures.txt"
    readme.write_text(
        "\n".join([
            "Publication-style benchmark figures generated successfully.",
            "",
            "Main figure types:",
            "- fig_boxplot_trial_means_<metric>",
            "- fig_mean_ci_<metric>",
            "- fig_ecdf_raw_<metric>",
            "- fig_ping_boxplot",
            "- fig_ping_timeseries",
            "- fig_failures_<metric> (only if failures exist)",
            "",
            "Summary CSVs:",
            "- trial_mean_summary.csv",
            "- failure_summary.csv",
            "- ping_summary.csv",
        ]),
        encoding="utf-8",
    )
    print(f"Saved publication-style figures to: {out_dir}")

if __name__ == "__main__":
    main()
