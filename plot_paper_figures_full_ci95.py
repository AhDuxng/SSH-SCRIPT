#!/usr/bin/env python3
"""Generate paper figures with CI95 variation for session setup and recv_pct.

This version computes variation from raw CSV samples whenever available.

Key outputs:
  - session_setup_bar.pdf        (mean ± CI95 from session setup CSVs)
  - recv_pct_lightweight.pdf     (mean ± CI95 from W1 line-log CSVs)
  - recv_pct_heavy.pdf           (mean ± CI95 from W4 line-log CSVs)
  - variation_ci95_summary.csv   (all means, std, n, CI95)

CSV discovery priority:
  1. Environment variables, e.g. W1_LOW_RECV_CSV=/path/file.csv
  2. Current working directory
  3. ROOT project tree
  4. /mnt/data, useful when running inside ChatGPT sandbox

Recommended env vars when filenames are ambiguous:
  W1_DEFAULT_RECV_CSV, W1_LOW_RECV_CSV, W1_MEDIUM_RECV_CSV, W1_HIGH_RECV_CSV
  W4_DEFAULT_RECV_CSV, W4_LOW_RECV_CSV, W4_MEDIUM_RECV_CSV, W4_HIGH_RECV_CSV
  SETUP_DEFAULT_CSV, SETUP_LOW_CSV, SETUP_MEDIUM_CSV, SETUP_HIGH_CSV
"""

import csv
import glob
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = os.environ.get("ROOT", "/home/twan/NETWORK/COMPARE_MOSH_SSH_SSH3/w3/SSH-SCRIPT")
FIGS_DIR = os.environ.get(
    "FIGS_DIR",
    os.path.join(ROOT, "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs"),
)
os.makedirs(FIGS_DIR, exist_ok=True)

SCENARIOS = ["default", "low", "medium", "high"]
SCEN_LABELS = ["", "Low", "Medium", "High"]
PROTOS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "ssh3": "SSH3", "mosh": "Mosh"}
COLORS = {"ssh": "#1f77b4", "mosh": "#2ca02c", "ssh3": "#d62728"}
HATCHES = {"ssh": "////", "mosh": "\\\\\\\\", "ssh3": "////"}
HATCH_COLORS = {"ssh": "#d4f4ff", "mosh": "#c2fac0", "ssh3": "#fab9ba"}
FONT = {"label": 9, "title": 13, "legend": 11, "axis": 12, "tick": 11}

SMALL_W1 = ["df -h", "grep -n root /etc/passwd", "ls"]
SMALL_W4 = ["git status"]
LARGE_W1 = ["ps aux"]
LARGE_W4 = ["find /", "docker logs $(docker ps -q | head -n 1)"]

W1_LIGHTWEIGHT_RECV_FILES = {
    "default": os.path.join(ROOT, "test-w1/w1_results_trungnt/default/w1_line_log.csv"),
    "low": os.path.join(ROOT, "test-w1/w1_results_trungnt/low/w1_line_log.csv"),
    "medium": os.path.join(ROOT, "test-w1/w1_results_trungnt/medium/w1_line_log.csv"),
    "high": os.path.join(ROOT, "test-w1/w1_results_trungnt/high/w1_line_log.csv"),
}

# Fallback means from the paper/table only used when raw CSVs are unavailable.
FALLBACK_RECV = {
    "Lightweight": {
        "low": {"ssh": 99.2, "mosh": 92.2, "ssh3": 99.2},
        "medium": {"ssh": 99.2, "mosh": 90.5, "ssh3": 99.0},
        "high": {"ssh": 99.2, "mosh": 89.1, "ssh3": 99.2},
    },
    "Heavy-output": {
        "low": {"ssh": 96.3, "mosh": 6.3, "ssh3": 96.6},
        "medium": {"ssh": 98.1, "mosh": 2.1, "ssh3": 97.0},
        "high": {"ssh": 96.4, "mosh": 2.5, "ssh3": 95.4},
    },
}

FALLBACK_SETUP_MEAN = {
    "default": {"ssh": 765, "ssh3": 219, "mosh": 908},
    "low": {"ssh": 811, "ssh3": 362, "mosh": 1166},
    "medium": {"ssh": 1796, "ssh3": 645, "mosh": 2366},
    "high": {"ssh": 3565, "ssh3": 1042, "mosh": 3856},
}
FALLBACK_SETUP_STD = {
    "default": {"ssh": 155, "ssh3": 56, "mosh": 163},
    "low": {"ssh": 49, "ssh3": 50, "mosh": 36},
    "medium": {"ssh": 137, "ssh3": 88, "mosh": 283},
    "high": {"ssh": 706, "ssh3": 202, "mosh": 353},
}
FALLBACK_SETUP_N = {"default": 60, "low": 18, "medium": 18, "high": 18}

