# Copyright (c) 2025
# All rights reserved.
#
# Hier3D_Chiplet: Hierarchical 3D Chiplet Topology
#
# 设计要点：
# - 全局网络为 X=Y=mesh_rows 的多层 3D 网格（Z 由 num_cpus / (X*Y) 推出）
# - 将每层划分为若干 chiplet（CHIP_X x CHIP_Y x CHIP_Z）
# - chiplet 内部：只连 chiplet 范围内的 X/Y/Z 邻接（权重 W_INTRA）
# - chiplet 之间：不直接跨边界连线，而是通过 chiplet 的“网关（GW）”
#   * 平面骨干：东西/南北方向，GW 与 GW 相连（权重 W_BACKBONE）
#   * 垂直骨干：Up/Down，通过 GW 相连（权重 W_VERTICAL）
# - GW 端口命名：EastGW/WestGW/NorthGW/SouthGW/UpGW/DownGW
# - 支持每个 chiplet 使用 1 个或 2 个 GW（GW_PER_CHIPLET = 1 或 2）
#   * 2-GW 策略：X 向骨干走 GWx；Y 向骨干走 GWy；Z 向骨干默认走 GWx
#
# 路由：
# - 使用 TABLE_（routing-algorithm=0），靠链路权重引导：
#   W_INTRA < W_BACKBONE < W_VERTICAL
#
# 使用方法：
# --topology=Hier3D_Chiplet --network=garnet --routing-algorithm=0
# 仅需提供 --mesh-rows 与 --num-cpus，且需满足：
#   X = Y = mesh_rows，Z = num_cpus / (X*Y) 为整数
#   X % CHIP_X == 0, Y % CHIP_Y == 0, Z % CHIP_Z == 0
#
# 如需调参，直接修改“设置变量区域”即可（无须改命令行）。

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


# =========================
# ===== 设置变量区域 ======
# =========================
# 每个 chiplet 的尺寸（必须整除全局维度）
CHIP_X = 2
CHIP_Y = 2
CHIP_Z = 1

# 每个 chiplet 网关数量（1 或 2）
GW_PER_CHIPLET = 1  # 可改为 2

# 权重（越小越优先）
W_INTRA = 1  # chiplet 内普通 X/Y/Z
W_BACKBONE = 2  # chiplet 之间的平面骨干（东西/南北）
W_VERTICAL = 3  # chiplet 之间的垂直骨干（Up/Down）


