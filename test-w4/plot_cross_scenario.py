"""
Cross-scenario comparison charts for W4 benchmark results.
Reads w4_meta.json from each scenario directory and produces:
  1. Session setup time (grouped bar, all 4 scenarios)
  2. Output delivery latency per workload size (3 subplots, log scale)
  3. Throughput per workload size (3 subplots)
  4. Latency heatmap (protocol × scenario, one panel per workload)
"""

import json
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

RESULT_DIR = os.path.join(os.path.dirname(__file__), "w4_results")
SCENARIOS = ["low", "default", "medium", "high"]
SCENARIO_LABELS = {
    "low":     "low\n(RTT~20ms, 0% loss)",
    "default": "default\n(VPN~100ms, 0% loss)",
    "medium":  "medium\n(RTT~100ms, 1.5% loss)",
    "high":    "high\n(RTT~200ms, 3% loss)",
}
PROTOCOLS = ["ssh", "ssh3", "mosh"]
PROTO_COLORS = {"ssh": "#2196F3", "ssh3": "#FF9800", "mosh": "#4CAF50"}
PROTO_LABELS = {"ssh": "SSH", "ssh3": "SSH3", "mosh": "Mosh"}

WORKLOADS = [
    ("head -c 524288 /dev/zero | base64",  "512 KiB"),
    ("head -c 2097152 /dev/zero | base64", "2 MiB"),
    ("head -c 8388608 /dev/zero | base64", "8 MiB"),
]

OUT_DIR = os.path.join(RESULT_DIR, "_cross_scenario")
os.makedirs(OUT_DIR, exist_ok=True)


def load_all():
    data = {}
    for sc in SCENARIOS:
        path = os.path.join(RESULT_DIR, sc, "w4_meta.json")
        with open(path) as f:
            data[sc] = json.load(f)
    return data


def get_latency(data, scenario, protocol, command):
    for r in data[scenario]["summary"]:
        if r["protocol"] == protocol and r["command"] == command:
            return r["mean_ms"], r["p95_ms"], r["stdev_ms"]
    return None, None, None


def get_throughput(data, scenario, protocol, command):
    for r in data[scenario]["summary"]:
        if r["protocol"] == protocol and r["command"] == command:
            return r["mean_throughput_kib_s"]
    return None


def get_setup(data, scenario, protocol):
    ss = data[scenario].get("session_setup", {}).get(protocol, {})
    if not ss:
        return None, None
    vals = [v["mean"] for v in ss.values()]
    return np.mean(vals), np.std(vals)


# ── 1. Session setup ──────────────────────────────────────────────────────────
def plot_session_setup(data):
    fig, ax = plt.subplots(figsize=(10, 5))
    n_sc = len(SCENARIOS)
    n_pr = len(PROTOCOLS)
    width = 0.22
    x = np.arange(n_sc)

    for i, proto in enumerate(PROTOCOLS):
        means, stds = [], []
        for sc in SCENARIOS:
            m, s = get_setup(data, sc, proto)
            means.append(m if m else 0)
            stds.append(s if s else 0)
        offset = (i - 1) * width
        bars = ax.bar(x + offset, means, width, yerr=stds, capsize=4,
                      label=PROTO_LABELS[proto], color=PROTO_COLORS[proto],
                      alpha=0.88, error_kw={"elinewidth": 1.2})
        for bar, val in zip(bars, means):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 30,
                    f"{val:.0f}", ha="center", va="bottom", fontsize=7.5)

    ax.set_xticks(x)
    ax.set_xticklabels([SCENARIO_LABELS[s] for s in SCENARIOS], fontsize=9)
    ax.set_ylabel("Session setup time (ms)")
    ax.set_title("Session Setup Time — SSH vs SSH3 vs Mosh across Network Scenarios")
    ax.legend()
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ax.grid(axis="y", which="minor", linestyle=":", alpha=0.25)
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "cross_session_setup.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