SCENARIO_ALIASES = {
    "default": "default",
    "vpn": "default",
    "low": "low",
    "medium": "medium",
    "meidum": "medium",
    "high": "high",
}


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ci95(values):
    values = list(values)
    if len(values) < 2:
        return 0.0
    return 1.96 * statistics.stdev(values) / math.sqrt(len(values))


def _stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "mean": 0.0, "std": 0.0, "ci95": 0.0}
    return {
        "n": len(values),
        "mean": statistics.mean(values),
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "ci95": _ci95(values),
    }


def _read_dict_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _relpath(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


def _source_name(path):
    if not path:
        return "missing"
    rel = _relpath(path)
    if not rel.startswith(".."):
        return f"./{rel}"
    return path


def _normalize_scenario(value):
    return SCENARIO_ALIASES.get((value or "").strip().lower())


def _infer_scenario_from_path(path):
    rel = _relpath(path).lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", rel) if t]
    for token in reversed(tokens):
        scenario = _normalize_scenario(token)
        if scenario:
            return scenario
    return None


def _row_scenario(row, path):
    # "unspecified" is not a valid scenario; infer those rows from the path.
    return _normalize_scenario(row.get("scenario")) or _infer_scenario_from_path(path)


def _can_treat_uninferred_as_default(path, kind):
    """Top-level/VPN captures are often written as scenario=unspecified."""
    rel = _relpath(path).replace("\\", "/")
    name = os.path.basename(path)
    if rel == name:
        return True
    if kind in {"w4", "setup"} and "test-w4/w4_results_trungnt/" in rel and "/" not in rel.split("test-w4/w4_results_trungnt/", 1)[1]:
        return True
    return False


def _discover_paths(patterns, env_name=None):
    candidates = []
    env = os.environ.get(env_name or "")
    if env:
        candidates.append(env)
    for pattern in patterns:
        candidates.extend(glob.glob(os.path.join(ROOT, "**", pattern), recursive=True))
        candidates.extend(glob.glob(os.path.join(os.getcwd(), "**", pattern), recursive=True))
    seen = []
    for path in candidates:
        if path and path not in seen:
            seen.append(path)
    return seen


def _valid_line_log(path, metric_col="received_pct"):
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, newline="", encoding="utf-8") as f:
            cols = csv.DictReader(f).fieldnames or []
        return "protocol" in cols and metric_col in cols
    except Exception:
        return False


def _valid_setup(path):
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, newline="", encoding="utf-8") as f:
            cols = csv.DictReader(f).fieldnames or []
        return "protocol" in cols and "session_setup_ms" in cols
    except Exception:
        return False


def _project_priority(path, scenario, kind, env_path=None):
    rel = _relpath(path).replace("\\", "/")
    score = 0
    if env_path:
        try:
            if os.path.samefile(path, env_path):
                score += 10000
        except OSError:
            if path == env_path:
                score += 10000

    if kind == "w1":
        if scenario == "default" and rel == "w1_line_log.csv":
            score += 1200
        if f"test-w1/w1_results_trungnt/w1_results/{scenario}/" in rel:
            score += 920
        if f"test-w1/w1_results/{scenario}/" in rel:
            score += 880
        if f"w1-27-05/{scenario}/" in rel or (scenario == "medium" and "w1-27-05/meidum/" in rel):
            score += 820
        if f"test-w1/w1_results_trungnt/{scenario}/" in rel:
            score += 760
    elif kind == "w4":
        if scenario == "default" and rel == "w4_line_log_patched.csv":
            score += 1200
        if scenario == "default" and "test-w4/w4_results_trungnt/w4_line_log_patched.csv" in rel:
            score += 1190
        if f"w2-27-05/{scenario}/" in rel:
            score += 930
        if f"test-w4/w4_results/{scenario}/" in rel:
            score += 850
        if f"w4/{scenario}/" in rel:
            score += 780
    elif kind == "setup":
        if scenario == "default" and "test-w4/w4_results_trungnt/w4_session_setup.csv" in rel:
            score += 1200
        if f"w2-27-05/{scenario}/" in rel:
            score += 930
        if f"test-w4/w4_results/{scenario}/" in rel:
            score += 850
        if f"w4/{scenario}/" in rel:
            score += 780
        if f"test-w4/w4_results_trungnt/{scenario}/" in rel:
            score += 740

    if f"/{scenario}/" in f"/{rel}/":
        score += 100
    if _infer_scenario_from_path(path) == scenario:
        score += 50
    return score


