# Cluster3D.py — 3D Cluster Mesh (4x4x4, 2x2 per cluster; hubs have no NI)
# 放到: configs/network/topologies/Cluster3D.py

from m5.params import *
from m5.objects import *

from topologies.BaseTopology import SimpleTopology
from common import FileSystemConfig


class Cluster3D(SimpleTopology):
    description = "3D Cluster Mesh (4x4x4, 2x2 per cluster; hubs have no NI)"

    def __init__(self, controllers):
        # controllers (CPUs + Dirs + DMA ...) from Ruby.py
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        # -----------------------------
        # Geometry (fixed for this file)
        # -----------------------------
        X, Y, Z = 4, 4, 4  # 4x4 per layer, 4 layers
        N_HR = X * Y * Z  # = 64 horizontal routers
        assert (
            options.num_cpus == N_HR
        ), "Please run with --num-cpus={} for 4x4x4 Cluster3D".format(N_HR)

        # Cluster size 2x2 per hub; 4 hubs per layer, 16 hubs total
        CLW, CLH = 2, 2
        hubs_per_layer = (X // CLW) * (Y // CLH)  # 4
        N_HUB = hubs_per_layer * Z  # 16
        N_TOTAL = N_HR + N_HUB  # 80 routers in total

        # Default latencies (can be overridden via cmdline)
        link_latency = options.link_latency
        router_latency = options.router_latency

        # Horizontal vs. intra-cluster vs. TSV latency
        H_LAT = link_latency
        HUB_LAT = max(1, link_latency - 1)  # member <-> hub
        TSV_LAT = max(1, link_latency - 2)  # hub <-> hub (vertical)

        # -------------------
        # Create routers
        # -------------------
        routers = [
            Router(router_id=i, latency=router_latency) for i in range(N_TOTAL)
        ]
        network.routers = routers

        # -------------------
        # External links
        # -------------------
        # Round-robin map ALL controllers to the FIRST N_HR routers (horizontal routers).
        # Hub routers (index >= N_HR) do NOT get external links.
        ext_links = []
        link_count = 0
        for i, cn in enumerate(nodes):
            rid = i % N_HR
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=cn,
                    int_node=routers[rid],
                    latency=link_latency,
                )
            )
            link_count += 1
        network.ext_links = ext_links

        # -------------------
        # Helpers
        # -------------------
        def hr_idx(xi, yi, zi):
            """Index of horizontal router at (x,y,z)."""
            return zi * (X * Y) + yi * X + xi

        def hub_idx(cxi, cyi, zi):
            """Index of hub router at (cluster_x, cluster_y, z)."""
            return N_HR + zi * hubs_per_layer + (cyi * (X // CLW) + cxi)

        # -------------------
        # Internal links
        # -------------------
        int_links = []

        # 1) Horizontal mesh among horizontal routers (like Mesh_XY)
        #    East/West weight=1, South/North weight=2 (用于 XY DOR 的权重约束)
        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    a = routers[hr_idx(x, y, z)]
                    # East <-> West
                    if x + 1 < X:
                        b = routers[hr_idx(x + 1, y, z)]
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=a,
                                dst_node=b,
                                src_outport="East",
                                dst_inport="West",
                                latency=H_LAT,
                                weight=1,
                            )
                        )
                        link_count += 1
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=b,
                                dst_node=a,
                                src_outport="West",
                                dst_inport="East",
                                latency=H_LAT,
                                weight=1,
                            )
                        )
                        link_count += 1
                    # South <-> North
                    if y + 1 < Y:
                        b = routers[hr_idx(x, y + 1, z)]
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=a,
                                dst_node=b,
                                src_outport="South",
                                dst_inport="North",
                                latency=H_LAT,
                                weight=2,
                            )
                        )
                        link_count += 1
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=b,
                                dst_node=a,
                                src_outport="North",
                                dst_inport="South",
                                latency=H_LAT,
                                weight=2,
                            )
                        )
                        link_count += 1

        # 2) Intra-cluster member <-> hub (bidirectional) —— 带唯一端口名
        for z in range(Z):
            for cy in range(Y // CLH):
                for cx in range(X // CLW):
                    hub_r = routers[hub_idx(cx, cy, z)]
                    for dy in range(CLH):
                        for dx in range(CLW):
                            mx = cx * CLW + dx
                            my = cy * CLH + dy
                            mem_r = routers[hr_idx(mx, my, z)]

                            # 唯一端口名（避免端口名冲突/共享）
                            m2h_out = f"ToHub_c{cx}_{cy}_z{z}_from{mx}_{my}"
                            h2m_in = f"FromMem_{mx}_{my}_z{z}_toHub_c{cx}_{cy}"
                            h2m_out = (
                                f"ToMem_{mx}_{my}_z{z}_fromHub_c{cx}_{cy}"
                            )
                            m2h_in = f"FromHub_c{cx}_{cy}_z{z}_to{mx}_{my}"

                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=mem_r,
                                    dst_node=hub_r,
                                    src_outport=m2h_out,
                                    dst_inport=h2m_in,
                                    latency=HUB_LAT,
                                    weight=3,
                                )
                            )
                            link_count += 1

                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=hub_r,
                                    dst_node=mem_r,
                                    src_outport=h2m_out,
                                    dst_inport=m2h_in,
                                    latency=HUB_LAT,
                                    weight=3,
                                )
                            )
                            link_count += 1

        # 3) Vertical TSV hub <-> hub (bidirectional) —— 也带唯一端口名
        for z in range(Z - 1):
            for cy in range(Y // CLH):
                for cx in range(X // CLW):
                    a = routers[hub_idx(cx, cy, z)]
                    b = routers[hub_idx(cx, cy, z + 1)]

                    a2b_out = f"ZPlus_c{cx}_{cy}_z{z}"
                    b2a_in = f"ZMinus_c{cx}_{cy}_z{z+1}"
                    b2a_out = f"ZMinus_c{cx}_{cy}_z{z+1}"
                    a2b_in = f"ZPlus_c{cx}_{cy}_z{z}"

                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=a,
                            dst_node=b,
                            src_outport=a2b_out,
                            dst_inport=b2a_in,
                            latency=TSV_LAT,
                            weight=3,
                        )
                    )
                    link_count += 1

                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=b,
                            dst_node=a,
                            src_outport=b2a_out,
                            dst_inport=a2b_in,
                            latency=TSV_LAT,
                            weight=3,
                        )
                    )
                    link_count += 1

        network.int_links = int_links

    # Register nodes with filesystem (same style as Mesh_XY; optional)
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
