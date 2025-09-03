Topologies Review and TSV Slowdown Integration

Summary
- Added a new command-line knob `--vlink-slowdown` (default 4) in `configs/network/Network.py`.
- Applied slower Z-link latency across 3D topologies using this factor.
- Fixed external-link (NI/controller) mapping in sparse 3D pillar topologies.
- Verified coordinate systems, link directions, and weights for correctness in each topology.
- Added two new topologies: `HyperX3D` (high-performance) and `PillarTorusExpress3D` (balanced).

Usage
- Add `--vlink-slowdown K` to multiply the Z-axis (TSV) link latency by `K`.
- Example (Garnet, 4x4x4, showing RubyNetwork trace):
  ./build/NULL/gem5.debug --debug-flags=RubyNetwork --debug-file=trace.out \
  configs/example/garnet_synth_traffic.py --network=garnet --num-cpus=64 \
  --num-dirs=64 --topology=<TOPO_NAME> --mesh-rows=4 --inj-vnet=0 \
  --vcs-per-vnet=1 --synthetic=uniform_random --sim-cycles=1000 \
  --injectionrate=0.03 --escape-vc --vlink-slowdown=4

Per-Topology Review
- Mesh3D_XYZ
  - Geometry: fixed 4x4x4; router id = z*(X*Y) + y*X + x.
  - Links: X(E/W), Y(N/S), Z(Up/Down); weights X=1, Y=2, Z=3.
  - Change: Z links now use `link_latency * --vlink-slowdown`.
  - Status: Correct and XYZ/DOR compatible. External links distributed uniformly with standard remainder-to-router0 mapping.

- Torus3D
  - Geometry: fixed 4x4x4; wraparound in X, Y, Z; same id mapping as above.
  - Links: X(E/W), Y(N/S) wrap; Z(Up/Down) wrap; weights X=1, Y=2, Z=3.
  - Change: Z links now use `link_latency * --vlink-slowdown`.
  - Status: Correct wraparound semantics. Uniform NI mapping with remainder-to-router0 preserved.

- Sparse3D_Pillars
  - Geometry: fixed 4x4x4; pillars at (x % PX==0, y % PY==0) with Z-links only on pillar coordinates; no wrap.
  - Links: full 2D mesh in X/Y; Z only on pillars. Weights X=1, Y=2, Z=3.
  - Fix: External-link mapping now follows standard distribution using `cntrls_per_router` with remainder-to-router0. Previously it round-robined all nodes and duplicated remainder links.
  - Change: Z links use `link_latency * --vlink-slowdown`.
  - Status: Correct sparse vertical connectivity and proper NI mapping.

- Sparse3D_Pillars_torus
  - Geometry: fixed 4x4x4; torus wrap in X and Y; Z links only at pillars with Z-wrap.
  - Links: as above; weights X=1, Y=2, Z=3.
  - Fix: External-link mapping updated to standard distribution (same bug as above).
  - Change: Z links use `link_latency * --vlink-slowdown`.
  - Status: Correct wraparound + sparse Z with proper NI mapping.

- Hier3D_Chiplet
  - Geometry: X=Y=`--mesh-rows`; Z inferred from `--num-cpus/(X*Y)`; partitioned into chiplets (CHIP_X/Y/Z) with intra-chiplet links and inter-chiplet backbones via gateway (GW) routers. Weights: intra=1, backbone(X/Y)=2, vertical backbone=3 (strict order to guide TABLE_ routes).
  - Change: All Up/Down paths (intra-chiplet and GW vertical) now use `link_latency * --vlink-slowdown`.
  - Status: Correct chiplet partitioning, gateway routing, and vertical latency modeling.

- SW3D_Express
  - Base: 3D mesh (no wrap), id = z*(X*Y) + y*X + x; base weights X=11, Y=12, Z=13; express weights X=1, Y=2, Z=3 (express strictly preferred).
  - Express placement: every `EXP_K` in both coordinates; spans `EXP_SPAN_*`. Ports use distinct names (*Exp) to avoid conflicts.
  - Change: Base Z latency uses `link_latency * --vlink-slowdown`. Z-Express latency uses `(link_latency * --vlink-slowdown) // EXP_LINK_SPEEDUP`. X/Y express still use `link_latency // EXP_LINK_SPEEDUP`.
- Status: Correct base + express layering with appropriate priority and TSV penalty.

New Designs (Cutting-Edge + Balanced)
- HyperX3D (High Performance)
  - Concept: HyperX/flattened-butterfly style for on-chip 3D. In each dimension, all routers that differ only in that coordinate are fully connected, forming cliques per dimension. Very low diameter and high bisection bandwidth at the cost of higher radix per router.
  - Implementation: `configs/topologies/HyperX3D.py`
    - Dimensions inferred from `--mesh-rows` (and optional `--mesh-cols`); `Z` inferred from `--num-cpus/(X*Y)`.
    - TABLE_ weights X=1, Y=2, Z=3 to preserve deterministic minimal routing.
    - Z links use `link_latency * --vlink-slowdown`.
    - Unique port names per link avoid conflicts (e.g., `X_to_<x2>`, `X_from_<x1>`).
  - When to use: Latency-sensitive, bandwidth-heavy workloads where additional router radix is acceptable. Provides near-minimal hop counts across the 3D space.

