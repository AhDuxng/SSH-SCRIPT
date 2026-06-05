#!/usr/bin/env python3
"""Generate 4 paper figures for 5.evaluation.tex.

Outputs (saved to paper/.../figs/):
  - small_output_bar.pdf   (Table III: small-output commands)
  - large_output_bar.pdf   (Table IV: large-output commands)
  - recv_pct_lightweight.pdf, recv_pct_heavy.pdf (Table V: output completeness)
  - w3_keystroke_bar.pdf   (Table W3: keystroke latency, 2 subplots)

Color scheme (matching session_setup_bar.png):
  - SSH  = blue   (#1f77b4)
  - MOSH = green  (#2ca02c)
  - SSH3 = orange (#ff7f0e)
"""
import csv
import math
import os
import statistics
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

ROOT = "/home/twan/NETWORK/COMPARE_MOSH_SSH_SSH3/w3/SSH-SCRIPT"
FIGS_DIR = os.path.join(
    ROOT,
    "paper/_ATC26__A_Comparative_Study_of_Mosh__SSHv2__and_SSHv3/figs",
)
os.makedirs(FIGS_DIR, exist_ok=True)

# CSVs containing the VPN recv_pct measurements.
# Default lookup order: environment variable -> current directory -> /mnt/data.
# Example override:
#   VPN_W1_RECV_CSV=/path/to/w1_line_log.csv \
#   VPN_W4_RECV_CSV=/path/to/w4_line_log_patched.csv \
#   python3 plot_paper_figures_recv_vpn.py
def _resolve_csv_path(env_name, filename):
    env_path = os.environ.get(env_name)
    candidates = []
    if env_path:
        candidates.append(env_path)
    candidates.extend([
        os.path.join(os.getcwd(), filename),
        os.path.join(ROOT, filename),
        os.path.join("/mnt/data", filename),
    ])
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return candidates[0] if candidates else filename

VPN_W1_RECV_CSV = _resolve_csv_path("VPN_W1_RECV_CSV", "w1_line_log.csv")
VPN_W4_RECV_CSV = _resolve_csv_path("VPN_W4_RECV_CSV", "w4_line_log_patched.csv")
VPN_SESSION_SETUP_CSV = _resolve_csv_path("VPN_SESSION_SETUP_CSV", "w4_session_setup.csv")

SCENARIOS = ["default", "low", "medium", "high"]
SCEN_LABELS = ["", "Low", "Medium", "High"]
PROTOS = ["ssh", "ssh3", "mosh"]
PROTO_LABELS = {"ssh": "SSHv2", "mosh": "Mosh", "ssh3": "SSH3"}
COLORS = {"ssh": "#1f77b4", "mosh": "#2ca02c", "ssh3": "#d62728"}
HATCHES = {"ssh": "////", "mosh": "\\\\\\\\", "ssh3": "////"}
# lighter color for hatch lines inside the bar (alpha blended with white)
HATCH_COLORS = {"ssh": "#d4f4ff", "mosh": "#c2fac0", "ssh3": "#fab9ba"}

# Font sizes — change here to adjust all figures consistently
FONT = {
    "label": 9,    # number on top of each bar
    "title": 13,    # subplot title
    "legend": 11,   # legend
    "axis": 12,     # xlabel / ylabel
    "tick": 11,     # x-axis / y-axis tick labels
}

SMALL_W1 = ["df -h", "grep -n root /etc/passwd", "ls"]
SMALL_W4 = ["git status"]
LARGE_W1 = ["ps aux"]
LARGE_W4 = ["find /", "docker logs $(docker ps -q | head -n 1)"]


# --- Data loading helpers ---

def read_csv(path, warmup_filter=False):
    data = defaultdict(list)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "ok":
                continue
            if warmup_filter and row.get("warmup") == "1":
                continue
            data[(row["protocol"], row["command"])].append(float(row["latency_ms"]))
    return data


def _ci95(samples):
    """95% confidence interval half-width: 1.96 * std / sqrt(n)."""
    n = len(samples)
    if n < 2:
        return 0.0
    return 1.96 * statistics.stdev(samples) / math.sqrt(n)