class Hier3D_Chiplet(SimpleTopology):
    description = "Hier3D_Chiplet"

    def __init__(self, controllers):
        self.nodes = controllers

    # Helper: 3D 坐标与 router_id 映射
    @staticmethod
    def _rid(x, y, z, X, Y):
        return z * (X * Y) + y * X + x

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        # --------- 全局维度推导（保持与 Mesh3D_XYZ_ 一致的假设）---------
        num_routers = options.num_cpus
        Y = options.mesh_rows
        assert Y > 0
        X = Y  # 约定 X=Y
        assert (
            num_routers % (X * Y)
        ) == 0, "num_cpus must be a multiple of mesh_rows^2"
        Z = num_routers // (X * Y)
        assert Z > 0

        # 基本延迟参数
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)
        tsv_slowdown = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_speedup = max(1, int(getattr(options, "tsv_speedup", 1)))
        vlink_latency = max(1, int(link_latency) * tsv_slowdown // tsv_speedup)

        # ------------- 分发路由器 -------------
        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        # ------------- 外部节点（控制器）连入 -------------
        link_count = 0
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        ext_links = []
        for (i, n) in enumerate(nodes[: len(nodes) - remainder]):
            cntrl_level, r_id = divmod(i, num_routers)
            assert cntrl_level < cntrls_per_router
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=n,
                    int_node=routers[r_id],
                    latency=link_latency,
                )
            )
            link_count += 1

        # 余下（通常 DMA）进 router 0
        for (i, n) in enumerate(nodes[len(nodes) - remainder :]):
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=n,
                    int_node=routers[0],
                    latency=link_latency,
                )
            )
            link_count += 1

        network.ext_links = ext_links

        # ------------- 校验 chiplet 可分性 -------------
        assert (
            X % CHIP_X == 0 and Y % CHIP_Y == 0 and Z % CHIP_Z == 0
        ), "Global dims (X,Y,Z) must be divisible by chiplet dims (CHIP_X,CHIP_Y,CHIP_Z)"

        CX = X // CHIP_X  # 每层 chiplet 列数
        CY = Y // CHIP_Y  # 每层 chiplet 行数
        CZ = Z // CHIP_Z  # chiplet 层数（在 Z 上）

        # ------------- 为每个 chiplet 选 GW -------------
        # 为简洁：每个 chiplet 的 GW 用“芯粒内的一个或两个路由器”表示
        # gw_map[(cx,cy,cz)] = (gwx, gwy [, gwz?])
        # 2-GW 模式：gw_x 用于 X 向骨干；gw_y 用于 Y 向骨干；Z 默认走 gw_x
        gw_x_map = {}
        gw_y_map = {}

        def choose_one_gateway(x0, y0, z0):
            # 选择 chiplet 中“近中心”的一个路由器作为单 GW
            gx = min(x0 + CHIP_X // 2, x0 + CHIP_X - 1)
            gy = min(y0 + CHIP_Y // 2, y0 + CHIP_Y - 1)
            gz = min(z0 + CHIP_Z // 2, z0 + CHIP_Z - 1)
            return (gx, gy, gz)

        def choose_two_gateways(x0, y0, z0):
            # X 向 GW：靠 chiplet 的“左边”中线
            gx0 = x0
            gy0 = min(y0 + CHIP_Y // 2, y0 + CHIP_Y - 1)
            gz0 = min(z0 + CHIP_Z // 2, z0 + CHIP_Z - 1)
            # Y 向 GW：靠 chiplet 的“上边”中线
            gx1 = min(x0 + CHIP_X // 2, x0 + CHIP_X - 1)
            gy1 = y0
            gz1 = gz0
            return (gx0, gy0, gz0), (gx1, gy1, gz1)

        for cz in range(CZ):
            for cy in range(CY):
                for cx in range(CX):
                    x0 = cx * CHIP_X
                    y0 = cy * CHIP_Y
                    z0 = cz * CHIP_Z
                    if GW_PER_CHIPLET == 1:
                        gx, gy, gz = choose_one_gateway(x0, y0, z0)
                        gw_x_map[(cx, cy, cz)] = (gx, gy, gz)
                        gw_y_map[(cx, cy, cz)] = (gx, gy, gz)  # 同一个
                    else:
                        (gx0, gy0, gz0), (gx1, gy1, gz1) = choose_two_gateways(
                            x0, y0, z0
                        )
                        gw_x_map[(cx, cy, cz)] = (gx0, gy0, gz0)  # X 向骨干
                        gw_y_map[(cx, cy, cz)] = (gx1, gy1, gz1)  # Y 向骨干

        # ------------- 生成 IntLink（簇内 + 骨干）-------------
        int_links = []

        def add_link(src_id, dst_id, src_port, dst_port, w):
            nonlocal link_count
            lat = vlink_latency if (
                ("Up" in src_port) or ("Down" in src_port) or ("Up" in dst_port) or ("Down" in dst_port)
            ) else link_latency
            int_links.append(
                IntLink(
                    link_id=link_count,
                    src_node=routers[src_id],
                    dst_node=routers[dst_id],
                    src_outport=src_port,
                    dst_inport=dst_port,
                    latency=lat,
                    weight=w,
                )
            )
            link_count += 1

        # a) chiplet 内部：X / Y / Z 邻接（仅限 chiplet 范围内）
        for z in range(Z):
            # chiplet 边界坐标
            cz = z // CHIP_Z
            for y in range(Y):
                cy = y // CHIP_Y
                for x in range(X):
                    cx = x // CHIP_X

                    # East-West（仅在同一 chiplet 内连）
                    if (x + 1 < X) and ((x + 1) // CHIP_X == cx):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x + 1, y, z, X, Y)
                        add_link(a, b, "East", "West", W_INTRA)
                        add_link(b, a, "West", "East", W_INTRA)

                    # North-South（仅在同一 chiplet 内连）
                    if (y + 1 < Y) and ((y + 1) // CHIP_Y == cy):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x, y + 1, z, X, Y)
                        add_link(a, b, "North", "South", W_INTRA)
                        add_link(b, a, "South", "North", W_INTRA)

                    # Up-Down（仅在同一 chiplet 内连）
                    if (z + 1 < Z) and ((z + 1) // CHIP_Z == cz):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x, y, z + 1, X, Y)
                        add_link(a, b, "Up", "Down", W_INTRA)
                        add_link(b, a, "Down", "Up", W_INTRA)

        # b) chiplet 之间：GW-to-GW 骨干（X、Y、Z）
        #   X 方向骨干：右邻 chiplet（cx+1）
        for cz in range(CZ):
            for cy in range(CY):
                for cx in range(CX - 1):
                    gx0, gy0, gz0 = gw_x_map[(cx, cy, cz)]
                    gx1, gy1, gz1 = gw_x_map[(cx + 1, cy, cz)]
                    a = self._rid(gx0, gy0, gz0, X, Y)
                    b = self._rid(gx1, gy1, gz1, X, Y)
                    add_link(a, b, "EastGW", "WestGW", W_BACKBONE)
                    add_link(b, a, "WestGW", "EastGW", W_BACKBONE)

        #   Y 方向骨干：下邻 chiplet（cy+1）
        for cz in range(CZ):
            for cy in range(CY - 1):
                for cx in range(CX):
                    gx0, gy0, gz0 = gw_y_map[(cx, cy, cz)]
                    gx1, gy1, gz1 = gw_y_map[(cx, cy + 1, cz)]
                    a = self._rid(gx0, gy0, gz0, X, Y)
                    b = self._rid(gx1, gy1, gz1, X, Y)
                    add_link(a, b, "NorthGW", "SouthGW", W_BACKBONE)
                    add_link(b, a, "SouthGW", "NorthGW", W_BACKBONE)

        #   Z 方向骨干：上邻 chiplet（cz+1）
        #   规则：Z 方向沿用 X 向的 GW（也可按需改为独立 gw_z_map）
        for cz in range(CZ - 1):
            for cy in range(CY):
                for cx in range(CX):
                    gx0, gy0, gz0 = gw_x_map[(cx, cy, cz)]
                    gx1, gy1, gz1 = gw_x_map[(cx, cy, cz + 1)]
                    a = self._rid(gx0, gy0, gz0, X, Y)
                    b = self._rid(gx1, gy1, gz1, X, Y)
                    add_link(a, b, "UpGW", "DownGW", W_VERTICAL)
                    add_link(b, a, "DownGW", "UpGW", W_VERTICAL)

        network.int_links = int_links

    # 注册文件系统节点（保持与官方风格一致）
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
