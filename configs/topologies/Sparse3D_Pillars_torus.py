# Sparse-Vertical 3D (Pillar-based) topology with Torus links for Garnet 3.0

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class Sparse3D_Pillars_torus(SimpleTopology):
    description = "Sparse3D_Pillars_torus"

    def __init__(self, controllers):
        self.nodes = controllers

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
        # TSV latency multiplier and optional parallel links (sparse-only)
        V_SLOWDOWN = max(1, int(getattr(options, "tsv_slowdown", 4)))
        V_SPEEDUP = max(1, int(getattr(options, "tsv_speedup", 1)))
        # ------------------------------------

        assert (
            num_routers == X * Y * Z
        ), f"Sparse3D_Pillars_torus requires --num-cpus={X*Y*Z}"
        assert num_rows == Y, f"Sparse3D_Pillars_torus requires --mesh-rows={Y}"
        if LAYOUT_MODE == "staggered":
            assert (
                PX % 2 == 0 and PY % 2 == 0
            ), "Staggered layout requires even spacing"

        # ----- Helper Functions -----
        def _idx(x, y, z):
            return z * (X * Y) + y * X + x

        # ----- Link Latencies -----
        link_latency = options.link_latency
        vlink_latency = max(1, int(link_latency) * V_SLOWDOWN // max(1, V_SPEEDUP))
        router_latency = options.router_latency

        # ----- Router Creation -----
        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        link_count = 0

        # ----- External Links -----
        ext_links = []
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        network_nodes = nodes[: len(nodes) - remainder]
        remainder_nodes = nodes[len(nodes) - remainder :]
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

        # East-West (+X / -X) links with wraparound
        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    a = _idx(x, y, z)
                    b = _idx((x + 1) % X, y, z)
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

        # North-South (+Y / -Y) links with wraparound
        for z in range(Z):
            for x in range(X):
                for y in range(Y):
                    a = _idx(x, y, z)
                    b = _idx(x, (y + 1) % Y, z)
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

        # Z-links (Up/Down) only on pillars, with wraparound; single link with adjusted latency
        for y in range(Y):
            for x in range(X):
                is_pillar_location = False
                if LAYOUT_MODE == "aligned":
                    if x % PX == 0 and y % PY == 0:
                        is_pillar_location = True
                elif LAYOUT_MODE == "staggered":
                    if x % PX == 0 and y % PY == 0:
                        is_pillar_location = True

                if is_pillar_location:
                    for z in range(Z):
                        a = _idx(x, y, z)
                        b = _idx(x, y, (z + 1) % Z)
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=routers[a],
                                dst_node=routers[b],
                                src_outport="Up",
                                dst_inport="Down",
                                latency=vlink_latency,
                                weight=WZP,
                            )
                        )
                        link_count += 1
                        int_links.append(
                            IntLink(
                                link_id=link_count,
                                src_node=routers[b],
                                dst_node=routers[a],
                                src_outport="Down",
                                dst_inport="Up",
                                latency=vlink_latency,
                                weight=WZN,
                            )
                        )
                        link_count += 1

        network.int_links = int_links

    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