def _collect_metric_by_protocol(path, scenario, metric_col, kind=None, warmup_filter=False, require_status=True):
    values = {p: [] for p in PROTOS}
    if not path or not os.path.exists(path):
        return values
    rows = _read_dict_rows(path)
    for row in rows:
        if require_status and row.get("status") != "ok":
            continue
        if warmup_filter and (row.get("warmup") or "").strip() == "1":
            continue
        row_sc = _row_scenario(row, path)
        if row_sc is None and scenario == "default" and kind and _can_treat_uninferred_as_default(path, kind):
            row_sc = "default"
        if row_sc != scenario:
            continue
        proto = (row.get("protocol") or "").strip()
        if proto not in PROTOS:
            continue
        value = _safe_float(row.get(metric_col))
        if value is not None:
            values[proto].append(value)
    return values


def _pick_metric_file_for_scenario(scenario, kind, metric_col, patterns, env_name=None, warmup_filter=False, require_status=True):
    env_path = os.environ.get(env_name or "")
    ranked = []
    for path in _discover_paths(patterns, env_name=env_name):
        valid = _valid_setup(path) if metric_col == "session_setup_ms" else _valid_line_log(path, metric_col)
        if not valid:
            continue
        try:
            vals = _collect_metric_by_protocol(
                path,
                scenario,
                metric_col,
                kind=kind,
                warmup_filter=warmup_filter,
                require_status=require_status,
            )
        except Exception:
            continue
        proto_count = sum(1 for pr in PROTOS if vals[pr])
        if proto_count == 0:
            continue
        min_n = min((len(vals[pr]) for pr in PROTOS if vals[pr]), default=0)
        total_n = sum(len(vals[pr]) for pr in PROTOS)
        mtime = os.path.getmtime(path)
        priority = _project_priority(path, scenario, kind, env_path=env_path)
        env_hit = 1 if env_path and os.path.abspath(path) == os.path.abspath(env_path) else 0
        ranked.append(((env_hit, proto_count, priority, min_n, total_n, mtime), path, vals))
    if not ranked:
        return None, {p: [] for p in PROTOS}
    ranked.sort(key=lambda item: item[0], reverse=True)
    _, path, vals = ranked[0]
    return path, vals


def collect_recv_pct_all():
    """Return recv_pct means, CI95 and detailed stats for both recv figures."""
    means = {"Lightweight": {}, "Heavy-output": {}}
    ci95 = {"Lightweight": {}, "Heavy-output": {}}
    detail = []

    # W1/lightweight: use the four scenario CSVs selected for this figure.
    for sc in SCENARIOS:
        p = W1_LIGHTWEIGHT_RECV_FILES[sc]
        vals = _collect_metric_by_protocol(
            p,
            sc,
            "received_pct",
            kind="w1",
            warmup_filter=True,
            require_status=True,
        )
        means["Lightweight"][sc] = {}
        ci95["Lightweight"][sc] = {}
        for pr in PROTOS:
            st = _stats(vals[pr])
            source = _source_name(p)
            if st["n"] == 0 and sc in FALLBACK_RECV["Lightweight"]:
                print(f"[warn] fallback used for recv_pct_lightweight/{sc}/{pr}")
                st = {"n": 0, "mean": FALLBACK_RECV["Lightweight"][sc][pr], "std": 0.0, "ci95": 0.0}
                source = "fallback"
            means["Lightweight"][sc][pr] = st["mean"]
            ci95["Lightweight"][sc][pr] = st["ci95"]
            detail.append(["recv_pct_lightweight", sc, pr, st["n"], st["mean"], st["std"], st["ci95"], source])
        print(
            f"[info] W1/lightweight {sc} from {_source_name(p)}: "
            + ", ".join(
                f"{PROTO_LABELS[pr]}={means['Lightweight'][sc][pr]:.2f}±{ci95['Lightweight'][sc][pr]:.2f}% n={next(d[3] for d in reversed(detail) if d[0] == 'recv_pct_lightweight' and d[1] == sc and d[2] == pr)}"
                for pr in PROTOS
            )
        )

    # W4/heavy-output: default from patched VPN CSV, controlled scenarios from W4 scenario CSVs.
    for sc in SCENARIOS:
        p, vals = _pick_metric_file_for_scenario(
            sc,
            "w4",
            "received_pct",
            ["w4_line_log*.csv", "w4_line_log_patched*.csv"],
            env_name=f"W4_{sc.upper()}_RECV_CSV",
            warmup_filter=False,
            require_status=True,
        )
        means["Heavy-output"][sc] = {}
        ci95["Heavy-output"][sc] = {}
        for pr in PROTOS:
            st = _stats(vals[pr])
            source = _source_name(p)
            if st["n"] == 0 and sc in FALLBACK_RECV["Heavy-output"]:
                print(f"[warn] fallback used for recv_pct_heavy/{sc}/{pr}")
                st = {"n": 0, "mean": FALLBACK_RECV["Heavy-output"][sc][pr], "std": 0.0, "ci95": 0.0}
                source = "fallback"
            means["Heavy-output"][sc][pr] = st["mean"]
            ci95["Heavy-output"][sc][pr] = st["ci95"]
            detail.append(["recv_pct_heavy", sc, pr, st["n"], st["mean"], st["std"], st["ci95"], source])
        print(
            f"[info] W4/heavy {sc} from {_source_name(p)}: "
            + ", ".join(
                f"{PROTO_LABELS[pr]}={means['Heavy-output'][sc][pr]:.2f}±{ci95['Heavy-output'][sc][pr]:.2f}% n={next(d[3] for d in reversed(detail) if d[0] == 'recv_pct_heavy' and d[1] == sc and d[2] == pr)}"
                for pr in PROTOS
            )
        )

    return means, ci95, detail


