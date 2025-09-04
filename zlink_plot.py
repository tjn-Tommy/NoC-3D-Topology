#!/usr/bin/env python3
"""
Plot 6 curves (2 topologies × 3 Z-link latencies) from the TSV sweep.

Usage:
  python3 zlink_plot.py <csv_path> <out_dir>
  python3 zlink_plot.py                 # uses built-in defaults

Input CSV schema (header row required):
  Topology,Traffic,InjectionRate,Throughput,PacketsInjected,PacketsReceived,AvgTotalLatency,AvgHops

Notes:
- This script assumes the sweep driver is `zlink.sh` located next to this file.
- With no arguments, it looks for CSV at: ./lab4/sparse3d_tsv/results.csv
  and writes plots to: ./lab4/sparse3d_tsv/plots

The 'Topology' field should include the Z latency tag, e.g.:
  Sparse3D_Pillars_Z1, Sparse3D_Pillars_Z2, Sparse3D_Pillars_Z4,
  Sparse3D_Pillars_torus_Z1, Sparse3D_Pillars_torus_Z2, Sparse3D_Pillars_torus_Z4
"""

import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from typing import Tuple


# Built-in paths relative to this script
HERE = os.path.dirname(os.path.abspath(__file__))
ZLINK_SH = os.path.join(HERE, "zlink.sh")
DEFAULT_RESULTS_DIR = os.path.join(HERE, "lab4", "sparse3d_tsv")
DEFAULT_CSV = os.path.join(DEFAULT_RESULTS_DIR, "results.csv")
DEFAULT_PLOT_DIR = os.path.join(DEFAULT_RESULTS_DIR, "plots")


def resolve_paths(argv: list) -> Tuple[str, str]:
    """Return (csv_path, out_dir), using built-in defaults when args are omitted."""
    if len(argv) == 1:
        # No arguments: use defaults that match zlink.sh
        return DEFAULT_CSV, DEFAULT_PLOT_DIR
    if len(argv) == 3:
        return argv[1], argv[2]
    print("Usage: python3 zlink_plot.py <csv_path> <out_dir>", file=sys.stderr)
    print(
        "       python3 zlink_plot.py  # uses built-in defaults",
        file=sys.stderr,
    )
    sys.exit(1)


