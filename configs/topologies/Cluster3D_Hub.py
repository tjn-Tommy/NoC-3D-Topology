# configs/topologies/Cluster3D_Hub.py
# ----------------------------------------------------------------------
# 3D Cluster Mesh (4x4x4): 每层划分为 2x2 簇，每簇 4 个 HR 共享一个 HBR（位于几何中心）。
# 仅 Hub Router 之间有垂直 Up/Down 连接，从而减少 TSV 数量。
# 默认使用 Garnet 的 TABLE_ 路由（表驱动，靠 link weight 形成 DOR 次序）。
# ----------------------------------------------------------------------

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class Cluster3D_Hub(SimpleTopology):
    description = "Cluster3D_Hub"

    # ===================== 配置区（改这里就行） =====================
    # Hub 路由器加速倍数：Hub router 的流水线延迟 = max(1, 全局router_latency // HUB_SPEEDUP)
    HUB_SPEEDUP = 2  # 例：2 表示 Hub 延迟减半；1 表示不变

    # 簇大小（目前固定 2x2）
    CLUSTER_SIDE = 2
    # ===================== 配置区（到此结束） =====================

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        # ------- 尺寸：默认用 mesh_rows 做 X=Y（方阵层），Z 由 num_cpus/(X*Y) 得出 -------
        X = int(options.mesh_rows)
        Y = int(options.mesh_rows)
        assert X > 0 and Y > 0 and X == Y, "本脚本假设每层是 X==Y 的方阵"

        N_HR = int(options.num_cpus)  # 水平路由器 (HR) 数量 == 节点数
        assert (N_HR % (X * Y)) == 0, "num_cpus 必须能整除 X*Y 才能得到整数层数"
        Z = N_HR // (X * Y)
        assert Z > 0, "层数 Z 必须 > 0"

        # 每层簇个数（2x2 簇）
        assert (
            X % self.CLUSTER_SIDE == 0 and Y % self.CLUSTER_SIDE == 0
        ), "X/Y 必须能被簇边长整除"
        hubs_per_layer = (X // self.CLUSTER_SIDE) * (Y // self.CLUSTER_SIDE)
        N_HBR = Z * hubs_per_layer  # Hub Router (HBR) 数量

        # 全局延迟
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)

        # 应用 TSV 近似（语义：Z延迟 = link_latency * SLOWDOWN / SPEEDUP）
        tsv_slow = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_fast = max(1, int(getattr(options, "tsv_speedup", 1)))

        vlink_latency = max(1, int(link_latency) * tsv_slow // tsv_fast)
        hub_latency = max(1, router_latency // max(1, self.HUB_SPEEDUP))

        # ------- id 编码：与 Mesh3D_XYZ_ 保持一致，便于 XYZ/DOR 使用 -------
        def hr_id(x, y, z):  # 普通水平路由器编号
            return z * (X * Y) + y * X + x

        def hbr_id(cx, cy, z):  # 簇中心 Hub 路由器编号（放在 HR 之后）
            # 簇网格坐标 (cx, cy)，范围：0..(X/2-1), 0..(Y/2-1)
            idx_in_layer = cy * (X // self.CLUSTER_SIDE) + cx
            return N_HR + z * hubs_per_layer + idx_in_layer

        # ------- 创建路由器：先 HR（router_latency），后 HBR（hub_latency 更快） -------
        routers = []
        for i in range(N_HR):
            routers.append(Router(router_id=i, latency=router_latency))
        for j in range(N_HBR):
            routers.append(Router(router_id=N_HR + j, latency=hub_latency))
        network.routers = routers

        # ------- 外部连接：仅 HR 暴露 NI，均匀映射 controllers -------
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

        # 余量（应为 DMA）绑到 router 0
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

        # ------- 内部连接 -------
        int_links = []

        # (A) HR <-> HR：层内 2D Mesh
        #   E/W 权重=1，N/S 权重=2（表驱动 DOR：先走 X 再走 Y）
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

        # (B) HR <-> HBR：簇内星型（每个 HR 接到其中心 Hub）
        #   权重=3（在表路由下，竖直作为第三优先级）
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

        # (C) HBR <-> HBR：竖直 Up/Down（单链，延迟按 TSV 参数调整）
        #   权重=4（最后一维 Z）
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

    # 与官方一致的注册函数
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