def collect_session_setup_all():
    means = {}
    ci95 = {}
    detail = []
    for sc in SCENARIOS:
        p, vals = _pick_metric_file_for_scenario(
            sc,
            "setup",
            "session_setup_ms",
            ["w4_session_setup*.csv"],
            env_name=f"SETUP_{sc.upper()}_CSV",
            warmup_filter=False,
            require_status=False,
        )
        means[sc] = {}
        ci95[sc] = {}
        for pr in PROTOS:
            st = _stats(vals[pr])
            source = _source_name(p)
            if st["n"] == 0:
                print(f"[warn] fallback used for session_setup_bar/{sc}/{pr}")
                fallback_ci = 1.96 * FALLBACK_SETUP_STD[sc][pr] / math.sqrt(FALLBACK_SETUP_N[sc])
                st = {
                    "n": FALLBACK_SETUP_N[sc],
                    "mean": FALLBACK_SETUP_MEAN[sc][pr],
                    "std": FALLBACK_SETUP_STD[sc][pr],
                    "ci95": fallback_ci,
                }
                source = "fallback"
            means[sc][pr] = st["mean"]
            ci95[sc][pr] = st["ci95"]
            detail.append(["session_setup_bar", sc, pr, st["n"], st["mean"], st["std"], st["ci95"], source])
        print(
            f"[info] setup {sc} from {_source_name(p)}: "
            + ", ".join(
                f"{PROTO_LABELS[pr]}={means[sc][pr]:.2f}±{ci95[sc][pr]:.2f} ms n={next(d[3] for d in reversed(detail) if d[0] == 'session_setup_bar' and d[1] == sc and d[2] == pr)}"
                for pr in PROTOS
            )
        )
    return means, ci95, detail


# --- Existing latency helpers for other figures ---

def read_latency_csv(path, warmup_filter=False):
    data = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "ok":
                continue
            if warmup_filter and row.get("warmup") == "1":
                continue
            v = _safe_float(row.get("latency_ms"))
            if v is not None:
                data[(row.get("protocol"), row.get("command"))].append(v)
    return data


