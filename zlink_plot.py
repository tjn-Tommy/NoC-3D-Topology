#!/usr/bin/env python3
"""
Plot curves from Z-link TSV sweeps and support different topologies.

Usage:
  python3 zlink_plot.py [csv_path] [out_dir] [--match REGEX] [--traffic NAME]
                        [--per-topo]

Examples:
  # Use defaults that match zlink.sh
  python3 zlink_plot.py

  # Explicit CSV/output dir, include any topologies with Z tags
  python3 zlink_plot.py results.csv plots

  # Only plot Mesh and Torus variants (with Z-latency suffix)
  python3 zlink_plot.py results.csv plots --match "(Mesh|Torus).*_Z\\d+$"

  # Plot per base-topology figures for a given traffic
  python3 zlink_plot.py results.csv plots --per-topo --traffic uniform_random

Input CSV schema (header row required):
  Topology,Traffic,InjectionRate,Throughput,PacketsInjected,PacketsReceived,AvgTotalLatency,AvgHops

Notes:
- This script assumes the sweep driver is `zlink.sh` located next to this file.
- With no arguments, it looks for CSV at: ./lab4/sparse3d_tsv/results.csv
  and writes plots to: ./lab4/sparse3d_tsv/plots

By convention, the 'Topology' field can include a Z-latency tag, e.g.:
  Sparse3D_Pillars_Z1, Sparse3D_Pillars_Z2, Sparse3D_Pillars_Z4,
  Sparse3D_Pillars_torus_Z1, Sparse3D_Pillars_torus_Z2, Sparse3D_Pillars_torus_Z4
This script derives a base-topology name by removing the trailing `_Z<d+>` when present.
"""

import os
import sys
import re
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
from typing import Tuple, Optional


# Built-in paths relative to this script
HERE = os.path.dirname(os.path.abspath(__file__))
ZLINK_SH = os.path.join(HERE, "zlink.sh")
DEFAULT_RESULTS_DIR = os.path.join(HERE, "lab4", "sparse3d_tsv")
DEFAULT_CSV = os.path.join(DEFAULT_RESULTS_DIR, "results.csv")
DEFAULT_PLOT_DIR = os.path.join(DEFAULT_RESULTS_DIR, "plots")


def resolve_paths_and_args(argv: list) -> Tuple[str, str, Optional[str], Optional[str], bool]:
    """Parse arguments and return (csv_path, out_dir, match_regex, traffic, per_topo)."""
    # Very small argparse replacement to keep the script lightweight and
    # backward-compatible with previous positional-only usage.
    csv_path = DEFAULT_CSV
    out_dir = DEFAULT_PLOT_DIR
    match_regex: Optional[str] = None
    traffic: Optional[str] = None
    per_topo = False

    # Collect flags first
    flags = []
    pos = []
    for a in argv[1:]:
        if a.startswith("--"):
            flags.append(a)
        else:
            pos.append(a)

    # Positional handling: 0 or 2 (csv, out_dir)
    if len(pos) == 0:
        pass  # keep defaults
    elif len(pos) == 2:
        csv_path, out_dir = pos
    else:
        print(
            "Usage: python3 zlink_plot.py [csv_path] [out_dir] [--match REGEX] [--traffic NAME] [--per-topo]",
            file=sys.stderr,
        )
        sys.exit(1)

    # Flag parsing (simple, order-independent)
    i = 0
    while i < len(flags):
        f = flags[i]
        if f == "--per-topo":
            per_topo = True
            i += 1
            continue
        if f == "--match":
            if i + 1 >= len(flags):
                print("ERROR: --match requires a REGEX argument", file=sys.stderr)
                sys.exit(1)
            match_regex = flags[i + 1]
            i += 2
            continue
        if f == "--traffic":
            if i + 1 >= len(flags):
                print("ERROR: --traffic requires a NAME argument", file=sys.stderr)
                sys.exit(1)
            traffic = flags[i + 1]
            i += 2
            continue
        print(f"ERROR: Unknown flag {f}", file=sys.stderr)
        sys.exit(1)

    return csv_path, out_dir, match_regex, traffic, per_topo


