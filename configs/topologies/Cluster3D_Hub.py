# configs/topologies/Cluster3D_Hub.py

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class Cluster3D_Hub(SimpleTopology):
    description = "Cluster3D_Hub"

    # ===================== configs =====================
    # Hub router  latency= max(1, router_latency // HUB_SPEEDUP)
    HUB_SPEEDUP = 2

    CLUSTER_SIDE = 2
    # ===================== configs =====================

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        # ------- X=Y -------
        X = int(options.mesh_rows)
        Y = int(options.mesh_rows)
        assert X > 0 and Y > 0 and X == Y, "本脚本假设每层是 X==Y 的方阵"

        N_HR = int(options.num_cpus)
        assert (N_HR % (X * Y)) == 0, "num_cpus 必须能整除 X*Y 才能得到整数层数"
        Z = N_HR // (X * Y)
        assert Z > 0, "层数 Z 必须 > 0"

        # num of clusters per layer
        assert (
            X % self.CLUSTER_SIDE == 0 and Y % self.CLUSTER_SIDE == 0
        ), "X/Y 必须能被簇边长整除"
        hubs_per_layer = (X // self.CLUSTER_SIDE) * (Y // self.CLUSTER_SIDE)
        N_HBR = Z * hubs_per_layer  # Hub Router

        # Link Latency
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)

        # Z = link_latency * SLOWDOWN / SPEEDUP
        tsv_slow = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_fast = max(1, int(getattr(options, "tsv_speedup", 1)))

        vlink_latency = max(1, int(link_latency) * tsv_slow // tsv_fast)
        hub_latency = max(1, router_latency // max(1, self.HUB_SPEEDUP))

        # ------- id -------
        def hr_id(x, y, z):
            return z * (X * Y) + y * X + x

        def hbr_id(cx, cy, z):

            idx_in_layer = cy * (X // self.CLUSTER_SIDE) + cx
            return N_HR + z * hubs_per_layer + idx_in_layer

        # ------- Add Routers -------
        routers = []
        for i in range(N_HR):
            routers.append(Router(router_id=i, latency=router_latency))
        for j in range(N_HBR):
            routers.append(Router(router_id=N_HR + j, latency=hub_latency))
        network.routers = routers

        # ------- ExtLink -------
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

        # ------- IntLink -------
        int_links = []

        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    u = hr_id(x, y, z)
                    if x + 1 < X:
                        v = hr_id(x + 1, y, z)
                        # u -> v (East), v -> u (West)
                        int_links.append(
                            IntLink(
                                link_id=link_id,
                                src_node=routers[u],
                                dst_node=routers[v],
                                src_outport="East",
                                dst_inport="West",
                                latency=link_latency,
                                weight=1,
                            )
                        )
                        link_id += 1
                        int_links.append(
                            IntLink(
                                link_id=link_id,
                                src_node=routers[v],
                                dst_node=routers[u],
                                src_outport="West",
                                dst_inport="East",
                                latency=link_latency,
                                weight=1,
                            )
                        )
                        link_id += 1
                    if y + 1 < Y:
                        v = hr_id(x, y + 1, z)
                        # u -> v (North), v -> u (South)
                        int_links.append(
                            IntLink(
                                link_id=link_id,
                                src_node=routers[u],
                                dst_node=routers[v],
                                src_outport="North",
                                dst_inport="South",
                                latency=link_latency,
                                weight=2,
                            )
                        )
                        link_id += 1
                        int_links.append(
                            IntLink(
                                link_id=link_id,
                                src_node=routers[v],
                                dst_node=routers[u],
                                src_outport="South",
                                dst_inport="North",
                                latency=link_latency,
                                weight=2,
                            )
                        )
                        link_id += 1

        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    cx, cy = x // self.CLUSTER_SIDE, y // self.CLUSTER_SIDE
                    hub = hbr_id(cx, cy, z)
                    h = hr_id(x, y, z)
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[h],
                            dst_node=routers[hub],
                            src_outport="ToHub",
                            dst_inport="FromCluster",
                            latency=link_latency,
                            weight=3,
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
                            weight=3,
                        )
                    )
                    link_id += 1

        for z in range(Z - 1):
            for cy in range(Y // self.CLUSTER_SIDE):
                for cx in range(X // self.CLUSTER_SIDE):
                    a = hbr_id(cx, cy, z)
                    b = hbr_id(cx, cy, z + 1)
                    # a -> b : Up
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="Up",
                            dst_inport="Down",
                            latency=vlink_latency,
                            weight=4,
                        )
                    )
                    link_id += 1
                    # b -> a : Down
                    int_links.append(
                        IntLink(
                            link_id=link_id,
                            src_node=routers[b],
                            dst_node=routers[a],
                            src_outport="Down",
                            dst_inport="Up",
                            latency=vlink_latency,
                            weight=4,
                        )
                    )
                    link_id += 1

        network.int_links = int_links

    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
