11# Report on 3D Network-on-Chip Topology Contributions

This document summarizes the design, performance, and trade-offs of the custom 3D Network-on-Chip (NoC) topologies developed as part of this experiment. The analysis is based on the topology implementation files found in `configs/topologies/` and performance metrics from simulation results located in `lab4/sec1/results.csv`.

## 1. Mesh (2D)

### 1.1. Description
The `Mesh_XY.py` topology implements a standard 2D Mesh. It is a foundational, non-3D architecture where routers are arranged in a grid. Each router connects to its nearest neighbors in the North, South, East, and West directions. This topology uses dimension-ordered XY routing, where packets travel fully along the X-dimension before turning to travel along the Y-dimension. This routing is simple and deadlock-free.

### 1.2. Performance Analysis
The `results.csv` data for `Mesh_XY` serves as our baseline for 2D performance.
- **Latency & Hops**: Under `uniform_random` traffic, the average hops are around 5.3-5.5, and latency scales linearly with the injection rate, starting around 15.5 cycles and increasing past 21 cycles at high load. For `tornado` traffic, which has a more structured communication pattern, average hops are lower (~3.8), and latency stays below 14 cycles until the network nears saturation.
- **Throughput**: The network saturates at lower injection rates for stressful traffic patterns like `transpose` and `shuffle` compared to `uniform_random` or `tornado`. For instance, latency skyrockets for `transpose` traffic above a 0.2 injection rate, indicating significant network congestion.

### 1.3. Potential Issues
- **Scalability**: As the number of nodes increases, the average hop count and latency grow, making it less suitable for very large systems.
- **Bisection Bandwidth**: The bisection bandwidth is limited by the number of links crossing the center of the mesh, which can become a bottleneck.
- **Single Point of Failure**: While the network is robust, link failures can partition the network or require complex re-routing.

## 2. 3D Topologies

### 2.1. Mesh3D_XYZ

#### 2.1.1. Description
`Mesh3D_XYZ.py` extends the 2D Mesh into a third dimension (Z). Routers are arranged in a 3D grid (e.g., 4x4x4) and connected to neighbors along the X, Y, and Z axes. It uses dimension-ordered XYZ routing. Vertical links (Z-axis), representing Through-Silicon Vias (TSVs), have a configurable latency penalty (`tsv_slowdown`) to model their higher delay compared to horizontal links.

#### 2.1.2. Performance Analysis
- **Latency & Hops**: The `results.csv` data shows that `Mesh3D_XYZ` significantly reduces the average hop count compared to `Mesh_XY`. For `uniform_random` traffic, hops are consistently around 3.8, a major improvement over the ~5.3 hops in 2D. This translates to lower latency, which stays between 16 and 26 cycles under heavy load, outperforming the 2D mesh. For `tornado` traffic, the average hops are remarkably low (~2.25), with latency staying flat around 9.5 cycles, indicating high efficiency for this pattern.
- **Throughput**: The 3D mesh sustains higher throughput, saturating at higher injection rates than the 2D mesh across all traffic patterns. The additional connectivity in the Z-dimension provides more paths and better distributes traffic.

#### 2.1.3. Potential Issues
- **Cost and Complexity**: The primary drawback is the high number of TSVs required for the dense vertical connections on every router. TSVs are expensive in terms of area, power, and manufacturing complexity.
- **Thermal Issues**: The dense 3D stacking can lead to heat dissipation challenges, potentially impacting performance and reliability.

### 2.2. Torus3D

#### 2.2.1. Description
`Torus3D.py` enhances the 3D Mesh by adding wraparound links on all three dimensions (X, Y, and Z), creating a torus network. This makes the topology regular, where every node has the same view of the network.

#### 2.2.2. Performance Analysis
- **Latency & Hops**: The `results.csv` data for `Torus3D` shows a further reduction in average hops compared to `Mesh3D_XYZ`. For `uniform_random` traffic, hops are consistently around 3.0. This is due to the shortcuts provided by the wraparound links. Latency is very stable, staying around 14 cycles across a wide range of injection rates before starting to climb. For `tornado` traffic, the average hops are extremely low (~1.75) with latency flat at ~8.5 cycles, making it the best performer for this pattern.
- **Throughput**: The bisection bandwidth of a torus is double that of a mesh of the same size, leading to higher throughput. The network saturates at higher injection rates than the 3D Mesh.