# ── 2. Latency per workload (log scale) ───────────────────────────────────────
def plot_latency(data):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5), sharey=False)
    fig.suptitle("Output Delivery Latency (mean ± stdev) — Log Scale", fontsize=13)

    for ax, (cmd, label) in zip(axes, WORKLOADS):
        n_sc = len(SCENARIOS)
        n_pr = len(PROTOCOLS)
        width = 0.22
        x = np.arange(n_sc)

        for i, proto in enumerate(PROTOCOLS):
            means, stds = [], []
            for sc in SCENARIOS:
                m, p95, sd = get_latency(data, sc, proto, cmd)
                means.append(m if m else 0)
                stds.append(sd if sd else 0)
            offset = (i - 1) * width
            bars = ax.bar(x + offset, means, width, yerr=stds, capsize=3,
                          label=PROTO_LABELS[proto], color=PROTO_COLORS[proto],
                          alpha=0.88, error_kw={"elinewidth": 1.0})
            for bar, val in zip(bars, means):
                if val > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2,
                            bar.get_height() * 1.08,
                            f"{val/1000:.1f}s" if val >= 1000 else f"{val:.0f}ms",
                            ha="center", va="bottom", fontsize=6.5, rotation=45)

        ax.set_yscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABELS[s] for s in SCENARIOS], fontsize=8)
        ax.set_title(f"Workload: {label}", fontsize=10)
        ax.set_ylabel("Latency (ms, log scale)")
        ax.legend(fontsize=8)
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(
            lambda v, _: f"{v/1000:.0f}s" if v >= 1000 else f"{v:.0f}ms"))

    fig.tight_layout()
    out = os.path.join(OUT_DIR, "cross_latency_log.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


# ── 3. Throughput per workload ────────────────────────────────────────────────
def plot_throughput(data):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle("Effective Throughput (output_bytes / latency) — SSH & SSH3 only\n"
                 "(Mosh excluded: output_bytes ≈ screen-sync diff, not actual payload)",
                 fontsize=11)

    for ax, (cmd, label) in zip(axes, WORKLOADS):
        x = np.arange(len(SCENARIOS))
        width = 0.3
        for i, proto in enumerate(["ssh", "ssh3"]):
            vals = []
            for sc in SCENARIOS:
                t = get_throughput(data, sc, proto, cmd)
                vals.append(t if t else 0)
            offset = (i - 0.5) * width
            bars = ax.bar(x + offset, vals, width,
                          label=PROTO_LABELS[proto], color=PROTO_COLORS[proto],
                          alpha=0.88)
            for bar, val in zip(bars, vals):
                if val > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2,
                            bar.get_height() + 50,
                            f"{val:.0f}", ha="center", va="bottom", fontsize=7)

        ax.set_xticks(x)
        ax.set_xticklabels([SCENARIO_LABELS[s] for s in SCENARIOS], fontsize=8)
        ax.set_title(f"Workload: {label}", fontsize=10)
        ax.set_ylabel("Throughput (KiB/s)")
        ax.legend(fontsize=8)
        ax.grid(axis="y", linestyle="--", alpha=0.4)

    fig.tight_layout()
    out = os.path.join(OUT_DIR, "cross_throughput.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


# ── 4. Heatmap: latency ratio SSH3/SSH ────────────────────────────────────────
def plot_heatmap(data):
    """
    Two heatmaps side by side:
      Left:  absolute mean latency (ms) for all protocol × scenario cells
      Right: speedup ratio SSH3/SSH and Mosh/SSH
    """
    wl_labels = [w[1] for w in WORKLOADS]
    sc_labels  = [SCENARIO_LABELS[s].replace("\n", " ") for s in SCENARIOS]

    # Build matrix: rows = workload, cols = scenario, depth = protocol
    def build_matrix(proto):
        mat = np.zeros((len(WORKLOADS), len(SCENARIOS)))
        for wi, (cmd, _) in enumerate(WORKLOADS):
            for si, sc in enumerate(SCENARIOS):
                m, _, _ = get_latency(data, sc, proto, cmd)
                mat[wi, si] = m if m else np.nan
        return mat

    ssh_mat  = build_matrix("ssh")
    ssh3_mat = build_matrix("ssh3")
    mosh_mat = build_matrix("mosh")

    fig, axes = plt.subplots(1, 3, figsize=(18, 4))
    fig.suptitle("Output Delivery Latency Heatmap (mean ms)", fontsize=12)

    for ax, mat, proto in zip(axes, [ssh_mat, ssh3_mat, mosh_mat], PROTOCOLS):
        im = ax.imshow(mat, aspect="auto", cmap="YlOrRd")
        ax.set_xticks(range(len(SCENARIOS)))
        ax.set_xticklabels(sc_labels, rotation=20, ha="right", fontsize=8)
        ax.set_yticks(range(len(WORKLOADS)))
        ax.set_yticklabels(wl_labels, fontsize=9)
        ax.set_title(PROTO_LABELS[proto], fontsize=11)
        for wi in range(len(WORKLOADS)):
            for si in range(len(SCENARIOS)):
                val = mat[wi, si]
                txt = f"{val/1000:.1f}s" if val >= 1000 else f"{val:.0f}ms"
                ax.text(si, wi, txt, ha="center", va="center",
                        fontsize=8, color="black" if val < mat.max() * 0.6 else "white")
        fig.colorbar(im, ax=ax, label="ms")

    fig.tight_layout()
    out = os.path.join(OUT_DIR, "cross_heatmap.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


# ── 5. Line chart: latency vs scenario per workload ───────────────────────────
def plot_line_trend(data):
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=False)
    fig.suptitle("Output Delivery Latency vs Network Scenario (mean ± stdev)", fontsize=12)

    sc_x = list(range(len(SCENARIOS)))
    sc_tick = [SCENARIO_LABELS[s] for s in SCENARIOS]

    for ax, (cmd, label) in zip(axes, WORKLOADS):
        for proto in PROTOCOLS:
            means, stds = [], []
            for sc in SCENARIOS:
                m, _, sd = get_latency(data, sc, proto, cmd)
                means.append(m if m else 0)
                stds.append(sd if sd else 0)
            means = np.array(means)
            stds  = np.array(stds)
            ax.plot(sc_x, means, marker="o", label=PROTO_LABELS[proto],
                    color=PROTO_COLORS[proto], linewidth=2)
            ax.fill_between(sc_x, means - stds, means + stds,
                            color=PROTO_COLORS[proto], alpha=0.15)

        ax.set_yscale("log")
        ax.set_xticks(sc_x)
        ax.set_xticklabels(sc_tick, fontsize=8)
        ax.set_title(f"Workload: {label}", fontsize=10)
        ax.set_ylabel("Latency (ms, log scale)")
        ax.legend(fontsize=8)
        ax.grid(linestyle="--", alpha=0.4)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(
            lambda v, _: f"{v/1000:.0f}s" if v >= 1000 else f"{v:.0f}ms"))

    fig.tight_layout()
    out = os.path.join(OUT_DIR, "cross_latency_line.png")
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Saved: {out}")


if __name__ == "__main__":
    data = load_all()
    plot_session_setup(data)
    plot_latency(data)
    plot_throughput(data)
    plot_heatmap(data)
    plot_line_trend(data)
    print("Done. Charts saved to", OUT_DIR)