def collect_w1_w4_groups():
    """dict[group][scenario][proto] = mean of per-command means.
    Also returns CI95 (half-width) across all samples within the group."""
    out = {"small": {sc: {} for sc in SCENARIOS},
           "large": {sc: {} for sc in SCENARIOS}}
    cis = {"small": {sc: {} for sc in SCENARIOS},
           "large": {sc: {} for sc in SCENARIOS}}
    for sc in SCENARIOS:
        w1 = read_csv(f"{ROOT}/w1/w1_results/{sc}/w1_line_log.csv", warmup_filter=True)
        w4 = read_csv(f"{ROOT}/w4/{sc}/w4_results/w4_line_log.csv")
        for p in PROTOS:
            small_means = (
                [statistics.mean(w1[(p, c)]) for c in SMALL_W1] +
                [statistics.mean(w4[(p, c)]) for c in SMALL_W4]
            )
            large_means = (
                [statistics.mean(w1[(p, c)]) for c in LARGE_W1] +
                [statistics.mean(w4[(p, c)]) for c in LARGE_W4]
            )
            out["small"][sc][p] = sum(small_means) / len(small_means)
            out["large"][sc][p] = sum(large_means) / len(large_means)
            small_pool = sum(([w1[(p, c)] for c in SMALL_W1] +
                              [w4[(p, c)] for c in SMALL_W4]), [])
            large_pool = sum(([w1[(p, c)] for c in LARGE_W1] +
                              [w4[(p, c)] for c in LARGE_W4]), [])
            cis["small"][sc][p] = _ci95(small_pool)
            cis["large"][sc][p] = _ci95(large_pool)
    return out, cis


def collect_w3():
    """dict[layout][scenario][proto] = mean keystroke latency.
    Also returns CI95 (half-width) per protocol per scenario."""
    out = {"1-pane": {sc: {} for sc in SCENARIOS},
           "5-pane": {sc: {} for sc in SCENARIOS}}
    cis = {"1-pane": {sc: {} for sc in SCENARIOS},
           "5-pane": {sc: {} for sc in SCENARIOS}}
    for sc in SCENARIOS:
        p1 = f"{ROOT}/w3/{sc}/w3_results/w3_line_log.csv"
        p5 = f"{ROOT}/w3-5/{sc}/w3_5pane_results/w3_line_log.csv"
        d1 = defaultdict(list)
        d5 = defaultdict(list)
        for path, dst in ((p1, d1), (p5, d5)):
            with open(path) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("status") != "ok":
                        continue
                    dst[row["protocol"]].append(float(row["latency_ms"]))
        for p in PROTOS:
            out["1-pane"][sc][p] = statistics.mean(d1[p])
            out["5-pane"][sc][p] = statistics.mean(d5[p])
            cis["1-pane"][sc][p] = _ci95(d1[p])
            cis["5-pane"][sc][p] = _ci95(d5[p])
    return out, cis


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def collect_vpn_recv_pct(csv_path, label, warmup_filter=False):
    """Mean recv_pct and CI95 half-width by protocol from a VPN line-log CSV.

    CI95 is computed as 1.96 * sample_std / sqrt(n). Only rows with
    status=ok and numeric received_pct are used. If warmup_filter=True,
    W1 warmup rows are excluded.
    """
    values = {p: [] for p in PROTOS}

    if not csv_path or not os.path.exists(csv_path):
        print(f"[warn] VPN {label} recv_pct CSV not found: {csv_path}")
        return None, None

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") != "ok":
                continue
            if warmup_filter and row.get("warmup") == "1":
                continue
            proto = row.get("protocol")
            if proto not in PROTOS:
                continue
            value = _safe_float(row.get("received_pct"))
            if value is not None:
                values[proto].append(value)

    if any(len(values[p]) == 0 for p in PROTOS):
        print(f"[warn] Incomplete VPN {label} recv_pct data in {csv_path}: " +
              str({p: len(values[p]) for p in PROTOS}))

    means = {p: (statistics.mean(values[p]) if values[p] else 0.0) for p in PROTOS}
    ci95 = {p: (_ci95(values[p]) if values[p] else 0.0) for p in PROTOS}

    print(f"[info] VPN {label} recv_pct from {csv_path}: " +
          ", ".join(f"{PROTO_LABELS[p]}={means[p]:.2f}% ±{ci95[p]:.2f}" for p in PROTOS))
    return means, ci95


def _zero_ci_like(data):
    return {
        group: {sc: {p: 0.0 for p in PROTOS} for sc in scenarios}
        for group, scenarios in data.items()
    }