def collect_w1_w4_groups():
    out = {"small": {sc: {} for sc in SCENARIOS}, "large": {sc: {} for sc in SCENARIOS}}
    cis = {"small": {sc: {} for sc in SCENARIOS}, "large": {sc: {} for sc in SCENARIOS}}
    for sc in SCENARIOS:
        try:
            w1 = read_latency_csv(os.path.join(ROOT, "w1", "w1_results", sc, "w1_line_log.csv"), warmup_filter=True)
            w4 = read_latency_csv(os.path.join(ROOT, "w4", sc, "w4_results", "w4_line_log.csv"))
            for p in PROTOS:
                small_lists = [w1[(p, c)] for c in SMALL_W1] + [w4[(p, c)] for c in SMALL_W4]
                large_lists = [w1[(p, c)] for c in LARGE_W1] + [w4[(p, c)] for c in LARGE_W4]
                small_means = [statistics.mean(x) for x in small_lists if x]
                large_means = [statistics.mean(x) for x in large_lists if x]
                out["small"][sc][p] = sum(small_means) / len(small_means) if small_means else 0.0
                out["large"][sc][p] = sum(large_means) / len(large_means) if large_means else 0.0
                small_pool = sum((x for x in small_lists if x), [])
                large_pool = sum((x for x in large_lists if x), [])
                cis["small"][sc][p] = _ci95(small_pool)
                cis["large"][sc][p] = _ci95(large_pool)
        except FileNotFoundError:
            for p in PROTOS:
                out["small"][sc][p] = 0.0
                out["large"][sc][p] = 0.0
                cis["small"][sc][p] = 0.0
                cis["large"][sc][p] = 0.0
    return out, cis


def collect_w3():
    out = {"1-pane": {sc: {} for sc in SCENARIOS}, "5-pane": {sc: {} for sc in SCENARIOS}}
    cis = {"1-pane": {sc: {} for sc in SCENARIOS}, "5-pane": {sc: {} for sc in SCENARIOS}}
    for sc in SCENARIOS:
        for pane, rel in (("1-pane", os.path.join("w3", sc, "w3_results", "w3_line_log.csv")),
                          ("5-pane", os.path.join("w3-5", sc, "w3_5pane_results", "w3_line_log.csv"))):
            vals = {p: [] for p in PROTOS}
            path = os.path.join(ROOT, rel)
            if os.path.exists(path):
                with open(path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("status") != "ok":
                            continue
                        proto = row.get("protocol")
                        v = _safe_float(row.get("latency_ms"))
                        if proto in PROTOS and v is not None:
                            vals[proto].append(v)
            for p in PROTOS:
                out[pane][sc][p] = statistics.mean(vals[p]) if vals[p] else 0.0
                cis[pane][sc][p] = _ci95(vals[p]) if vals[p] else 0.0
    return out, cis


# --- Plotting ---

def grouped_bar(ax, scenario_data, scenarios, scen_labels, ylabel,
                title=None, log_scale=False, value_fmt="{:.1f}",
                fontsize_label=None, ymax_pad=1.18, error_data=None,
                show_legend=True, xlabel="", value_label_pad=0.0,
                error_clip=None):
    if fontsize_label is None:
        fontsize_label = FONT["label"]
    x = np.arange(len(scenarios))
    width = 0.25
    spacing = 0.02
    max_v = 0.0
    for i, p in enumerate(PROTOS):
        vals = [scenario_data[sc][p] for sc in scenarios]
        errs = [error_data[sc][p] for sc in scenarios] if error_data is not None else [0.0] * len(vals)
        if error_clip is not None:
            lo, hi = error_clip
            err_low = [min(e, max(0.0, v - lo)) for v, e in zip(vals, errs)]
            err_high = [min(e, max(0.0, hi - v)) for v, e in zip(vals, errs)]
            yerr = np.array([err_low, err_high])
            label_tops = [v + eh for v, eh in zip(vals, err_high)]
        else:
            yerr = errs if error_data is not None else None
            label_tops = [v + e for v, e in zip(vals, errs)]
        max_v = max(max_v, max(label_tops or [0.0]))
        offset = (i - 1) * (width + spacing)
        ax.bar(x + offset, vals, width, facecolor="white", edgecolor=HATCH_COLORS[p], hatch=HATCHES[p], linewidth=0, zorder=2)
        bars = ax.bar(
            x + offset, vals, width, label=PROTO_LABELS[p], facecolor="none", edgecolor=COLORS[p], linewidth=1.2,
            yerr=yerr, ecolor="black", capsize=3, error_kw={"linewidth": 0.8}, zorder=3,
        )
        for b, v, top in zip(bars, vals, label_tops):
            if v <= 0 and top <= 0:
                continue
            ax.text(
                b.get_x() + b.get_width() / 2,
                top + value_label_pad,
                value_fmt.format(v),
                ha="center",
                va="bottom",
                fontsize=fontsize_label,
                color=COLORS[p],
            )
    ax.set_xticks(x)
    ax.set_xticklabels(scen_labels)
    ax.set_xlabel(xlabel, fontsize=FONT["axis"])
    ax.set_ylabel(ylabel, fontsize=FONT["axis"])
    ax.tick_params(axis="x", labelsize=FONT["tick"])
    ax.tick_params(axis="y", labelsize=FONT["tick"])
    if title:
        ax.set_title(title, fontsize=FONT["title"], fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5, zorder=0)
    if show_legend:
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=len(PROTOS), framealpha=0.9, fontsize=FONT["legend"], frameon=True, borderaxespad=0)
    if log_scale:
        ax.set_yscale("log")
    else:
        ax.set_ylim(0, max(1.0, max_v * ymax_pad))


