#!/usr/bin/env python3
"""
Built-in-config plotting for gem5 Garnet topology comparison.
Input CSV must have (header row): Topology,Traffic,InjectionRate,Throughput,PacketsInjected,PacketsReceived,AvgTotalLatency,AvgHops

Outputs go to OUTDIR:
- throughput_vs_injection_<TRAFFIC>.png
- latency_vs_injection_<TRAFFIC>.png
- latency_vs_throughput_<TRAFFIC>.png
- facet_throughput_vs_injection.png
- peak_throughput_heatmap.png
- knee_injection_rate_heatmap.png
- results_summary.csv
- inj_rate_vs_throughput_<TOPOLOGY>.png (NEW)
- throughput_vs_latency_<TOPOLOGY>.png (NEW)
"""

import os
import sys
import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import warnings

# ============================== CONFIG ======================================
CSV_FILE = "./lab4/sec2/results.csv"
OUTDIR = "./lab4/sec2/plots"
DPI = 300

# Knee = first InjectionRate where latency >= KNEE_FACTOR * low-load latency
KNEE_FACTOR = 2.0

# Optional filters (set to [] or None to use all)
FILTER_TRAFFIC = None  # e.g., ["uniform_random", "transpose"]
FILTER_TOPOLOGY = None  # e.g., ["Mesh_XY", "Torus_XY"]

# Show interactive windows? (usually False for batch)
SHOW = False

