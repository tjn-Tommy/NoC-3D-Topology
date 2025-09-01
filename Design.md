Title: Adaptive Minimal 3D Routing for Garnet (gem5)

Overview
- Goal: Provide a deadlock-free, locally adaptive minimal routing policy for 3D topologies in Garnet (RubyNetwork), improving throughput/latency over static minimal routing while preserving correctness.
- Scope: Works across arbitrary topologies by reusing Garnet’s routing tables to identify minimal output candidates, then choosing adaptively using local congestion signals (credits). It integrates with the existing escape VC mechanism for safety.

Key Ideas
- Minimal set via routing table: Use the already-populated per-vnet routing table to find all outports with the minimum configured link weight that can reach the destination. This works for all supported topologies (including Sparse3D_Pillars) without requiring explicit coordinate awareness.
- Congestion metric: For each candidate outport, compute a local congestion score using downstream credit availability observed in the current router’s OutputUnit. We sum credits across the vnet’s non-escape VCs (exclude VC0 when escape is enabled) to estimate remaining buffer space downstream.
- Selection policy: Pick the candidate outport with the highest credit sum (max-free-credits heuristic). Ties are broken using a per-input-port round‑robin pointer to avoid bias and encourage path diversity.
- Escape VC integration: The algorithm does not alter the escape VC semantics. If normal VC allocation fails persistently, SwitchAllocator’s existing fallback tries the escape route/VC (VC0). The escape routing remains minimal and tree-based (up/down) and is always safe.

Why this works for 3D
- Topology-agnostic minimality: Garnet’s routing table encodes minimal reachability and link weights (e.g., via topology generator). By filtering candidates to those with minimum weight that still reach the destination, we implicitly enforce minimal paths, regardless of the network being 2D/3D or sparse/irregular.
- Local adaptivity: Using downstream credits as the congestion signal is simple, local, and effective. It prefers less congested minimal neighbors and naturally balances traffic across multiple dimensions (including “Up/Down” in 3D) whenever parallel minimal options exist.

Deadlock and Correctness
- Data VCs vs Escape VC: Adaptive selection only changes which minimal outport is used. VC allocation still uses non-escape VCs for normal traffic and switches to escape VC only when normal allocation can’t proceed. The dedicated escape VC (VC0) enforces a deadlock-free spanning tree, independent of the adaptive path, preventing cycles.
- Ordered vnets: The existing ordered‑vnet checks in SwitchAllocator remain unchanged and enforce per‑vnet ordering constraints.

Config Knob and Usage
- Add routing algorithm value 3 for “Adaptive minimal (credit-aware)”.
- Command-line example:
  ./build/NULL/gem5.debug --debug-flags=RubyNetwork --debug-file=trace.out \
    configs/example/garnet_synth_traffic.py --network=garnet --num-cpus=64 \
    --num-dirs=64 --topology=Sparse3D_Pillars --mesh-rows=4 --inj-vnet=0 \
    --vcs-per-vnet=1 --synthetic=uniform_random --sim-cycles=1000 \
    --injectionrate=0.03 --escape-vc --routing-algorithm=3

Implementation Notes
- RoutingUnit::outportComputeCustom constructs the set of minimal outport candidates from the routing table (same as TABLE_ but keeps all min‑weight links). It then evaluates congestion per candidate:
  - For route.vnet, sum OutputUnit::get_credit_count(vc) for that outport over all VCs in the vnet, excluding escape VC (index 0) when enabled.
  - Choose the outport with the largest sum. If ties, pick using a per‑inport round‑robin pointer.
  - If the destination is local, default to table lookup to select the LOCAL outport.
- SwitchAllocator already handles escape fallback when normal VC allocation fails. The adaptive routing does not modify VA/SA behavior.

Validation Plan
- Baseline: Minimal table-based (routing-algorithm=0) with/without escape VC.
- Adaptive: routing-algorithm=3 and enable --escape-vc for safety.
- Traffic patterns: uniform_random, hotspot, transpose; sweep injectionrate 0.01..0.30.
- Metrics: Compare throughput, average packet/flit latency, average hops from m5out/stats.txt. Verify no deadlock/timeouts.
- Traces: Use --debug-flags=RubyNetwork to verify path diversity and balanced credits in trace.out (e.g., different directions chosen under load).