def add_network_region_labels(ax):
    trans = ax.get_xaxis_transform()
    ax.axvline(0.5, color="0.7", linestyle="--", linewidth=1.0, zorder=1)
    ax.text(0, -0.08, "VPN", transform=trans, ha="center", va="top", fontsize=14, color="0.05", clip_on=False)
    ax.text(2, -0.14, "Controlled emulation", transform=trans, ha="center", va="top", fontsize=11, color="0", clip_on=False)


def fig_session_setup(means, ci95):
    fig, ax = plt.subplots(figsize=(6.8, 4.15))
    add_network_region_labels(ax)
    grouped_bar(ax, means, SCENARIOS, SCEN_LABELS, "Time (ms)", value_fmt="{:.0f}", error_data=ci95)
    ax.set_xlabel("")
    fig.subplots_adjust(bottom=0.22, top=0.88)
    out = os.path.join(FIGS_DIR, "session_setup_bar.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")


def fig_recv_pct(rp, rp_ci95):
    light_scen = ["default", "low", "medium", "high"]
    heavy_scen = ["default", "low", "medium", "high"]
    labels = ["", "Low", "Medium", "High"]

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    add_network_region_labels(ax)
    grouped_bar(
        ax,
        rp["Lightweight"],
        light_scen,
        labels,
        "Bytes received (%)",
        value_fmt="{:.1f}",
        ymax_pad=1.1,
        error_data=rp_ci95["Lightweight"],
        show_legend=True,
        fontsize_label=8,
        value_label_pad=1.2,
        error_clip=(0.0, 100.0),
    )
    ax.set_ylim(0, 110)
    fig.subplots_adjust(bottom=0.22, top=0.88)
    out = os.path.join(FIGS_DIR, "recv_pct_lightweight.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")

    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    add_network_region_labels(ax)
    grouped_bar(ax, rp["Heavy-output"], heavy_scen, labels, "Bytes received (%)", value_fmt="{:.1f}", error_data=rp_ci95["Heavy-output"], show_legend=False, fontsize_label=10)
    ax.set_ylim(0, 110)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "recv_pct_heavy.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")


def fig_small_output(g, cis):
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    grouped_bar(ax, g["small"], SCENARIOS, SCEN_LABELS, "Latency (ms)")
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "small_output_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")


def fig_large_output(g, cis):
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    grouped_bar(ax, g["large"], SCENARIOS, SCEN_LABELS, "Latency (ms)", show_legend=False)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "large_output_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")


def fig_w3_keystroke(w3, cis):
    fig, axes = plt.subplots(2, 1, figsize=(6.5, 7.5))
    grouped_bar(axes[0], w3["1-pane"], SCENARIOS, SCEN_LABELS, "Keystroke latency (ms, log)", title="Single-pane tmux", log_scale=True, show_legend=True, fontsize_label=9)
    grouped_bar(axes[1], w3["5-pane"], SCENARIOS, SCEN_LABELS, "Keystroke latency (ms, log)", title="Five-pane tmux", log_scale=True, show_legend=False, fontsize_label=9)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "w3_keystroke_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")


def export_variation_summary(ss_detail, rp_detail):
    out = os.path.join(FIGS_DIR, "variation_ci95_summary.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["figure", "scenario", "protocol", "n", "mean", "std", "ci95_half_width", "source_file"])
        for row in ss_detail + rp_detail:
            writer.writerow(row)
    print(f"[saved] {out}")


def main():
    g, g_ci = collect_w1_w4_groups()
    w3, w3_ci = collect_w3()
    rp, rp_ci95, rp_detail = collect_recv_pct_all()
    ss_means, ss_ci95, ss_detail = collect_session_setup_all()

    fig_session_setup(ss_means, ss_ci95)
    fig_small_output(g, g_ci)
    fig_large_output(g, g_ci)
    fig_recv_pct(rp, rp_ci95)
    fig_w3_keystroke(w3, w3_ci)
    export_variation_summary(ss_detail, rp_detail)


if __name__ == "__main__":
    main()