def recv_pct_data():
    """Table V data plus VPN groups from local W1/W4 line logs.

    Returns (means, ci95). CI95 for the new VPN groups is computed from the
    uploaded/current CSV files. Low/Medium/High values remain the hard-coded
    Table V means, so their CI95 is set to 0 unless raw scenario CSVs are added.
    """
    data = {
        "Lightweight": {
            "low":    {"ssh": 99.2, "mosh": 92.2, "ssh3": 99.2},
            "medium": {"ssh": 99.2, "mosh": 90.5, "ssh3": 99.0},
            "high":   {"ssh": 99.2, "mosh": 89.1, "ssh3": 99.2},
        },
        "Heavy-output": {
            "low":    {"ssh": 96.3, "mosh": 6.3, "ssh3": 96.6},
            "medium": {"ssh": 98.1, "mosh": 2.1, "ssh3": 97.0},
            "high":   {"ssh": 96.4, "mosh": 2.5, "ssh3": 95.4},
        },
    }
    ci95 = _zero_ci_like(data)

    # If raw controlled-scenario CSVs are present under ROOT, use them to
    # compute both mean and CI95 for Low/Medium/High instead of relying only on
    # hard-coded Table V means.
    for sc in ("low", "medium", "high"):
        w1_path = os.path.join(ROOT, "w1", "w1_results", sc, "w1_line_log.csv")
        if os.path.exists(w1_path):
            mean, ci = collect_vpn_recv_pct(w1_path, f"W1/lightweight {sc}", warmup_filter=True)
            if mean is not None:
                data["Lightweight"][sc] = mean
                ci95["Lightweight"][sc] = ci

        w4_path = os.path.join(ROOT, "w4", sc, "w4_results", "w4_line_log.csv")
        if os.path.exists(w4_path):
            mean, ci = collect_vpn_recv_pct(w4_path, f"W4/heavy-output {sc}", warmup_filter=False)
            if mean is not None:
                data["Heavy-output"][sc] = mean
                ci95["Heavy-output"][sc] = ci

    vpn_light, vpn_light_ci = collect_vpn_recv_pct(
        VPN_W1_RECV_CSV, "W1/lightweight VPN", warmup_filter=True
    )
    if vpn_light is not None:
        data["Lightweight"] = {"default": vpn_light, **data["Lightweight"]}
        ci95["Lightweight"] = {"default": vpn_light_ci, **ci95["Lightweight"]}

    vpn_heavy, vpn_heavy_ci = collect_vpn_recv_pct(
        VPN_W4_RECV_CSV, "W4/heavy-output VPN", warmup_filter=False
    )
    if vpn_heavy is not None:
        data["Heavy-output"] = {"default": vpn_heavy, **data["Heavy-output"]}
        ci95["Heavy-output"] = {"default": vpn_heavy_ci, **ci95["Heavy-output"]}

    return data, ci95


def _collect_session_setup_from_csv(csv_path):
    """Collect mean and CI95 for VPN session setup from a setup CSV if present."""
    values = {p: [] for p in PROTOS}
    if not csv_path or not os.path.exists(csv_path):
        print(f"[warn] VPN session setup CSV not found: {csv_path}")
        return None, None

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            proto = row.get("protocol")
            if proto not in PROTOS:
                continue
            value = _safe_float(row.get("session_setup_ms"))
            if value is not None:
                values[proto].append(value)

    if any(len(values[p]) == 0 for p in PROTOS):
        print(f"[warn] Incomplete session setup data in {csv_path}: " +
              str({p: len(values[p]) for p in PROTOS}))

    means = {p: (statistics.mean(values[p]) if values[p] else 0.0) for p in PROTOS}
    ci95 = {p: (_ci95(values[p]) if values[p] else 0.0) for p in PROTOS}
    print(f"[info] VPN session setup from {csv_path}: " +
          ", ".join(f"{PROTO_LABELS[p]}={means[p]:.2f} ms ±{ci95[p]:.2f}" for p in PROTOS))
    return means, ci95


def session_setup_data():
    """Session setup mean and CI95 half-width.

    Low/Medium/High are the existing Table II means with CI95 from Table II std.
    If VPN_SESSION_SETUP_CSV exists, the VPN/default row is updated from that CSV.
    """
    means = {
        "default": {"ssh": 765,  "ssh3": 219,  "mosh": 908},
        "low":     {"ssh": 811,  "ssh3": 362,  "mosh": 1166},
        "medium":  {"ssh": 1796, "ssh3": 645,  "mosh": 2366},
        "high":    {"ssh": 3565, "ssh3": 1042, "mosh": 3856},
    }
    stds = {
        "default": {"ssh": 155, "ssh3": 56,  "mosh": 163},
        "low":     {"ssh": 49,  "ssh3": 50,  "mosh": 36},
        "medium":  {"ssh": 137, "ssh3": 88,  "mosh": 283},
        "high":    {"ssh": 706, "ssh3": 202, "mosh": 353},
    }
    n_per_scenario = {"default": 60, "low": 18, "medium": 18, "high": 18}
    ci95 = {sc: {p: 1.96 * stds[sc][p] / math.sqrt(n_per_scenario[sc])
                 for p in PROTOS}
            for sc in SCENARIOS}

    for sc in ("low", "medium", "high"):
        setup_path = os.path.join(ROOT, "w4", sc, "w4_results", "w4_session_setup.csv")
        if os.path.exists(setup_path):
            sc_mean, sc_ci95 = _collect_session_setup_from_csv(setup_path)
            if sc_mean is not None:
                means[sc] = sc_mean
                ci95[sc] = sc_ci95

    vpn_mean, vpn_ci95 = _collect_session_setup_from_csv(VPN_SESSION_SETUP_CSV)
    if vpn_mean is not None:
        means["default"] = vpn_mean
        ci95["default"] = vpn_ci95

    return means, ci95