- PillarTorusExpress3D (Cost-Performance Balanced)
  - Concept: Start from a TSV-efficient pillar topology. Improve planar connectivity with torus wraps and add sparse XY express only at pillar coordinates. This reduces average path length and hotspots with modest extra links and minimal new TSVs.
  - Implementation: `configs/topologies/PillarTorusExpress3D.py`
    - Base links: torus X/Y; sparse Z (pillars only). Weights: X=11, Y=12, Z=13.
    - Express XY: short-span links originating only at pillar coordinates (every `EXP_K`), with weights `W_EXP_X=1`, `W_EXP_Y=2` so TABLE_ prefers them.
    - Latency: Z uses `--vlink-slowdown`; XY express can optionally be lower latency via `EXP_LINK_SPEEDUP`.
  - When to use: Environments where TSVs are expensive but better planar connectivity is needed. Balanced port count and cost with good performance gains.

Notes on Literature and Rationale
- HyperX/Flattened Butterfly: Well-known low-diameter high-radix network families (e.g., HyperX, flattened-butterfly) deliver strong performance by turning dimension lines into cliques, improving bisection and path diversity.
- Torus with Express: Adding a limited set of long-range links (express) reduces average distance without fully incurring the degree cost of HyperX. Restricting express to pillar nodes lowers area/power overhead.
- 3D TSV Penalty: TSV-based verticals remain slower; we reflect this via latency scaling (`--vlink-slowdown`) rather than weight changes so routing remains minimal while delay realism is preserved.

How to Run (Examples)
- HyperX3D (64 nodes, 4x4x4):
  ./build/NULL/gem5.debug --debug-flags=RubyNetwork --debug-file=trace.out \
  configs/example/garnet_synth_traffic.py --network=garnet --num-cpus=64 \
  --num-dirs=64 --topology=HyperX3D --mesh-rows=4 --inj-vnet=0 \
  --vcs-per-vnet=1 --synthetic=uniform_random --sim-cycles=1000 \
  --injectionrate=0.03 --escape-vc --vlink-slowdown=4

- PillarTorusExpress3D (64 nodes, 4x4x4):
  ./build/NULL/gem5.debug --debug-flags=RubyNetwork --debug-file=trace.out \
  configs/example/garnet_synth_traffic.py --network=garnet --num-cpus=64 \
  --num-dirs=64 --topology=PillarTorusExpress3D --mesh-rows=4 --inj-vnet=0 \
  --vcs-per-vnet=1 --synthetic=uniform_random --sim-cycles=1000 \
  --injectionrate=0.03 --escape-vc --vlink-slowdown=4

Expected Outcomes
- HyperX3D: Lower average hops and improved throughput/latency vs. Mesh/Torus, especially under high load or skewed traffic; higher radix and wire cost per router.
- PillarTorusExpress3D: Material improvement over Sparse3D pillar baselines and Torus3D, with limited extra wires and unchanged TSV count; good cost-performance.

Design Notes
- Weights follow the standard increasing order per dimension (X<Y<Z) for TABLE_ routing to emulate DOR. Unique port names prevent unintended port sharing.
- Z links model TSV being slower; we only adjust latency, not weights. This reflects physical delay while retaining routing preferences.
- External-link mapping now consistently follows gem5/garnet convention: even distribution across routers, with leftover (DMAs) homed to router 0.

Expected Effects of --vlink-slowdown
- Throughput/latency: Workloads with heavy inter-layer (Z) traffic will see reduced throughput and higher latency as slowdown increases. X/Y-dominated patterns change minimally.
- Topology sensitivity:
  - Mesh3D_XYZ/Torus3D: Global impact proportional to Z-traffic fraction.
  - Sparse3D_Pillars(_torus): Stronger sensitivity to pillar placement; vertical contention concentrated on pillars.
  - Hier3D_Chiplet: Vertical backbone becomes a clear bottleneck; intra-chiplet traffic lightly affected.
  - SW3D_Express: Z-Express helps, but TSV penalty still applies; X/Y express unaffected.

Validation Tips
- Baseline vs TSV slowdown: compare `--vlink-slowdown=1` to higher values.
- Synthetic mixes: uniform_random, hotspot, transpose; sweep `--injectionrate`.
- Deadlock: no deadlocks observed in inspection; use `--escape-vc` for safety in experiments.
- Tracing: enable `--debug-flags=RubyNetwork` and check `trace.out` for Up/Down hops and credits.

Files Changed
- configs/network/Network.py: added `--vlink-slowdown` option.
- configs/topologies/Mesh3D_XYZ.py: Z latency uses slowdown factor.
- configs/topologies/Torus3D.py: Z latency uses slowdown factor.
- configs/topologies/Sparse3D_Pillars.py: fixed NI mapping; Z latency uses slowdown factor.
- configs/topologies/Sparse3D_Pillars_torus.py: fixed NI mapping; Z latency uses slowdown factor.
- configs/topologies/Hier3D_Chiplet.py: unified Z latency penalty across intra/backbone.
- configs/topologies/SW3D_Express.py: Z base and Z express latencies include TSV slowdown.
