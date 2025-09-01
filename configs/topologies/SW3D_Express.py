# Copyright (c) 2025
# All rights reserved.
#
# SW3D_Express: 3D Mesh + rule-based express links
#
# Usage:
#   --network=garnet --topology=SW3D_Express
#   --num-cpus=N(=X*Y*Z) --mesh-rows=Y
#   --routing-algorithm=0  # TABLE_
#
# Notes:
# - Base 3D Mesh: ordinary X/Y/Z neighbors with weights W_X/W_Y/W_Z
# - Rule-based express links: every EXP_K routers, add span=EXP_SPAN_* along X/Y/Z
#   * Ports: EastExp/WestExp/NorthExp/SouthExp/UpExp/DownExp (no conflict with base)
#   * Weights are strictly ordered to avoid TABLE_ random tie-breaks
# - Optional: express link latency speedup (EXP_LINK_SPEEDUP >= 1)
#
# This is a TABLE_ (routing-table) topology; XY/XYZ routers ignore weights.

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


# ======================
# Tunables
# ======================

# —— Weight scheme (strict ordering: Express < Base; and X < Y < Z) ————————
# This guarantees a unique minimum-weight candidate so TABLE_ won't randomize.

# Express (highest priority)
W_EXP_X = 1
W_EXP_Y = 2
W_EXP_Z = 3

# Base links (lower priority than any Express)
W_X = 11
W_Y = 12
W_Z = 13

# Express placement rule: every EXP_K routers
EXP_K = 2

# Express span (e.g., 2 means jump over two hops)
EXP_SPAN_X = 2
EXP_SPAN_Y = 2
EXP_SPAN_Z = 2

# Express link latency speedup: exp_latency = link_latency // EXP_LINK_SPEEDUP (>=1)
EXP_LINK_SPEEDUP = 1  # 1 = no speedup


class SW3D_Express(SimpleTopology):
    description = "SW3D_Express (3D Mesh + Express Links)"

    def __init__(self, controllers):
        self.nodes = controllers

    # Router id mapping: id = z*(X*Y) + y*X + x
    @staticmethod
    def _rid(x, y, z, X, Y):
        return z * (X * Y) + y * X + x

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        num_routers = options.num_cpus
        assert num_routers > 0

        # ===== infer dimensions =====
        Y = int(options.mesh_rows)
        assert Y > 0
        X = Y
        if hasattr(options, "mesh_cols") and options.mesh_cols:
            X = int(options.mesh_cols)
        XY = X * Y
        assert num_routers % XY == 0
        Z = num_routers // XY
        if hasattr(options, "mesh_depth") and options.mesh_depth:
            assert int(options.mesh_depth) == Z

        # ===== timing =====
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)

        # express link latency (optionally faster)
        exp_latency = max(1, link_latency // max(1, int(EXP_LINK_SPEEDUP)))

        # ===== routers =====
        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        # ===== external links (NI/controllers) =====
        link_count = 0
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        network_nodes, remainder_nodes = [], []
        for idx, n in enumerate(nodes):
            if idx < (len(nodes) - remainder):
                network_nodes.append(n)
            else:
                remainder_nodes.append(n)

        ext_links = []
        for i, n in enumerate(network_nodes):
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

        # leftover controllers (should be DMAs) → router 0
        for i, node in enumerate(remainder_nodes):
            assert node.type == "DMA_Controller"
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

        # ===== internal links (base X/Y/Z, bidirectional) =====
        int_links = []

        # X: East / West
        for z in range(Z):
            for y in range(Y):
                for x in range(X - 1):
                    a = self._rid(x, y, z, X, Y)
                    b = self._rid(x + 1, y, z, X, Y)
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="East",
                            dst_inport="West",
                            latency=link_latency,
                            weight=W_X,
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
                            weight=W_X,
                        )
                    )
                    link_count += 1

        # Y: North / South
        for z in range(Z):
            for y in range(Y - 1):
                for x in range(X):
                    a = self._rid(x, y, z, X, Y)
                    b = self._rid(x, y + 1, z, X, Y)
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="North",
                            dst_inport="South",
                            latency=link_latency,
                            weight=W_Y,
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
                            weight=W_Y,
                        )
                    )
                    link_count += 1

        # Z: Up / Down
        for z in range(Z - 1):
            for y in range(Y):
                for x in range(X):
                    a = self._rid(x, y, z, X, Y)
                    b = self._rid(x, y, z + 1, X, Y)
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="Up",
                            dst_inport="Down",
                            latency=link_latency,
                            weight=W_Z,
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
                            latency=link_latency,
                            weight=W_Z,
                        )
                    )
                    link_count += 1

        # ===== rule-based Express links (bidirectional) =====
        # Ports: *Exp; weights are W_EXP_X/Y/Z; latency may be exp_latency

        # X-Express
        if EXP_SPAN_X > 0 and X > EXP_SPAN_X:
            for z in range(Z):
                for y in range(Y):
                    for x in range(0, X - EXP_SPAN_X):
                        if (x % EXP_K == 0) and (y % EXP_K == 0):
                            a = self._rid(x, y, z, X, Y)
                            b = self._rid(x + EXP_SPAN_X, y, z, X, Y)
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[a],
                                    dst_node=routers[b],
                                    src_outport="EastExp",
                                    dst_inport="WestExp",
                                    latency=exp_latency,
                                    weight=W_EXP_X,
                                )
                            )
                            link_count += 1
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[b],
                                    dst_node=routers[a],
                                    src_outport="WestExp",
                                    dst_inport="EastExp",
                                    latency=exp_latency,
                                    weight=W_EXP_X,
                                )
                            )
                            link_count += 1

        # Y-Express
        if EXP_SPAN_Y > 0 and Y > EXP_SPAN_Y:
            for z in range(Z):
                for y in range(0, Y - EXP_SPAN_Y):
                    for x in range(X):
                        if (x % EXP_K == 0) and (y % EXP_K == 0):
                            a = self._rid(x, y, z, X, Y)
                            b = self._rid(x, y + EXP_SPAN_Y, z, X, Y)
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[a],
                                    dst_node=routers[b],
                                    src_outport="NorthExp",
                                    dst_inport="SouthExp",
                                    latency=exp_latency,
                                    weight=W_EXP_Y,
                                )
                            )
                            link_count += 1
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[b],
                                    dst_node=routers[a],
                                    src_outport="SouthExp",
                                    dst_inport="NorthExp",
                                    latency=exp_latency,
                                    weight=W_EXP_Y,
                                )
                            )
                            link_count += 1

        # Z-Express
        if EXP_SPAN_Z > 0 and Z > EXP_SPAN_Z:
            for z in range(0, Z - EXP_SPAN_Z):
                for y in range(Y):
                    for x in range(X):
                        if (x % EXP_K == 0) and (y % EXP_K == 0):
                            a = self._rid(x, y, z, X, Y)
                            b = self._rid(x, y, z + EXP_SPAN_Z, X, Y)
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[a],
                                    dst_node=routers[b],
                                    src_outport="UpExp",
                                    dst_inport="DownExp",
                                    latency=exp_latency,
                                    weight=W_EXP_Z,
                                )
                            )
                            link_count += 1
                            int_links.append(
                                IntLink(
                                    link_id=link_count,
                                    src_node=routers[b],
                                    dst_node=routers[a],
                                    src_outport="DownExp",
                                    dst_inport="UpExp",
                                    latency=exp_latency,
                                    weight=W_EXP_Z,
                                )
                            )
                            link_count += 1

        network.int_links = int_links

    # Register nodes for FS (same as official Mesh topologies)
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