# --- Plotting helpers ---

def grouped_bar(ax, scenario_data, scenarios, scen_labels, ylabel,
                title=None, log_scale=False, value_fmt="{:.1f}",
                fontsize_label=None, ymax_pad=1.18, error_data=None,
                show_legend=True, xlabel=""):
    if fontsize_label is None:
        fontsize_label = FONT["label"]
    x = np.arange(len(scenarios))
    width = 0.25
    spacing = 0.02  # gap between bars within the same group
    max_v = 0
    for i, p in enumerate(PROTOS):
        vals = [scenario_data[sc][p] for sc in scenarios]
        max_v = max(max_v, max(vals))
        offset = (i - 1) * (width + spacing)
        # Layer 1: light hatch lines (no border)
        ax.bar(x + offset, vals, width,
               facecolor="white", edgecolor=HATCH_COLORS[p],
               hatch=HATCHES[p], linewidth=0)
        # Layer 2: dark border on top (no hatch, transparent fill) + optional error bars
        if error_data is not None:
            errs = [error_data[sc][p] for sc in scenarios]
            bars = ax.bar(x + offset, vals, width, label=PROTO_LABELS[p],
                          facecolor="none", edgecolor=COLORS[p],
                          linewidth=1.2, yerr=errs,
                          ecolor="black", capsize=3,
                          error_kw={"linewidth": 0.8})
            # update max to include error bar top
            for v, e in zip(vals, errs):
                max_v = max(max_v, v + e)
        else:
            errs = [0] * len(vals)
            bars = ax.bar(x + offset, vals, width, label=PROTO_LABELS[p],
                          facecolor="none", edgecolor=COLORS[p],
                          linewidth=1.2)
        for b, v, e in zip(bars, vals, errs):
            ax.text(b.get_x() + b.get_width() / 2, v + e,
                    value_fmt.format(v),
                    ha="center", va="bottom", fontsize=fontsize_label,
                    color=COLORS[p])
    ax.set_xticks(x)
    ax.set_xticklabels(scen_labels)
    ax.set_xlabel(xlabel, fontsize=FONT["axis"])
    ax.set_ylabel(ylabel, fontsize=FONT["axis"])
    ax.tick_params(axis="x", labelsize=FONT["tick"])
    ax.tick_params(axis="y", labelsize=FONT["tick"])
    if title:
        ax.set_title(title, fontsize=FONT["title"], fontweight="bold")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    # Legend outside the axes, horizontal layout above the chart
    if show_legend:
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
                  ncol=len(PROTOS), framealpha=0.9, fontsize=FONT["legend"],
                  frameon=True, borderaxespad=0)
    if log_scale:
        ax.set_yscale("log")
    else:
        ax.set_ylim(0, max_v * ymax_pad)


def add_network_region_labels(ax):
    trans = ax.get_xaxis_transform()

    # shaded region for emulation scenarios: Low, Medium, High
    ax.axvspan(
        0.5, 3.5,
        ymin=0,
        ymax=1,
        facecolor="1",
        edgecolor="none",
        zorder=0,
    )

    # separator between real VPN and emulated scenarios
    ax.axvline(
        0.5,
        color="0.7",
        linestyle="--",
        linewidth=1.0,
        zorder=1,
    )

    # group captions under x-axis
    ax.text(
        0,
        -0.08,
        "VPN",
        transform=trans,
        ha="center",
        va="top",
        fontsize=14,
        color="0.05",
        clip_on=False,
    )

    ax.text(
        2,
        -0.14,
        "Controlled emulation",
        transform=trans,
        ha="center",
        va="top",
        fontsize=11,
        color="0",
        clip_on=False,
    )

# --- Figure builders ---