#### 2.2.3. Potential Issues
- **Cabling Complexity**: The long wraparound links can be complex and costly to implement, especially in a physical 3D layout.
- **Latency of Long Links**: The wraparound links, being physically longer, may introduce higher latency, which is not fully modeled here but is a practical concern.

### 2.3. Sparse3D_Pillars & Sparse3D_Pillars_torus

#### 2.3.1. Description
These topologies (`Sparse3D_Pillars.py`, `Sparse3D_Pillars_torus.py`) aim to reduce the cost of 3D integration. Instead of providing vertical (Z-axis) links at every router, they are only placed at specific "pillar" locations (e.g., every 2nd router in X and Y). This drastically reduces the number of required TSVs. The `_torus` variant adds wraparound links in the X and Y dimensions.

#### 2.3.2. Performance Analysis
- **Latency & Hops**: The `results.csv` data for `Sparse3D_Pillars` shows that under `uniform_random` traffic, the average hop count is around 4.1-4.2, which is higher than the dense `Mesh3D_XYZ` (~3.8) but still better than the 2D `Mesh_XY` (~5.3). This demonstrates a good balance between cost and performance. However, the network saturates very quickly, with latency exploding at injection rates above 0.2. The `_torus` variant (`Sparse3D_Pillars_torus`) brings the average hops down to ~3.4 and shows better latency characteristics, indicating the wraparound links are effective.
- **Throughput**: The sparse vertical connectivity creates potential bottlenecks. Packets must travel horizontally to a pillar to move vertically. The `results.csv` data confirms this, showing that `Sparse3D_Pillars` has a low saturation point. The `_torus` version improves this by providing more routing flexibility on the horizontal planes.

#### 2.3.3. Potential Issues
- **Congestion at Pillars**: The pillar routers are critical points for all inter-layer traffic, making them prone to congestion.
- **Routing Complexity**: Routing is more complex than in a simple mesh, as packets must first be routed towards a pillar to travel vertically. The provided implementations use a weight-based system to guide packets.

### 2.4. Cluster3D_Hub

#### 2.4.1. Description
`Cluster3D_Hub.py` implements a hierarchical topology. Each layer is divided into 2x2 clusters of routers. Within each cluster, the four routers connect to a central, high-speed "Hub Router" (HBR). Vertical connections (TSVs) exist only between the HBRs of adjacent layers. This further reduces TSV count compared to the sparse pillar design.

#### 2.4.2. Performance Analysis
- **Latency & Hops**: According to `results.csv`, `Cluster3D_Hub` has a relatively high average hop count of ~4.3-4.5 for `uniform_random` traffic, comparable to `Sparse3D_Pillars`. The two-hop trip (node -> HBR -> destination) inside each layer adds to the latency. The network also saturates very early, with latency increasing dramatically above a 0.3 injection rate.
- **Throughput**: The HBRs are significant potential bottlenecks, as all traffic进出 a cluster must pass through them. This limits the overall throughput, as confirmed by the early saturation seen in the results.

#### 2.4.3. Potential Issues
- **Hub Bottleneck**: The central hub routers are a major point of contention and a critical weakness of this design.
- **Uneven Link Utilization**: Links connected to the hub will be heavily utilized, while direct horizontal links between non-hub routers do not exist, leading to inefficient routing for some traffic patterns.

### 2.5. Hier3D_Chiplet

#### 2.5.1. Description
`Hier3D_Chiplet.py` models a chiplet-based 3D architecture. The global 3D mesh is partitioned into smaller `chiplet` blocks. Connections within a chiplet are standard mesh links. Connections between chiplets are restricted to designated "Gateway" (GW) routers, which form a higher-level backbone network. This is a highly realistic model for modern chiplet-based designs.