def main():
    csv_path, out_dir, match_regex, opt_traffic, per_topo = resolve_paths_and_args(sys.argv)

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

    # Derive base topology (strip trailing _Z<d+>) and Z tag if present
    df["base_topo"] = df["Topology"].str.replace(r"_Z\d+$", "", regex=True)
    z_series = df["Topology"].str.extract(r"_Z(\d+)$")[0]
    df["z_tag"] = z_series.where(~z_series.isna(), other="NA")

    # Filter by regex if provided, else default to any topology with a Z tag
    if match_regex:
        try:
            df = df[df["Topology"].str.contains(match_regex, regex=True)]
        except re.error as e:
            print(f"ERROR: invalid --match regex: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        df = df[df["Topology"].str.contains(r"_Z\d+$", regex=True)]

    if df.empty:
        print("No matching TSV data found in CSV after filtering.", file=sys.stderr)
        sys.exit(0)

    # Traffic selection
    traffic: str
    if opt_traffic is not None:
        if opt_traffic not in set(df["Traffic"].unique()):
            print(
                f"ERROR: --traffic {opt_traffic} not found. Available: {sorted(df['Traffic'].unique())}",
                file=sys.stderr,
            )
            sys.exit(1)
        traffic = opt_traffic
    else:
        uniq = list(sorted(set(df["Traffic"].unique())))
        if len(uniq) == 1:
            traffic = uniq[0]
        elif "uniform_random" in uniq:
            traffic = "uniform_random"
        else:
            traffic = uniq[0]
            print(
                f"Info: multiple Traffic values found {uniq}. Defaulting to '{traffic}'.",
                file=sys.stderr,
            )

    df_t = df[df["Traffic"] == traffic].copy()

    # Sort lines for nicer plotting
    df_t = df_t.sort_values(["Topology", "InjectionRate"]).reset_index(
        drop=True
    )

    sns.set_theme(style="whitegrid", palette="deep")

    # Helpers for labeling, titles, and filename tagging
    def _sanitize(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)

    def _label_for_combined(topo: str) -> str:
        return topo

    def _label_for_per_topo(z: str) -> str:
        return f"Z{z}"

    # 1) Throughput vs InjectionRate (linear)
    def _plot_throughput(ax, groups, xcol="InjectionRate", ycol="Throughput"):
        for label, g in groups:
            g = g.sort_values([xcol])
            ax.plot(
                g[xcol],
                g[ycol],
                marker="o",
                label=label,
            )
            # Annotate peak throughput
            idx = g[ycol].idxmax()
            x_pk = g.loc[idx, xcol]
            y_pk = g.loc[idx, ycol]
            ax.scatter([x_pk], [y_pk], color=ax.lines[-1].get_color(), zorder=5)
            ax.annotate(
                f"peak {y_pk:.3f}@{x_pk:.3f}",
                (x_pk, y_pk),
                textcoords="offset points",
                xytext=(6, 6),
                fontsize=8,
            )
        ax.set_xlabel("Injection Rate (pkts/node/cycle)")
        ax.set_ylabel("Throughput (accepted pkts/node/cycle)")
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(title="Series")

    # 2) Latency vs InjectionRate (linear)
    def _plot_latency(ax, groups, xcol="InjectionRate", ycol="AvgTotalLatency"):
        for label, g in groups:
            g = g.sort_values([xcol])
            ax.plot(
                g[xcol],
                g[ycol],
                marker="s",
                label=label,
            )
            # annotate low-load latency
            min_idx = g[xcol].idxmin()
            x0 = g.loc[min_idx, xcol]
            y0 = g.loc[min_idx, ycol]
            ax.annotate(
                f"low {y0:.1f}",
                (x0, y0),
                textcoords="offset points",
                xytext=(6, 6),
                fontsize=8,
            )
        ax.set_xlabel("Injection Rate (pkts/node/cycle)")
        ax.set_ylabel("Average Packet Latency (cycles)")
        ax.set_ylim(bottom=0)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(title="Series")

    # 3) Latency vs Throughput (useful for saturation view)
    def _plot_latency_vs_tp(ax, groups, xcol="Throughput", ycol="AvgTotalLatency"):
        for label, g in groups:
            g = g.sort_values([xcol])
            ax.plot(
                g[xcol],
                g[ycol],
                marker="d",
                label=label,
            )
        ax.set_xlabel("Throughput (accepted pkts/node/cycle)")
        ax.set_ylabel("Average Packet Latency (cycles)")
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.legend(title="Series")

    saved_paths = []

    if per_topo:
        # Generate separate figures per base topology; legend shows Z tags
        for base in sorted(df_t["base_topo"].unique()):
            df_b = df_t[df_t["base_topo"] == base]
            if df_b.empty:
                continue

            groups_tp = [( _label_for_per_topo(z), g) for z, g in df_b.groupby("z_tag")]

            # Throughput vs Injection (linear)
            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_throughput(ax, groups_tp)
            ax.set_title(f"Throughput vs Injection — {traffic}\n{base} (Z-latency sweep)")
            out_tp = os.path.join(out_dir, f"{_sanitize(base)}_{_sanitize(traffic)}_throughput_vs_injection.png")
            fig.savefig(out_tp, dpi=300, bbox_inches="tight")
            plt.close(fig)
            saved_paths.append(out_tp)

            # Throughput vs Injection (log-y)
            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_throughput(ax, groups_tp)
            ymin = max(1e-6, float(np.nanmin(df_b["Throughput"].replace(0, np.nan))))
            ax.set_yscale("log")
            ax.set_ylim(bottom=ymin)
            ax.set_title(f"Throughput vs Injection (log-y) — {traffic}\n{base} (Z-latency sweep)")
            out_tp_logy = os.path.join(out_dir, f"{_sanitize(base)}_{_sanitize(traffic)}_throughput_vs_injection_logy.png")
            fig.savefig(out_tp_logy, dpi=300, bbox_inches="tight")
            plt.close(fig)
            saved_paths.append(out_tp_logy)

            # Latency vs Injection (linear)
            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_latency(ax, groups_tp)
            ax.set_title(f"Latency vs Injection — {traffic}\n{base} (Z-latency sweep)")
            out_lat = os.path.join(out_dir, f"{_sanitize(base)}_{_sanitize(traffic)}_latency_vs_injection.png")
            fig.savefig(out_lat, dpi=300, bbox_inches="tight")
            plt.close(fig)
            saved_paths.append(out_lat)

            # Latency vs Injection (log-y)
            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_latency(ax, groups_tp)
            ymin_lat = max(1e-3, float(np.nanmin(df_b["AvgTotalLatency"].replace(0, np.nan))))
            ax.set_yscale("log")
            ax.set_ylim(bottom=ymin_lat)
            ax.set_title(f"Latency vs Injection (log-y) — {traffic}\n{base} (Z-latency sweep)")
            out_lat_logy = os.path.join(out_dir, f"{_sanitize(base)}_{_sanitize(traffic)}_latency_vs_injection_logy.png")
            fig.savefig(out_lat_logy, dpi=300, bbox_inches="tight")
            plt.close(fig)
            saved_paths.append(out_lat_logy)

            # Latency vs Throughput
            fig, ax = plt.subplots(figsize=(12, 7))
            _plot_latency_vs_tp(ax, groups_tp)
            ax.set_title(f"Latency vs Throughput — {traffic}\n{base} (Z-latency sweep)")
            out_lvt = os.path.join(out_dir, f"{_sanitize(base)}_{_sanitize(traffic)}_latency_vs_throughput.png")
            fig.savefig(out_lvt, dpi=300, bbox_inches="tight")
            plt.close(fig)
            saved_paths.append(out_lvt)
    else:
        # Combined figures across all matching topologies; legend shows full topology
        groups_all = [( _label_for_combined(topo), g) for topo, g in df_t.groupby("Topology")]

        # Throughput vs Injection (linear)
        fig, ax = plt.subplots(figsize=(12, 7))
        _plot_throughput(ax, groups_all)
        ax.set_title(
            f"Throughput vs Injection — {traffic}\nTopologies: {', '.join(sorted(df_t['base_topo'].unique()))}"
        )
        out_tp = os.path.join(out_dir, "tsv_latency_sweep_throughput_vs_injection.png")
        fig.savefig(out_tp, dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_tp)

        # Throughput vs Injection (log-y)
        fig, ax = plt.subplots(figsize=(12, 7))
        _plot_throughput(ax, groups_all)
        ymin = max(1e-6, float(np.nanmin(df_t["Throughput"].replace(0, np.nan))))
        ax.set_yscale("log")
        ax.set_ylim(bottom=ymin)
        ax.set_title(
            f"Throughput vs Injection (log-y) — {traffic}\nTopologies: {', '.join(sorted(df_t['base_topo'].unique()))}"
        )
        out_tp_logy = os.path.join(out_dir, "tsv_latency_sweep_throughput_vs_injection_logy.png")
        fig.savefig(out_tp_logy, dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_tp_logy)

        # Latency vs Injection (linear)
        fig, ax = plt.subplots(figsize=(12, 7))
        _plot_latency(ax, groups_all)
        ax.set_title(
            f"Latency vs Injection — {traffic}\nTopologies: {', '.join(sorted(df_t['base_topo'].unique()))}"
        )
        out_lat = os.path.join(out_dir, "tsv_latency_sweep_latency_vs_injection.png")
        fig.savefig(out_lat, dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_lat)

        # Latency vs Injection (log-y)
        fig, ax = plt.subplots(figsize=(12, 7))
        _plot_latency(ax, groups_all)
        ymin_lat = max(1e-3, float(np.nanmin(df_t["AvgTotalLatency"].replace(0, np.nan))))
        ax.set_yscale("log")
        ax.set_ylim(bottom=ymin_lat)
        ax.set_title(
            f"Latency vs Injection (log-y) — {traffic}\nTopologies: {', '.join(sorted(df_t['base_topo'].unique()))}"
        )
        out_lat_logy = os.path.join(out_dir, "tsv_latency_sweep_latency_vs_injection_logy.png")
        fig.savefig(out_lat_logy, dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_lat_logy)

        # Latency vs Throughput
        fig, ax = plt.subplots(figsize=(12, 7))
        _plot_latency_vs_tp(ax, groups_all)
        ax.set_title(f"Latency vs Throughput — {traffic}")
        out_lvt = os.path.join(out_dir, "tsv_latency_sweep_latency_vs_throughput.png")
        fig.savefig(out_lvt, dpi=300, bbox_inches="tight")
        plt.close(fig)
        saved_paths.append(out_lvt)

    print("Saved:")
    for p in saved_paths:
        print(p)
    if os.path.isfile(ZLINK_SH):
        print(f"Sweep script: {ZLINK_SH}")


if __name__ == "__main__":
    main()
