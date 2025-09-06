#!/usr/bin/env python3
"""
Plotting for gem5 Garnet 2D/3D mesh analysis.

Input CSV must have (header row):
Topology,Traffic,InjectionRate,Throughput,PacketsInjected,PacketsReceived,AvgTotalLatency,AvgHops

Outputs go to OUTDIR, separated by topology:
- <topology>_throughput_vs_injection.png
- <topology>_latency_vs_throughput_logy.png
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import warnings

# ============================== CONFIG ======================================
CSV_FILE = "./lab4/mesh_analysis/results.csv"
OUTDIR = "./lab4/mesh_analysis/plots"
DPI = 300

# Show interactive windows? (usually False for batch)
SHOW = False
# ============================================================================


def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    required = [
        "Topology",
        "Traffic",
        "InjectionRate",
        "Throughput",
        "AvgTotalLatency",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"CSV missing columns: {missing}\nFound: {list(df.columns)}"
        )
    return df


def to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in [
        "InjectionRate",
        "Throughput",
        "AvgTotalLatency",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["InjectionRate", "Throughput", "AvgTotalLatency"])
    return df


def safe_name(s: str) -> str:
    return str(s).replace(" ", "_").replace("/", "_")


def plot_throughput_vs_injection_by_traffic(df, topology, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="InjectionRate",
        y="Throughput",
        hue="Traffic",
        style="Traffic",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Throughput vs Injection Rate — {topology}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Throughput (accepted pkts/node/cycle)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Traffic")
    fn = os.path.join(
        outdir, f"{safe_name(topology)}_throughput_vs_injection.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_latency_vs_throughput_logy_by_traffic(df, topology, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="Throughput",
        y="AvgTotalLatency",
        hue="Traffic",
        style="Traffic",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Latency vs Throughput (log-y) — {topology}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Throughput (accepted pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    ymin = 0
    try:
        ymin = max(
            1e-3, float(np.nanmin(df["AvgTotalLatency"].replace(0, np.nan)))
        )
    except (ValueError, TypeError):
        ymin = 1e-3 # fallback

    plt.yscale("log")
    
    p99 = df["AvgTotalLatency"].quantile(0.99)
    if not np.isnan(p99) and p99 > 0:
        plt.ylim(bottom=ymin, top=p99 * 2)
    else:
        plt.ylim(bottom=ymin)

    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.legend(title="Traffic")
    fn = os.path.join(
        outdir, f"{safe_name(topology)}_latency_vs_throughput_logy.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn

def main():
    os.makedirs(OUTDIR, exist_ok=True)

    # Style
    sns.set_theme(style="whitegrid", palette="deep")

    # Load CSV
    try:
        df = pd.read_csv(CSV_FILE)
    except FileNotFoundError:
        print(f"Error: File not found: {CSV_FILE}")
        sys.exit(1)

    df = ensure_columns(df)
    df = to_numeric(df)

    if df.empty:
        print("No data after cleaning. Check CSV.")
        sys.exit(0)

    # Sort for nice lines
    df = df.sort_values(["Topology", "Traffic", "InjectionRate"]).reset_index(
        drop=True
    )

    saved = []

    # Filter for 2D Mesh and plot
    df_2d = df[df["Topology"] == "Mesh_XY"]
    if not df_2d.empty:
        print("Plotting for 2D Mesh (Mesh_XY)")
        saved.append(plot_throughput_vs_injection_by_traffic(df_2d, "2D_Mesh", OUTDIR, DPI))
        saved.append(plot_latency_vs_throughput_logy_by_traffic(df_2d, "2D_Mesh", OUTDIR, DPI))
    else:
        print("No data for Mesh_XY found.")

    # Filter for 3D Mesh and plot
    df_3d = df[df["Topology"] == "Mesh3D_XYZ"]
    if not df_3d.empty:
        print("Plotting for 3D Mesh (Mesh3D_XYZ)")
        saved.append(plot_throughput_vs_injection_by_traffic(df_3d, "3D_Mesh", OUTDIR, DPI))
        saved.append(plot_latency_vs_throughput_logy_by_traffic(df_3d, "3D_Mesh", OUTDIR, DPI))
    else:
        print("No data for Mesh3D_XYZ found.")

    print("\n=== Saved Figures ===")
    for s in sorted(list(set(saved))):
        print(s)


if __name__ == "__main__":
    main()