Limitations and Future Work
- The congestion metric is purely local and credit-based; adding queue occupancy, link utilization, or multi-hop lookahead may further improve performance.
- Round‑robin is simple; a more principled history-based tie-breaker could improve stability under bursty traffic.

---

Advanced Design: CAR-3D (Congestion-Aware Routing with Lookahead)

Motivation
- Local-only credit heuristics are effective but limited, especially under bursty or adversarial patterns. Inspired by DBAR (HPCA’11) and RCA (ISCA’09), CAR‑3D targets better load balancing using two-hop lookahead and smoothed congestion estimates, while remaining minimal and escape-safe.

Key Features
- Two-hop lookahead (lightweight): Among minimal outports, prefer those whose next-hop (neighbor’s minimal options toward the same destination) appear less congested. We approximate next-hop congestion using locally maintained EWMA per-direction, derived from observed credits over time when sending on that outport (no extra wires).
- Smoothed congestion score: Per outport o, maintain EWMA C[o] of available credits per vnet (excluding escape). Score(o) = α·local_credits(o) + β·C[o]. This reduces oscillations and adds “memory” of congestion trends.
- Distance-aware tie-breaking: In case of equal scores, bias toward the dimension with larger remaining hop distance (Z/Y/X priority) while still minimal. This improves progress in deep 3D stacks.
- Stickiness with timeout: To avoid flapping, retain the last chosen outport for the same flow (src,dst,vnet) unless its score drops below a threshold or a timeout expires.
- Escape invariants preserved: All data VCs use CAR-3D scoring; persistent blockage still falls back to escape VC/route, guaranteeing deadlock freedom.

Scoring Function
- For each candidate outport o (minimal):
  - local_credits(o): Sum of free credits across non-escape VCs in the vnet at o.
  - C[o] (EWMA): C[o] ← (1−λ)·C[o] + λ·local_credits(o) sampled whenever we transmit via o.
  - dist_bias(o): small additive bias favoring directions reducing the largest component of remaining Manhattan distance.
  - score(o) = α·local_credits(o) + β·C[o] + γ·dist_bias(o) − δ·recent_failure_penalty(o).
  - Choose max score; ties → round-robin with stickiness.

Minimality and Deadlock
- Minimality: Candidate set is restricted to min-weight outports from the routing table.
- Deadlock: Escape VC unchanged; switch allocator’s fallback to escape maintains deadlock freedom.

Single-Flit, One-Credit Regime
- With single-flit packets and one credit/VC, local_credits is binary; EWMA adds valuable smoothing over time to detect persistently busy directions. Stickiness avoids churning on sparse signals.

Implementation Outline
- State additions:
  - In RoutingUnit: per-outport EWMA array `m_outport_ewma` (size = num outports), initialized to 0; per-inport round-robin and per-flow stickiness map keyed by (inport, vnet, dest_router) → last_outport, last_touch_tick.
  - Configurable params: α, β, γ, δ, λ (constants or derived from simple integers).
- Updates:
  - Update EWMA on successful sends: Hook after switch grant (or in OutputUnit insert_flit) to call back into the source router’s RoutingUnit to `updateEWMA(outport, observedCredits)`. If avoiding callbacks, perform a lazy update at next route computation using the most recent observed `get_credit_count` snapshot.
  - scoring in `outportComputeCustom`: compute candidate set (min-weight), evaluate score per outport using current local_credits and stored EWMA; apply distance bias from (myXYZ, destXYZ) if available or deducible; apply stickiness unless score gap exceeds threshold.
- Interfaces used (no new wires):
  - `m_router->getOutputUnit(outport)->get_credit_count(vc)` for local_credits.
  - Existing topology table to get minimal candidates.
- Testing hooks:
  - Debug prints of chosen outport, score terms, EWMA values; optional stats vectors per outport.

Tuning
- Suggested defaults: α=1.0, β=0.5, γ=0.1, δ=0.0, λ=0.2.
- For 1-credit, single-flit workloads, emphasize EWMA (β) and stickiness; for deeper buffers, emphasize α.