# ============================================================================

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    
    # Backward-compat column aliases
    rename_map = {}
    if "SentPackets" in df.columns and "PacketsInjected" not in df.columns:
        rename_map["SentPackets"] = "PacketsInjected"
    if "ReceivedPackets" in df.columns and "PacketsReceived" not in df.columns:
        rename_map["ReceivedPackets"] = "PacketsReceived"
    if (
        "AvgPacketLatency" in df.columns
        and "AvgTotalLatency" not in df.columns
    ):
        rename_map["AvgPacketLatency"] = "AvgTotalLatency"
    
    if rename_map:
        df = df.rename(columns=rename_map)
    
    if "Topology" not in df.columns:
        # If older CSVs didn't include topology, assume Mesh_XY to keep plotting usable
        df["Topology"] = "Mesh_XY"
    
    required = [
        "Topology",
        "Traffic",
        "InjectionRate",
        "Throughput",
        "PacketsInjected",
        "PacketsReceived",
        "AvgTotalLatency",
        "AvgHops",
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
        "PacketsInjected",
        "PacketsReceived",
        "AvgTotalLatency",
        "AvgHops",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    df = df.dropna(subset=["InjectionRate", "Throughput", "AvgTotalLatency"])
    return df


def maybe_filter(df: pd.DataFrame) -> pd.DataFrame:
    if FILTER_TRAFFIC:
        keep = set([t.strip() for t in FILTER_TRAFFIC if str(t).strip()])
        df = df[df["Traffic"].isin(keep)]
    if FILTER_TOPOLOGY:
        keep = set([t.strip() for t in FILTER_TOPOLOGY if str(t).strip()])
        df = df[df["Topology"].isin(keep)]
    return df


def safe_name(s: str) -> str:
    return str(s).replace(" ", "_").replace("/", "_")


def summarize(df: pd.DataFrame, knee_factor: float) -> pd.DataFrame:
    rows = []
    for (traffic, topo), g in df.groupby(["Traffic", "Topology"], sort=True):
        g = g.sort_values("InjectionRate")
        
        # Peak throughput
        idx_peak = g["Throughput"].idxmax()
        peak_tp = g.loc[idx_peak, "Throughput"]
        inj_at_peak = g.loc[idx_peak, "InjectionRate"]
        
        # Low-load latency at min inj
        min_inj = g["InjectionRate"].min()
        low_latency = g.loc[g["InjectionRate"].idxmin(), "AvgTotalLatency"]
        
        # Knee
        knee_thresh = low_latency * knee_factor
        knee_rows = g[g["AvgTotalLatency"] >= knee_thresh]
        knee_inj = (
            knee_rows["InjectionRate"].min()
            if not knee_rows.empty
            else np.nan
        )
        
        rows.append(
            {
                "Traffic": traffic,
                "Topology": topo,
                "PeakThroughput": peak_tp,
                "InjectionAtPeakTP": inj_at_peak,
                "LowLoadInjection": float(min_inj),
                "LowLoadLatency": float(low_latency),
                "KneeFactor": knee_factor,
                "KneeInjectionRate": (
                    float(knee_inj) if not pd.isna(knee_inj) else np.nan
                ),
            }
        )
    
    return pd.DataFrame(rows).sort_values(["Traffic", "Topology"])


# NEW FUNCTION: Injection Rate vs Throughput (by Topology)
def plot_inj_rate_vs_throughput_by_topology(df, topology, outdir, dpi):
    """
    Plot Injection Rate vs Throughput for a specific topology.
    Different traffic patterns (flow patterns) are shown in the same plot.
    """
    plt.figure(figsize=(8, 6))
    
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
        f"Injection Rate vs Throughput — {topology}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Throughput (accepted pkts/node/cycle)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Traffic Pattern")
    
    fn = os.path.join(
        outdir, f"inj_rate_vs_throughput_{safe_name(topology)}.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


# NEW FUNCTION: Throughput vs Latency (by Topology)
def plot_throughput_vs_latency_by_topology(df, topology, outdir, dpi):
    """
    Plot Throughput vs Latency for a specific topology.
    Different traffic patterns (flow patterns) are shown in the same plot.
    """
    plt.figure(figsize=(8, 6))
    
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
        f"Throughput vs Latency — {topology}",
        fontsize=16,
        fontweight="bold"
    )
    plt.xlabel("Throughput (accepted pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    plt.ylim(bottom=0)
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Traffic Pattern")
    
    fn = os.path.join(
        outdir, f"throughput_vs_latency_{safe_name(topology)}.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


# EXISTING FUNCTIONS (keeping all the original functionality)
def plot_throughput_vs_injection(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="InjectionRate",
        y="Throughput",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Throughput vs Injection Rate — {traffic}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Throughput (accepted pkts/node/cycle)")
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    fn = os.path.join(
        outdir, f"throughput_vs_injection_{safe_name(traffic)}.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_latency_vs_injection(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="InjectionRate",
        y="AvgTotalLatency",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Latency vs Injection Rate — {traffic}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    plt.ylim(bottom=0)
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    # Annotate knee (first inj where latency >= 2x low-load)
    try:
        for topo, g in df.groupby("Topology"):
            g = g.sort_values("InjectionRate")
            low = g.loc[g["InjectionRate"].idxmin(), "AvgTotalLatency"]
            thresh = low * KNEE_FACTOR
            knees = g[g["AvgTotalLatency"] >= thresh]
            if not knees.empty:
                knee_inj = float(knees["InjectionRate"].min())
                plt.axvline(
                    knee_inj,
                    color="black",
                    linestyle="--",
                    alpha=0.3,
                )
                plt.text(
                    knee_inj,
                    plt.gca().get_ylim()[1] * 0.85,
                    f"knee {topo}\n@ {knee_inj:.3f}",
                    rotation=90,
                    va="top",
                    ha="right",
                    fontsize=8,
                    alpha=0.7,
                )
    except Exception as e:
        warnings.warn(f"Knee annotation skipped: {e}")
    
    fn = os.path.join(outdir, f"latency_vs_injection_{safe_name(traffic)}.png")
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_latency_vs_throughput(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="Throughput",
        y="AvgTotalLatency",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Latency vs Throughput — {traffic}",
        fontsize=16,
        fontweight="bold"
    )
    plt.xlabel("Throughput (accepted pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    plt.ylim(bottom=0)
    plt.grid(True, linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    fn = os.path.join(
        outdir, f"latency_vs_throughput_{safe_name(traffic)}.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_throughput_vs_injection_logy(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="InjectionRate",
        y="Throughput",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Throughput vs Injection Rate (log-y) — {traffic}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Throughput (accepted pkts/node/cycle)")
    ymin = max(1e-6, float(np.nanmin(df["Throughput"].replace(0, np.nan))))
    plt.yscale("log")
    plt.ylim(bottom=ymin)
    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    fn = os.path.join(
        outdir, f"throughput_vs_injection_{safe_name(traffic)}_logy.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_latency_vs_injection_logy(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="InjectionRate",
        y="AvgTotalLatency",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Latency vs Injection Rate (log-y) — {traffic}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Injection Rate (pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    ymin = max(
        1e-3, float(np.nanmin(df["AvgTotalLatency"].replace(0, np.nan)))
    )
    plt.yscale("log")
    plt.ylim(bottom=ymin)
    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    fn = os.path.join(
        outdir, f"latency_vs_injection_{safe_name(traffic)}_logy.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_latency_vs_throughput_logy(df, traffic, outdir, dpi):
    plt.figure(figsize=(12, 7))
    sns.lineplot(
        data=df,
        x="Throughput",
        y="AvgTotalLatency",
        hue="Topology",
        style="Topology",
        markers=True,
        dashes=False,
    )
    plt.title(
        f"Latency vs Throughput (log-y) — {traffic}",
        fontsize=16,
        fontweight="bold",
    )
    plt.xlabel("Throughput (accepted pkts/node/cycle)")
    plt.ylabel("Average Packet Latency (cycles)")
    ymin = max(
        1e-3, float(np.nanmin(df["AvgTotalLatency"].replace(0, np.nan)))
    )
    plt.yscale("log")
    plt.ylim(bottom=ymin)
    plt.grid(True, which="both", linestyle="--", alpha=0.4)
    plt.legend(title="Topology")
    
    fn = os.path.join(
        outdir, f"latency_vs_throughput_{safe_name(traffic)}_logy.png"
    )
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def facet_throughput_vs_injection(df, outdir, dpi):
    g = sns.FacetGrid(
        df,
        col="Traffic",
        hue="Topology",
        sharex=True,
        sharey=True,
        height=4,
        aspect=1.2,
    )
    g.map_dataframe(
        sns.lineplot, x="InjectionRate", y="Throughput", marker="o"
    )
    g.add_legend(title="Topology")
    g.set_axis_labels("Injection Rate", "Throughput")
    g.set_titles(col_template="{col_name}")
    
    for ax in g.axes.flat:
        ax.grid(True, linestyle="--", alpha=0.4)
    
    fn = os.path.join(outdir, "facet_throughput_vs_injection.png")
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_peak_tp_heatmap(summary_df, outdir, dpi):
    pivot = summary_df.pivot(
        index="Topology", columns="Traffic", values="PeakThroughput"
    )
    plt.figure(
        figsize=(
            1.2 * max(6, len(pivot.columns)),
            0.9 * max(5, len(pivot.index)),
        )
    )
    sns.heatmap(
        pivot,
        annot=True,
        fmt=".3f",
        linewidths=0.5,
        cbar_kws={"label": "Peak Throughput"},
    )
    plt.title(
        "Peak Throughput Heatmap (Topology × Traffic)",
        fontsize=16,
        fontweight="bold",
    )
    plt.ylabel("Topology")
    plt.xlabel("Traffic")
    
    fn = os.path.join(outdir, "peak_throughput_heatmap.png")
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def plot_knee_heatmap(summary_df, outdir, dpi):
    pivot = summary_df.pivot(
        index="Topology", columns="Traffic", values="KneeInjectionRate"
    )
    plt.figure(
        figsize=(
            1.2 * max(6, len(pivot.columns)),
            0.9 * max(5, len(pivot.index)),
        )
    )
    sns.heatmap(
        pivot,
        annot=True,
        fmt=".3f",
        linewidths=0.5,
        cbar_kws={"label": "Knee Injection Rate"},
    )
    plt.title(
        "Knee (Latency Blow-up) Injection Rate Heatmap",
        fontsize=16,
        fontweight="bold",
    )
    plt.ylabel("Topology")
    plt.xlabel("Traffic")
    
    fn = os.path.join(outdir, "knee_injection_rate_heatmap.png")
    plt.savefig(fn, dpi=dpi, bbox_inches="tight")
    if SHOW:
        plt.show()
    plt.close()
    return fn


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    
    # Style
    sns.set_theme(style="whitegrid", palette="deep")
    
    # Load CSV (assumes header row exists)
    try:
        df = pd.read_csv(CSV_FILE)
    except FileNotFoundError:
        print(f"Error: File not found: {CSV_FILE}")
        sys.exit(1)
    
    df = ensure_columns(df)
    df = to_numeric(df)
    df = maybe_filter(df)
    
    if df.empty:
        print("No data after filtering. Check CSV/filters.")
        sys.exit(0)
    
    # Sort for nice lines
    df = df.sort_values(["Traffic", "Topology", "InjectionRate"]).reset_index(
        drop=True
    )
    
    # Summary metrics & save
    summary_df = summarize(df, knee_factor=KNEE_FACTOR)
    summary_path = os.path.join(OUTDIR, "results_summary.csv")
    summary_df.to_csv(summary_path, index=False)
    
    saved = []
    
    # Per-traffic comparison (topologies as hue) - EXISTING PLOTS
    for traffic, g in df.groupby("Traffic", sort=True):
        saved.append(plot_throughput_vs_injection(g, traffic, OUTDIR, DPI))
        saved.append(
            plot_throughput_vs_injection_logy(g, traffic, OUTDIR, DPI)
        )
        saved.append(plot_latency_vs_injection(g, traffic, OUTDIR, DPI))
        saved.append(plot_latency_vs_injection_logy(g, traffic, OUTDIR, DPI))
        saved.append(plot_latency_vs_throughput(g, traffic, OUTDIR, DPI))
        saved.append(plot_latency_vs_throughput_logy(g, traffic, OUTDIR, DPI))
    
    # NEW: Per-topology comparison (traffic patterns as hue)
    for topology, g in df.groupby("Topology", sort=True):
        saved.append(plot_inj_rate_vs_throughput_by_topology(g, topology, OUTDIR, DPI))
        saved.append(plot_throughput_vs_latency_by_topology(g, topology, OUTDIR, DPI))
    
    # Aggregated visuals
    saved.append(facet_throughput_vs_injection(df, OUTDIR, DPI))
    saved.append(plot_peak_tp_heatmap(summary_df, OUTDIR, DPI))
    saved.append(plot_knee_heatmap(summary_df, OUTDIR, DPI))
    
    print("\n=== Saved Figures ===")
    for s in saved:
        print(s)
    print(f"\nSummary CSV: {summary_path}")
    
    # Quick highlights: best topology per traffic
    print("\n=== Peak Throughput by Traffic/Topology ===")
    for traffic, g in summary_df.groupby("Traffic"):
        best = g.sort_values("PeakThroughput", ascending=False).iloc[0]
        knee = (
            f"{best.KneeInjectionRate:.3f}"
            if not math.isnan(best.KneeInjectionRate)
            else "N/A"
        )
        print(
            f"{traffic:>16}: best {best.Topology} @ {best.PeakThroughput:.4f} (inj={best.InjectionAtPeakTP:.3f}) | knee @ {knee}"
        )


if __name__ == "__main__":
    main()