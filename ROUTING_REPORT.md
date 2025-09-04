# Report on Garnet Routing Algorithms

This document provides an analysis of the routing algorithms implemented within the gem5 Garnet network model, as specified in `src/mem/ruby/network/garnet/`. The focus is on the mechanisms, strengths, and weaknesses of the routing schemes corresponding to IDs 0, 3, and 4.

## 1. Algorithm 0: `TABLE_` (Static Table-Based Routing)

### 1.1. Mechanism
As defined in `RoutingUnit.cc`, `TABLE_` is the default routing scheme in Garnet. It is a static, non-adaptive algorithm that relies entirely on a pre-computed routing table.

1.  **Lookup**: When a flit arrives, the `outportCompute` function calls `lookupRoutingTable`. This function uses the flit's virtual network (vnet) ID and its `NetDest` (destination set) to find potential output ports.
2.  **Weight-Based Selection**: The topology files (`configs/topologies/*.py`) assign a `weight` to each `IntLink`. The routing unit finds all valid outports that can reach the destination and identifies the minimum weight among them.
3.  **Arbitration**: It collects all outports that match this minimum weight.
    - If the vnet is configured as *ordered*, it deterministically picks the first candidate to prevent packet reordering.
    - If the vnet is *unordered*, it randomly selects one of the minimum-weight candidates to distribute the load.

### 1.2. Strengths
- **Simplicity and Predictability**: The routing path is determined by the static table, making network behavior easy to predict and debug.
- **Flexibility**: It can support any topology and routing function, as long as the logic can be encoded into the routing table and weights. This is how complex routing schemes like dimension-ordered routing (DOR) for meshes and Up/Down routing for irregular topologies are implemented.
- **Deadlock-Free (by configuration)**: Deadlock freedom is guaranteed if the link weights are assigned correctly in the topology file to break all routing cycles (e.g., by enforcing DOR).

### 1.3. Weaknesses
- **Non-Adaptive**: The primary weakness is its inability to adapt to network congestion. It will continue sending traffic along a path even if that path becomes a bottleneck, leading to poor performance under dynamic or non-uniform traffic loads.
- **Configuration-Dependent**: The correctness and deadlock-freedom of the entire network rely solely on the manual configuration of the routing table and link weights in the topology files. Errors in this configuration can lead to deadlocks or incorrect routing.

---

## 2. Algorithm 3: `ADAPTIVE_` (Minimal Adaptive Routing)

### 2.1. Mechanism
This algorithm provides minimal adaptive routing. It aims to send flits along a minimal path (shortest path) but adapts its choice based on local congestion.

1.  **Candidate Selection**: It begins by using the same logic as `TABLE_` to identify the set of all possible outports that lie on a minimal path (i.e., all outports with the minimum weight).
2.  **Congestion-Aware Scoring**: If there are multiple minimal paths, it scores each candidate outport based on the downstream buffer availability. The score is the sum of free credits across all non-escape virtual channels for the flit's vnet at the downstream router. A higher score indicates less congestion.
3.  **Selection**: The outport with the highest credit score is chosen.
4.  **Tie-Breaking**: If multiple outports have the same highest score, a simple round-robin arbiter (`m_rr_by_inport`) is used to make the final selection.

### 2.2. Strengths
- **Congestion-Aware**: By routing traffic towards less congested paths, it can significantly improve performance and throughput for non-uniform traffic patterns compared to static routing.
- **Deadlock-Free**: Since it only ever chooses among minimal paths (which are assumed to be deadlock-free from the table weights), it does not introduce any new routing cycles and remains deadlock-free.
- **Simplicity**: The adaptiveness is based on a simple and local metric (downstream credits), making it relatively straightforward to implement.

### 2.3. Weaknesses
- **Minimal-Only**: It cannot route non-minimally. If all minimal paths are congested, it cannot choose a longer, less-congested path. This limits its effectiveness in certain complex congestion scenarios.
- **Local Information**: The routing decision is based only on the buffer status of the immediate downstream router. It has no visibility into congestion further down the path.

---

## 3. Algorithm 4: `CAR3D_` (Congestion-Aware Routing for 3D)

### 3.1. Mechanism
CAR-3D is a more sophisticated adaptive routing algorithm that enhances the `ADAPTIVE_` scheme with a more advanced scoring function that includes historical congestion data.

1.  **Candidate Selection**: It starts identically to `ADAPTIVE_`, selecting the set of minimal-path outports from the routing table.
2.  **Advanced Scoring**: It computes a score for each candidate outport using the formula: `score = alpha * localCredits + beta * ewma`.
    - `localCredits`: The same instantaneous downstream credit count used by the `ADAPTIVE_` algorithm.
    - `ewma`: An **Exponentially Weighted Moving Average** of the credit counts observed at that outport over time. This provides a smoothed, historical measure of the outport's congestion level.
    - `alpha` and `beta`: Configurable parameters to weigh the importance of instantaneous vs. historical congestion.
3.  **Selection**: The candidate with the highest score is chosen. Ties are broken by a "stickiness" mechanism: if the previously chosen outport for that flow is still in the top-scored set, it is chosen again to reduce packet re-ordering. If not, a round-robin arbiter is used.

### 3.2. Strengths
- **Advanced Congestion Awareness**: By combining instantaneous and historical congestion data, CAR-3D can make more intelligent routing decisions and is less susceptible to rapid fluctuations in network traffic.
- **Improved Stability**: The EWMA component smooths out the congestion metric, preventing the router from oscillating rapidly between paths.
- **Reduced Reordering**: The stickiness mechanism is beneficial for performance, as it reduces the likelihood of out-of-order packet arrivals at the destination.

### 3.3. Weaknesses
- **Complexity**: The algorithm is more complex, requiring state to be maintained for the EWMA of each outport/vnet pair and for the sticky tie-breaker.
- **Tuning**: The `alpha` and `beta` parameters may require tuning for optimal performance under different network conditions or traffic patterns.
- **Minimal-Only**: Like `ADAPTIVE_`, it is still a minimal routing algorithm and cannot perform non-minimal routing to escape severe congestion.