#### 2.5.2. Performance Analysis
- **Latency & Hops**: The `results.csv` data for `Hier3D_Chiplet` shows an average hop count of ~4.2 for `uniform_random` traffic, similar to the other sparse/hierarchical designs. The latency profile is also similar, showing early saturation past a 0.3 injection rate. This is expected, as inter-chiplet communication must go through the GWs.
- **Throughput**: The gateways are bottlenecks, limiting inter-chiplet bandwidth. The performance is highly dependent on the number and placement of these gateways. The results suggest that for uniform traffic, the performance is comparable to `Cluster3D_Hub`.

#### 2.5.3. Potential Issues
- **Gateway Congestion**: Similar to the hub/pillar models, the gateway routers are points of congestion. The number of gateways per chiplet (`GW_PER_CHIPLET`) is a critical design parameter.
- **Partitioning**: The performance is highly dependent on the traffic pattern. For applications with high locality (intra-chiplet communication), it will perform well. For applications with scattered communication (inter-chiplet), performance will be limited by the gateway bandwidth.

### 2.6. SW3D_Express & PillarTorusExpress3D

#### 2.6.1. Description
These two topologies augment a base 3D network with "express" links that skip over multiple routers.
- `SW3D_Express.py`: Adds long-distance express links in all three dimensions (X, Y, Z) to a base 3D Mesh. These links are added based on a regular rule (every `EXP_K` routers).
- `PillarTorusExpress3D.py`: A hybrid design that combines a sparse pillar-based Z-axis, a torus in the X/Y planes, and additional express links in X and Y that are only located at the pillar coordinates.

#### 2.6.2. Performance Analysis
- **Latency & Hops**: The express links provide shortcuts that significantly reduce hop count. `SW3D_Express` achieves an average hop count of ~3.1 for `uniform_random` traffic, which is a substantial improvement over the standard 3D Mesh (~3.8). `PillarTorusExpress3D` also performs well, with hops around ~3.4, demonstrating the combined benefit of torus links and express lanes.
- **Throughput**: By offloading long-distance traffic to express links, these topologies reduce congestion on the base mesh links, leading to higher saturation points. The `results.csv` data shows they maintain low latency at higher injection rates compared to the non-express variants.

#### 2.6.3. Potential Issues
- **Radix**: The express links increase the number of ports (radix) required on the routers where they connect, increasing router complexity and cost.
- **Routing**: These topologies rely on weight-based routing in `TABLE_` mode to prioritize the express links. This works well but may not be as flexible as fully adaptive routing algorithms.

## 3. Summary of Contributions & Conclusion

The experiment involved the implementation and evaluation of a diverse set of 3D NoC topologies, moving from simple, well-known architectures to more complex, cost-aware, and hierarchical designs.

- **Baseline Architectures (`Mesh_XY`, `Mesh3D_XYZ`, `Torus3D`)**: These were implemented to establish performance baselines and confirm the fundamental benefits of 3D integration (lower latency, higher throughput) and torus optimizations.
- **Cost-Aware Sparse Topologies (`Sparse3D_Pillars`, `Cluster3D_Hub`)**: These designs directly address the high cost of TSVs by creating networks with sparse vertical connectivity. The results show a clear trade-off: TSV count is reduced, but at the cost of creating performance bottlenecks at the pillar/hub locations, leading to earlier network saturation.
- **Hierarchical Topology (`Hier3D_Chiplet`)**: This provides a realistic model for modern chiplet-based systems. Its performance characteristics are similar to the sparse topologies, highlighting the critical role of gateway bandwidth in such systems.
- **Express Link Topologies (`SW3D_Express`, `PillarTorusExpress3D`)**: These explore the use of long-distance shortcut links to reduce average latency and improve throughput. The results confirm that even a small number of express links can significantly boost performance, offering a compelling alternative to dense, uniform meshes.

In conclusion, this work provides a comprehensive analysis of the design space for 3D NoCs. While a dense `Torus3D` offers the best raw performance, its cost is likely prohibitive. The sparse and hierarchical designs present more practical, cost-effective alternatives, though their performance is highly sensitive to traffic patterns and can suffer from congestion at key choke points. The express-link topologies emerge as a promising middle ground, achieving near-torus-level latency with a more constrained link budget.