def main():
    csv_path, out_dir = resolve_paths(sys.argv)

    if not os.path.exists(csv_path):
        print(f"ERROR: CSV not found at: {csv_path}", file=sys.stderr)
        if os.path.isfile(ZLINK_SH):
            print(
                f"Hint: generate it by running the sweep: {ZLINK_SH}",
                file=sys.stderr,
            )
        else:
            print(
                "Hint: expected sweep driver 'zlink.sh' next to this script.",
                file=sys.stderr,
            )
        sys.exit(1)

    os.makedirs(out_dir, exist_ok=True)

    df = pd.read_csv(csv_path)
    # Basic cleaning
    df.columns = [c.strip() for c in df.columns]
    for col in ["InjectionRate", "Throughput", "AvgTotalLatency"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["InjectionRate", "Throughput", "AvgTotalLatency", "Topology"])  # type: ignore

    # Keep only the two target base topologies (with Z tags)
    df = df[
        df["Topology"].str.contains(
            "Sparse3D_Pillars_Z|Sparse3D_Pillars_torus_Z", regex=True
        )
    ]
    if df.empty:
        print(
            "No matching Sparse3D/torus TSV data found in CSV.",
            file=sys.stderr,
        )
        sys.exit(0)

    # Choose the first traffic found (typically uniform_random)
    traffic = df["Traffic"].iloc[0]
    df_t = df[df["Traffic"] == traffic].copy()

    # Sort lines for nicer plotting
    df_t = df_t.sort_values(["Topology", "InjectionRate"]).reset_index(
        drop=True
    )

    sns.set_theme(style="whitegrid", palette="deep")

    # 1) Throughput vs InjectionRate (linear)
    def _plot_throughput(ax):
        for topo in sorted(df_t["Topology"].unique()):
            g = df_t[df_t["Topology"] == topo]
            ax.plot(
                g["InjectionRate"],
                g["Throughput"],
                marker="o",
                label=topo,
            )
            # Annotate peak throughput
            idx = g["Throughput"].idxmax()
            x_pk = g.loc[idx, "InjectionRate"]
            y_pk = g.loc[idx, "Throughput"]
            ax.scatter(
                [x_pk], [y_pk], color=ax.lines[-1].get_color(), zorder=5
            )
            ax.annotate(
                f"peak {y_pk:.3f}@{x_pk:.3f}",
                (x_pk, y_pk),
                textcoords="offset points",
                xytext=(6, 6),
                fontsize=8,
            )
        ax.set_title(
            f"Throughput vs Injection Rate — {traffic}\nSparse3D vs Torus with Z-latency ∈ {{1,2,4}}"
        )
        ax.set_xlabel("Injection Rate (pkts/node/cycle)")
        ax.set_ylabel("Throughput (accepted pkts/node/cycle)")
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(title="Topology_Z")

    fig, ax = plt.subplots(figsize=(12, 7))
    _plot_throughput(ax)
    out_tp = os.path.join(
        out_dir, "tsv_latency_sweep_throughput_vs_injection.png"
    )
    fig.savefig(out_tp, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # 1b) Throughput vs InjectionRate (log-scale Y)
    fig, ax = plt.subplots(figsize=(12, 7))
    _plot_throughput(ax)
    # ensure strictly positive lower bound for log scale
    ymin = max(1e-6, float(np.nanmin(df_t["Throughput"].replace(0, np.nan))))
    ax.set_yscale("log")
    ax.set_ylim(bottom=ymin)
    out_tp_logy = os.path.join(
        out_dir, "tsv_latency_sweep_throughput_vs_injection_logy.png"
    )
    fig.savefig(out_tp_logy, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # 2) Latency vs InjectionRate (linear)
    def _plot_latency(ax):
        for topo in sorted(df_t["Topology"].unique()):
            g = df_t[df_t["Topology"] == topo]
            ax.plot(
                g["InjectionRate"],
                g["AvgTotalLatency"],
                marker="s",
                label=topo,
            )
            # annotate low-load latency
            min_idx = g["InjectionRate"].idxmin()
            x0 = g.loc[min_idx, "InjectionRate"]
            y0 = g.loc[min_idx, "AvgTotalLatency"]
            ax.annotate(
                f"low {y0:.1f}",
                (x0, y0),
                textcoords="offset points",
                xytext=(6, 6),
                fontsize=8,
            )
        ax.set_title(
            f"Latency vs Injection Rate — {traffic}\nSparse3D vs Torus with Z-latency ∈ {{1,2,4}}"
        )
        ax.set_xlabel("Injection Rate (pkts/node/cycle)")
        ax.set_ylabel("Average Packet Latency (cycles)")
        ax.set_ylim(bottom=0)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(title="Topology_Z")

    fig, ax = plt.subplots(figsize=(12, 7))
    _plot_latency(ax)
    out_lat = os.path.join(
        out_dir, "tsv_latency_sweep_latency_vs_injection.png"
    )
    fig.savefig(out_lat, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # 2b) Latency vs InjectionRate (log-scale Y)
    fig, ax = plt.subplots(figsize=(12, 7))
    _plot_latency(ax)
    ymin_lat = max(
        1e-3, float(np.nanmin(df_t["AvgTotalLatency"].replace(0, np.nan)))
    )
    ax.set_yscale("log")
    ax.set_ylim(bottom=ymin_lat)
    out_lat_logy = os.path.join(
        out_dir, "tsv_latency_sweep_latency_vs_injection_logy.png"
    )
    fig.savefig(out_lat_logy, dpi=300, bbox_inches="tight")
    plt.close(fig)

    # 3) Latency vs Throughput (useful for saturation view)
    fig, ax = plt.subplots(figsize=(12, 7))
    for topo in sorted(df_t["Topology"].unique()):
        g = df_t[df_t["Topology"] == topo]
        ax.plot(
            g["Throughput"],
            g["AvgTotalLatency"],
            marker="d",
            label=topo,
        )
    ax.set_title(f"Latency vs Throughput — {traffic}")
    ax.set_xlabel("Throughput (accepted pkts/node/cycle)")
    ax.set_ylabel("Average Packet Latency (cycles)")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(title="Topology_Z")
    out_lvt = os.path.join(
        out_dir, "tsv_latency_sweep_latency_vs_throughput.png"
    )
    fig.savefig(out_lvt, dpi=300, bbox_inches="tight")
    plt.close(fig)

    print("Saved:")
    print(out_tp)
    print(out_tp_logy)
    print(out_lat)
    print(out_lat_logy)
    print(out_lvt)
    if os.path.isfile(ZLINK_SH):
        print(f"Sweep script: {ZLINK_SH}")


if __name__ == "__main__":
    main()
