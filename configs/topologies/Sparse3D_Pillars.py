# configs/topologies/Sparse3D_Pillars.py
# Sparse-Vertical 3D (Pillar-based) topology for Garnet 3.0

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class Sparse3D_Pillars(SimpleTopology):
    description = "Sparse3D_Pillars"

    def __init__(self, controllers):
        self.nodes = controllers

    # 判断 (x,y) 是否是“柱子坐标”

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes
        num_routers = options.num_cpus
        num_rows = options.mesh_rows

        # ----- Configuration Parameters -----
        PX, PY = 2, 2
        LAYOUT_MODE = "aligned"
        WXP, WYP, WZP = 1, 2, 3
        WXN, WYN, WZN = 1, 2, 3
        X, Y, Z = 4, 4, 4
        # 垂直链路延迟近似（只影响延迟，不影响权重）
        # 语义：Z 延迟 = link_latency * SLOWDOWN / SPEEDUP
        # SLOWDOWN: k>=1（默认 4）
        # SPEEDUP:  m>=1（默认 1）
        V_SLOWDOWN = max(1, int(getattr(options, "tsv_slowdown", 4)))
        V_SPEEDUP = max(1, int(getattr(options, "tsv_speedup", 1)))
        # ------------------------------------

        assert (
            num_routers == X * Y * Z
        ), f"Sparse3D_Pillars requires --num-cpus={X*Y*Z}"
        assert num_rows == Y, f"Sparse3D_Pillars requires --mesh-rows={Y}"
        if LAYOUT_MODE == "staggered":
            assert (
                PX % 2 == 0 and PY % 2 == 0
            ), "Staggered layout requires even spacing"

        # ----- Helper Functions -----
        def _is_pillar_xy(x, y):
            return (x % max(1, PX) == 0) and (y % max(1, PY) == 0)

        def _idx(x, y, z):
            return z * (X * Y) + y * X + x

        # --- add helpers near the top of makeTopology() ---
        def nearest_pillar_xy(x, y):
            # pillars at (k*PX, l*PY)
            px = round(x / PX) * PX
            py = round(y / PY) * PY
            # clamp to grid [0..X-1],[0..Y-1]
            px = max(0, min(X - 1, px))
            py = max(0, min(Y - 1, py))
            return px, py

        def dist_to_pillar(x, y):
            px, py = nearest_pillar_xy(x, y)
            return abs(x - px) + abs(y - py)

        # ----- Link Latencies -----
        link_latency = options.link_latency
        # 将 Z 向链路延迟放大为普通链路的 VLINK_SLOWDOWN 倍
        vlink_latency = max(1, int(link_latency) * V_SLOWDOWN // max(1, V_SPEEDUP))
        router_latency = options.router_latency

        # ----- Router Creation -----
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)

        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        link_count = 0

        # ----- External Links -----
        # Distribute controllers uniformly across routers (same as Mesh_XY)
        network_nodes = []
        remainder_nodes = []
        for node_index in range(len(nodes)):
            if node_index < (len(nodes) - remainder):
                network_nodes.append(nodes[node_index])
            else:
                remainder_nodes.append(nodes[node_index])

        # Connect external nodes (CPUs, etc.) to routers
        ext_links = []
        for (i, n) in enumerate(network_nodes):
            cntrl_level, router_id = divmod(i, num_routers)
            assert cntrl_level < cntrls_per_router
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=n,
                    int_node=routers[router_id],
                    latency=link_latency,
                )
            )
            link_count += 1

        # Connect the remaining nodes to router 0. These should only be DMA nodes.
        for (i, node) in enumerate(remainder_nodes):
            assert node.type == "DMA_Controller"
            assert i < remainder
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=node,
                    int_node=routers[0],
                    latency=link_latency,
                )
            )
            link_count += 1

        network.ext_links = ext_links

        # ----- Internal Links -----
        int_links = []

        # East-West (+X / -X) links
        for z in range(Z):
            for y in range(Y):
                for x in range(X - 1):
                    a = _idx(x, y, z)
                    b = _idx(x + 1, y, z)
                    # if going East reduces distance to nearest pillar => toward
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="East",
                            dst_inport="West",
                            latency=link_latency,
                            weight=WXP,
                        )
                    )
                    link_count += 1
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="West",
                            dst_inport="East",
                            latency=link_latency,
                            weight=WXN,
                        )
                    )
                    link_count += 1

        # North-South (+Y / -Y) links
        for z in range(Z):
            for x in range(X):
                for y in range(Y - 1):
                    a = _idx(x, y, z)
                    b = _idx(x, y + 1, z)
                    # w_ab = W_TOWARD if dist_to_pillar(x, y + 1) < dist_to_pillar(x, y) else W_AWAY
                    # w_ba = W_TOWARD if dist_to_pillar(x, y) < dist_to_pillar(x, y + 1) else W_AWAY
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="North",
                            dst_inport="South",
                            latency=link_latency,
                            weight=WYP,
                        )
                    )
                    link_count += 1
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="South",
                            dst_inport="North",
                            latency=link_latency,
                            weight=WYN,
                        )
                    )
                    link_count += 1

        int_links_z = []
        for z in range(Z - 1):
            for y in range(Y):
                for x in range(X):
                    is_pillar_location = False
                    if LAYOUT_MODE == "aligned":
                        if x % PX == 0 and y % PY == 0:
                            is_pillar_location = True
                    elif LAYOUT_MODE == "staggered":
                        # On even layers, use the aligned pattern
                        if z % 2 == 0:
                            if x % PX == 0 and y % PY == 0:
                                is_pillar_location = True
                        # On odd layers, use the offset pattern
                        else:  # z is odd
                            if (x + PX // 2) % PX == 0 and (
                                y + PY // 2
                            ) % PY == 0:
                                is_pillar_location = True
                    else:
                        raise ValueError(
                            f"Invalid layout specified: {LAYOUT_MODE}"
                        )

                    if is_pillar_location:
                        a = _idx(x, y, z)
                        b = _idx(x, y, z + 1)
                        int_links_z.append(
                            IntLink(
                                link_id=link_count,
                                src_node=routers[a],
                                dst_node=routers[b],
                                src_outport="Up",
                                dst_inport="Down",
                                latency=vlink_latency,
                                weight=WZN,
                            )
                        )
                        link_count += 1
                        int_links_z.append(
                            IntLink(
                                link_id=link_count,
                                src_node=routers[b],
                                dst_node=routers[a],
                                src_outport="Down",
                                dst_inport="Up",
                                latency=vlink_latency,
                                weight=WZP,
                            )
                        )
                        link_count += 1

        int_links.extend(int_links_z)
        network.int_links = int_links

    # 文件系统注册（与官方 Mesh_XY 一致）
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
