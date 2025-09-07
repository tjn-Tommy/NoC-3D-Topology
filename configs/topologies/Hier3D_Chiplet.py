from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


# =========================
# ===== configs ======
# =========================

# size of a chiplet
CHIP_X = 2
CHIP_Y = 2
CHIP_Z = 1

# num of gateways per chiplet
GW_PER_CHIPLET = 1

# WEIGHT
W_INTRA = 1
W_BACKBONE = 2
W_VERTICAL = 3

# =========================
# ===== configs ======
# =========================


class Hier3D_Chiplet(SimpleTopology):
    description = "Hier3D_Chiplet"

    def __init__(self, controllers):
        self.nodes = controllers

    # Helper: coordinate and router_id
    @staticmethod
    def _rid(x, y, z, X, Y):
        return z * (X * Y) + y * X + x

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        num_routers = options.num_cpus
        Y = options.mesh_rows
        assert Y > 0
        X = Y
        assert (
            num_routers % (X * Y)
        ) == 0, "num_cpus must be a multiple of mesh_rows^2"
        Z = num_routers // (X * Y)
        assert Z > 0

        # Latency
        link_latency = int(options.link_latency)
        router_latency = int(options.router_latency)
        tsv_slowdown = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_speedup = max(1, int(getattr(options, "tsv_speedup", 1)))
        vlink_latency = max(1, int(link_latency) * tsv_slowdown // tsv_speedup)

        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

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

        assert (
            X % CHIP_X == 0 and Y % CHIP_Y == 0 and Z % CHIP_Z == 0
        ), "Global dims (X,Y,Z) must be divisible by chiplet dims (CHIP_X,CHIP_Y,CHIP_Z)"

        CX = X // CHIP_X
        CY = Y // CHIP_Y
        CZ = Z // CHIP_Z  # chiplet layer num

        gw_x_map = {}
        gw_y_map = {}

        def choose_one_gateway(x0, y0, z0):

            gx = min(x0 + CHIP_X // 2, x0 + CHIP_X - 1)
            gy = min(y0 + CHIP_Y // 2, y0 + CHIP_Y - 1)
            gz = min(z0 + CHIP_Z // 2, z0 + CHIP_Z - 1)
            return (gx, gy, gz)

        def choose_two_gateways(x0, y0, z0):

            gx0 = x0
            gy0 = min(y0 + CHIP_Y // 2, y0 + CHIP_Y - 1)
            gz0 = min(z0 + CHIP_Z // 2, z0 + CHIP_Z - 1)

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
                        gw_y_map[(cx, cy, cz)] = (gx, gy, gz)
                    else:
                        (gx0, gy0, gz0), (gx1, gy1, gz1) = choose_two_gateways(
                            x0, y0, z0
                        )
                        gw_x_map[(cx, cy, cz)] = (gx0, gy0, gz0)  # X
                        gw_y_map[(cx, cy, cz)] = (gx1, gy1, gz1)  # Y

        # ------------- IntLink-------------
        int_links = []

        def add_link(src_id, dst_id, src_port, dst_port, w):
            nonlocal link_count
            lat = (
                vlink_latency
                if (
                    ("Up" in src_port)
                    or ("Down" in src_port)
                    or ("Up" in dst_port)
                    or ("Down" in dst_port)
                )
                else link_latency
            )
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

        for z in range(Z):
            # chiplet
            cz = z // CHIP_Z
            for y in range(Y):
                cy = y // CHIP_Y
                for x in range(X):
                    cx = x // CHIP_X

                    # East-West
                    if (x + 1 < X) and ((x + 1) // CHIP_X == cx):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x + 1, y, z, X, Y)
                        add_link(a, b, "East", "West", W_INTRA)
                        add_link(b, a, "West", "East", W_INTRA)

                    # North-South
                    if (y + 1 < Y) and ((y + 1) // CHIP_Y == cy):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x, y + 1, z, X, Y)
                        add_link(a, b, "North", "South", W_INTRA)
                        add_link(b, a, "South", "North", W_INTRA)

                    # Up-Down
                    if (z + 1 < Z) and ((z + 1) // CHIP_Z == cz):
                        a = self._rid(x, y, z, X, Y)
                        b = self._rid(x, y, z + 1, X, Y)
                        add_link(a, b, "Up", "Down", W_INTRA)
                        add_link(b, a, "Down", "Up", W_INTRA)

        #   X  chiplet
        for cz in range(CZ):
            for cy in range(CY):
                for cx in range(CX - 1):
                    gx0, gy0, gz0 = gw_x_map[(cx, cy, cz)]
                    gx1, gy1, gz1 = gw_x_map[(cx + 1, cy, cz)]
                    a = self._rid(gx0, gy0, gz0, X, Y)
                    b = self._rid(gx1, gy1, gz1, X, Y)
                    add_link(a, b, "EastGW", "WestGW", W_BACKBONE)
                    add_link(b, a, "WestGW", "EastGW", W_BACKBONE)

        #   Y  chiplet
        for cz in range(CZ):
            for cy in range(CY - 1):
                for cx in range(CX):
                    gx0, gy0, gz0 = gw_y_map[(cx, cy, cz)]
                    gx1, gy1, gz1 = gw_y_map[(cx, cy + 1, cz)]
                    a = self._rid(gx0, gy0, gz0, X, Y)
                    b = self._rid(gx1, gy1, gz1, X, Y)
                    add_link(a, b, "NorthGW", "SouthGW", W_BACKBONE)
                    add_link(b, a, "SouthGW", "NorthGW", W_BACKBONE)

        #   Z  chiplet

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

    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