def fig_session_setup(means, stds):
    fig, ax = plt.subplots(figsize=(6.8, 4.15))

    add_network_region_labels(ax)

    grouped_bar(
        ax, means, SCENARIOS, SCEN_LABELS,
        "Time (ms)",
        value_fmt="{:.0f}",
        error_data=stds,
    )

    ax.set_xlabel("")
    fig.subplots_adjust(bottom=0.22, top=0.88)

    out = os.path.join(FIGS_DIR, "session_setup_bar.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")

def fig_small_output(g, stds):
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    grouped_bar(ax, g["small"], SCENARIOS, SCEN_LABELS,
                "Latency (ms)")
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "small_output_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")


def fig_large_output(g, stds):
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    grouped_bar(ax, g["large"], SCENARIOS, SCEN_LABELS,
                "Latency (ms)", show_legend=False)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "large_output_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")


def fig_recv_pct(rp, rp_ci95):
    light_scen = ["default", "low", "medium", "high"] if "default" in rp["Lightweight"] else ["low", "medium", "high"]
    light_labels = ["", "Low", "Medium", "High"] if "default" in rp["Lightweight"] else ["Low", "Medium", "High"]

    heavy_scen = ["default", "low", "medium", "high"] if "default" in rp["Heavy-output"] else ["low", "medium", "high"]
    heavy_labels = ["", "Low", "Medium", "High"] if "default" in rp["Heavy-output"] else ["Low", "Medium", "High"]

    # Lightweight subplot — now includes VPN from w1_line_log.csv
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    add_network_region_labels(ax)
    grouped_bar(ax, rp["Lightweight"], light_scen, light_labels,
                "Bytes received (%)",
                title="",
                value_fmt="{:.1f}", ymax_pad=1.1,
                error_data=rp_ci95["Lightweight"],
                show_legend=True,
                fontsize_label=10,
                xlabel="")
    ax.set_ylim(80, 105)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "recv_pct_lightweight.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")

    # Heavy-output subplot — now includes VPN from w4_line_log_patched(2).csv
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    add_network_region_labels(ax)
    grouped_bar(ax, rp["Heavy-output"], heavy_scen, heavy_labels,
                "Bytes received (%)",
                title="",
                value_fmt="{:.1f}",
                error_data=rp_ci95["Heavy-output"],
                show_legend=False,
                fontsize_label=10,
                xlabel="")
    ax.set_ylim(0, 110)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "recv_pct_heavy.pdf")
    plt.savefig(out, bbox_inches="tight")
    plt.close()
    print(f"[saved] {out}")


def fig_w3_keystroke(w3, stds):
    fig, axes = plt.subplots(2, 1, figsize=(6.5, 7.5))
    # Top subplot: legend ON (shared legend)
    grouped_bar(axes[0], w3["1-pane"], SCENARIOS, SCEN_LABELS,
                "Keystroke latency (ms, log)",
                title="Single-pane tmux",
                log_scale=True,
                show_legend=True,
                fontsize_label=9)
    # Bottom subplot: legend OFF
    grouped_bar(axes[1], w3["5-pane"], SCENARIOS, SCEN_LABELS,
                "Keystroke latency (ms, log)",
                title="Five-pane tmux",
                log_scale=True,
                show_legend=False,
                fontsize_label=9)
    plt.tight_layout()
    out = os.path.join(FIGS_DIR, "w3_keystroke_bar.pdf")
    plt.savefig(out)
    plt.close()
    print(f"[saved] {out}")



def export_variation_summary(ss_means, ss_ci95, rp, rp_ci95):
    out = os.path.join(FIGS_DIR, "variation_ci95_summary.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["figure", "scenario", "protocol", "mean", "ci95_half_width"])
        for sc in SCENARIOS:
            for p in PROTOS:
                writer.writerow(["session_setup_ms", sc, p, f"{ss_means[sc][p]:.6f}", f"{ss_ci95[sc][p]:.6f}"])
        for group in ("Lightweight", "Heavy-output"):
            for sc in rp[group]:
                for p in PROTOS:
                    writer.writerow([f"recv_pct_{group}", sc, p, f"{rp[group][sc][p]:.6f}", f"{rp_ci95[group][sc][p]:.6f}"])
    print(f"[saved] {out}")

def main():
    g, g_stds = collect_w1_w4_groups()
    w3, w3_stds = collect_w3()
    rp, rp_ci95 = recv_pct_data()
    ss_means, ss_stds = session_setup_data()

    fig_session_setup(ss_means, ss_stds)
    fig_small_output(g, g_stds)
    fig_large_output(g, g_stds)
    fig_recv_pct(rp, rp_ci95)
    fig_w3_keystroke(w3, w3_stds)
    export_variation_summary(ss_means, ss_stds, rp, rp_ci95)


if __name__ == "__main__":
    main()
