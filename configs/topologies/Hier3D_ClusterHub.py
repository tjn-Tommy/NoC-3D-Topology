# configs/topologies/Hier3D_ClusterHub.py

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class Hier3D_ClusterHub(SimpleTopology):
    description = "Hier3D_ClusterHub"

    # Config knobs
    HUB_SPEEDUP = (
        2  # hub router latency = max(1, router_latency // HUB_SPEEDUP)
    )
    CLUSTER_SIDE = 2  # 2x2 HRs per hub

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        # Geometry: use mesh_rows for X=Y, derive Z from num_cpus/(X*Y)
        X = int(options.mesh_rows)
        Y = int(options.mesh_rows)
        assert (
            X > 0 and Y > 0 and X == Y
        ), "Require square per-layer grid (X == Y)"

        N_HR = int(
            options.num_cpus
        )  # number of horizontal routers (one per tile)
        assert (
            N_HR % (X * Y)
        ) == 0, "num_cpus must be divisible by X*Y to get integer Z"
        Z = N_HR // (X * Y)
        assert Z > 0, "Z must be > 0"

        # Cluster grid per layer (2x2 HRs -> 1 hub)
        assert (
            X % self.CLUSTER_SIDE == 0 and Y % self.CLUSTER_SIDE == 0
        ), "X and Y must be multiples of CLUSTER_SIDE"
        CX = X // self.CLUSTER_SIDE
        CY = Y // self.CLUSTER_SIDE
        hubs_per_layer = CX * CY
        N_HBR = Z * hubs_per_layer

        # Latencies
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)

        # TSV controls for vertical (Z) links
        tsv_slow = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_fast = max(1, int(getattr(options, "tsv_speedup", 1)))
        vlink_latency = max(1, int(link_latency) * tsv_slow // tsv_fast)

        hub_latency = max(1, router_latency // max(1, self.HUB_SPEEDUP))

        # ID helpers: first N_HR are HRs, then hubs
        def hr_id(x, y, z):
            return z * (X * Y) + y * X + x

        def hub_id(cx, cy, z):
            return N_HR + z * (CX * CY) + cy * CX + cx

        # Create routers: HRs with router_latency, HBRs with hub_latency
        routers = []
        for i in range(N_HR):
            routers.append(Router(router_id=i, latency=router_latency))
        for j in range(N_HBR):
            routers.append(Router(router_id=N_HR + j, latency=hub_latency))
        network.routers = routers

        # External links: map controllers uniformly to HRs; remainder to router 0 (DMA)
        ext_links = []
        link_id = 0
        cntrls_per_router, remainder = divmod(len(nodes), N_HR)
        network_nodes = nodes[: len(nodes) - remainder]
        remainder_nodes = nodes[len(nodes) - remainder :]

        for i, n in enumerate(network_nodes):
            level, rid = divmod(i, N_HR)
            assert level < cntrls_per_router
            ext_links.append(
                ExtLink(
                    link_id=link_id,
                    ext_node=n,
                    int_node=routers[rid],
                    latency=link_latency,
                )
            )
            link_id += 1

        for i, n in enumerate(remainder_nodes):
            assert n.type == "DMA_Controller" and i < remainder
            ext_links.append(
                ExtLink(
                    link_id=link_id,
                    ext_node=n,
                    int_node=routers[0],
                    latency=link_latency,
                )
            )
            link_id += 1

        network.ext_links = ext_links

        int_links = []

        # Weights for DOR-friendly ordering on the hub backbone
        WX, WY, WZ = 1, 2, 3

        # (A) HR <-> HBR: star within each 2x2 cluster (bidirectional)
        #     Weight = 1 (local egress/ingress to hub)
        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    cx, cy = x // self.CLUSTER_SIDE, y // self.CLUSTER_SIDE
                    h = hr_id(x, y, z)
                    hub = hub_id(cx, cy, z)
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[h],
                            dst_node=routers[hub],
                            src_outport="ToHub",
                            dst_inport="FromCluster",
                            latency=link_latency,
                            weight=1,
                        )
                    )
                    link_id += 1
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[hub],
                            dst_node=routers[h],
                            src_outport="ToCluster",
                            dst_inport="FromHub",
                            latency=link_latency,
                            weight=1,
                        )
                    )
                    link_id += 1

        # (B) HBR <-> HBR: 3D mesh among hubs (only hubs connect horizontally/vertically)
        # X dimension (East/West): weight=WX, latency=link_latency
        for z in range(Z):
            for cy in range(CY):
                for cx in range(CX - 1):
                    a = hub_id(cx, cy, z)
                    b = hub_id(cx + 1, cy, z)
                    # a -> b (East), b -> a (West)
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="East",
                            dst_inport="West",
                            latency=link_latency,
                            weight=WX,
                        )
                    )
                    link_id += 1
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="West",
                            dst_inport="East",
                            latency=link_latency,
                            weight=WX,
                        )
                    )
                    link_id += 1

        # Y dimension (North/South): weight=WY, latency=link_latency
        for z in range(Z):
            for cx in range(CX):
                for cy in range(CY - 1):
                    a = hub_id(cx, cy, z)
                    b = hub_id(cx, cy + 1, z)
                    # a -> b (North), b -> a (South)
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="North",
                            dst_inport="South",
                            latency=link_latency,
                            weight=WY,
                        )
                    )
                    link_id += 1
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="South",
                            dst_inport="North",
                            latency=link_latency,
                            weight=WY,
                        )
                    )
                    link_id += 1

        # Z dimension (Up/Down): weight=WZ, latency=vlink_latency
        for cy in range(CY):
            for cx in range(CX):
                for z in range(Z - 1):
                    a = hub_id(cx, cy, z)
                    b = hub_id(cx, cy, z + 1)
                    # a -> b (Up), b -> a (Down)
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="Up",
                            dst_inport="Down",
                            latency=vlink_latency,
                            weight=WZ,
                        )
                    )
                    link_id += 1
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="Down",
                            dst_inport="Up",
                            latency=vlink_latency,
                            weight=WZ,
                        )
                    )
                    link_id += 1

        network.int_links = int_links

    # Register nodes with filesystem
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
