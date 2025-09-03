# PillarTorusExpress3D: Pillar-based sparse Z + Torus XY + Sparse XY Express
#
# Goal: Cost-performance balance. Reduce TSV count by using pillar Z links,
# use torus wrap in X/Y to improve bisection, and add sparse XY express links
# only at pillar coordinates to reduce average hop count with limited radix.
#
# Usage:
#   --network=garnet --topology=PillarTorusExpress3D
#   --num-cpus = X * Y * Z, --mesh-rows=Y [--mesh-cols=X]
#   --routing-algorithm=0 (TABLE_) for weight-directed routing
#
# Latency: Z links are penalized by --tsv-slowdown (default 4). Effective Z
# latency is link_latency * SLOWDOWN / SPEEDUP. Express links can be given
# lower latency via EXP_LINK_SPEEDUP.

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


# Tunables (static constants for simplicity)
PX, PY = 2, 2  # pillar spacing in X and Y
EXP_K = 2  # only routers where (x%EXP_K==0 and y%EXP_K==0) originate express
EXP_SPAN_X = 2  # span for X express
EXP_SPAN_Y = 2  # span for Y express
EXP_LINK_SPEEDUP = 1  # 2 => express latency is half

# Weights: express strictly preferred to base, and X<Y<Z for base
W_EXP_X = 1
W_EXP_Y = 2
W_X = 11
W_Y = 12
W_Z = 13


class PillarTorusExpress3D(SimpleTopology):
    description = "PillarTorusExpress3D"

    def __init__(self, controllers):
        self.nodes = controllers

    @staticmethod
    def _rid(x, y, z, X, Y):
        return z * (X * Y) + y * X + x

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        num_routers = int(options.num_cpus)
        Y = int(options.mesh_rows)
        X = Y
        if hasattr(options, "mesh_cols") and options.mesh_cols:
            X = int(options.mesh_cols)
        XY = X * Y
        assert num_routers % XY == 0
        Z = num_routers // XY

        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)
        # TSV controls (sparse-only): slowdown overrides vlink, speedup => parallel links
        v_slowdown = max(1, int(getattr(options, "tsv_slowdown", 4)))
        v_speedup = max(1, int(getattr(options, "tsv_speedup", 1)))
        z_latency = max(1, int(link_latency) * v_slowdown // max(1, v_speedup))
        exp_xy_latency = max(1, link_latency // max(1, int(EXP_LINK_SPEEDUP)))

        routers = [Router(router_id=i, latency=router_latency) for i in range(num_routers)]
        network.routers = routers

        # External links
        link_id = 0
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        network_nodes = nodes[: len(nodes) - remainder]
        remainder_nodes = nodes[len(nodes) - remainder :]

        ext_links = []
        for i, n in enumerate(network_nodes):
            level, rid = divmod(i, num_routers)
            assert level < cntrls_per_router
            ext_links.append(ExtLink(link_id=link_id, ext_node=n, int_node=routers[rid], latency=link_latency))
            link_id += 1
        for i, n in enumerate(remainder_nodes):
            assert n.type == "DMA_Controller" and i < remainder
            ext_links.append(ExtLink(link_id=link_id, ext_node=n, int_node=routers[0], latency=link_latency))
            link_id += 1
        network.ext_links = ext_links

        # Internal links
        int_links = []

        # Base: Torus X
        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    a = self._rid(x, y, z, X, Y)
                    b = self._rid((x + 1) % X, y, z, X, Y)
                    int_links.append(IntLink(link_id=link_id, src_node=routers[a], dst_node=routers[b], src_outport="East", dst_inport="West", latency=link_latency, weight=W_X))
                    link_id += 1
                    int_links.append(IntLink(link_id=link_id, src_node=routers[b], dst_node=routers[a], src_outport="West", dst_inport="East", latency=link_latency, weight=W_X))
                    link_id += 1

        # Base: Torus Y
        for z in range(Z):
            for x in range(X):
                for y in range(Y):
                    a = self._rid(x, y, z, X, Y)
                    b = self._rid(x, (y + 1) % Y, z, X, Y)
                    int_links.append(IntLink(link_id=link_id, src_node=routers[a], dst_node=routers[b], src_outport="North", dst_inport="South", latency=link_latency, weight=W_Y))
                    link_id += 1
                    int_links.append(IntLink(link_id=link_id, src_node=routers[b], dst_node=routers[a], src_outport="South", dst_inport="North", latency=link_latency, weight=W_Y))
                    link_id += 1

        # Base: Sparse Z links (pillars only), no wrap; single link with adjusted latency
        for z in range(Z - 1):
            for y in range(Y):
                for x in range(X):
                    if x % PX == 0 and y % PY == 0:
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x, y, z + 1, X, Y)
                        int_links.append(IntLink(link_id=link_id, src_node=routers[a], dst_node=routers[b], src_outport="Up", dst_inport="Down", latency=z_latency, weight=W_Z))
                        link_id += 1
                        int_links.append(IntLink(link_id=link_id, src_node=routers[b], dst_node=routers[a], src_outport="Down", dst_inport="Up", latency=z_latency, weight=W_Z))
                        link_id += 1

        # Sparse XY Express only at pillar coordinates, bidirectional, no wrap
        # X-Express
        if EXP_SPAN_X > 0 and X > EXP_SPAN_X:
            for z in range(Z):
                for y in range(Y):
                    if y % PY != 0:
                        continue
                    for x in range(0, X - EXP_SPAN_X):
                        if (x % PX == 0) and (y % PY == 0) and (x % EXP_K == 0):
                            a = self._rid(x, y, z, X, Y)
                            b = self._rid(x + EXP_SPAN_X, y, z, X, Y)
                            int_links.append(IntLink(link_id=link_id, src_node=routers[a], dst_node=routers[b], src_outport="EastExp", dst_inport="WestExp", latency=exp_xy_latency, weight=W_EXP_X))
                            link_id += 1
                            int_links.append(IntLink(link_id=link_id, src_node=routers[b], dst_node=routers[a], src_outport="WestExp", dst_inport="EastExp", latency=exp_xy_latency, weight=W_EXP_X))
                            link_id += 1

        # Y-Express
        if EXP_SPAN_Y > 0 and Y > EXP_SPAN_Y:
            for z in range(Z):
                for x in range(X):
                    if x % PX != 0:
                        continue
                    for y in range(0, Y - EXP_SPAN_Y):
                        if (x % PX == 0) and (y % PY == 0) and (y % EXP_K == 0):
                            a = self._rid(x, y, z, X, Y)
                            b = self._rid(x, y + EXP_SPAN_Y, z, X, Y)
                            int_links.append(IntLink(link_id=link_id, src_node=routers[a], dst_node=routers[b], src_outport="NorthExp", dst_inport="SouthExp", latency=exp_xy_latency, weight=W_EXP_Y))
                            link_id += 1
                            int_links.append(IntLink(link_id=link_id, src_node=routers[b], dst_node=routers[a], src_outport="SouthExp", dst_inport="NorthExp", latency=exp_xy_latency, weight=W_EXP_Y))
                            link_id += 1

        network.int_links = int_links

    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
