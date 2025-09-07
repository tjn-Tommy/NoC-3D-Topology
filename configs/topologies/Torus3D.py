# Torus3D  topology for Garnet 3.0

from m5.params import *
from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology

class Torus3D(SimpleTopology):
    description = "Torus3D"

    def __init__(self, controllers):
        self.nodes = controllers

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        num_routers = options.num_cpus
        num_rows = options.mesh_rows

        # Fixed geometry: 4x4x4
        X, Y, Z = 4, 4, 4
        assert num_rows > 0
        assert num_routers == X * Y * Z, "Torus3D requires --num-cpus=64"
        assert num_rows == Y, "Torus3D requires --mesh-rows=4"

        # default values for link latency and router latency.
        link_latency = options.link_latency
        router_latency = options.router_latency
        # Z-link (TSV) latency = link_latency * SLOWDOWN / SPEEDUP
        tsv_slowdown = max(1, int(getattr(options, "tsv_slowdown", 4)))
        tsv_speedup = max(1, int(getattr(options, "tsv_speedup", 1)))
        vlink_latency = max(1, int(link_latency) * tsv_slowdown // tsv_speedup)

        # Create the routers
        routers = [Router(router_id=i, latency=router_latency) for i in range(num_routers)]
        network.routers = routers

        # link counter
        link_count = 0

        # Connect controllers to routers
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        
        network_nodes = nodes[:len(nodes) - remainder]
        remainder_nodes = nodes[len(nodes) - remainder:]

        ext_links = []
        for (i, n) in enumerate(network_nodes):
            cntrl_level, router_id = divmod(i, num_routers)
            assert cntrl_level < cntrls_per_router
            ext_links.append(ExtLink(link_id=link_count, ext_node=n, int_node=routers[router_id], latency=link_latency))
            link_count += 1

        for (i, node) in enumerate(remainder_nodes):
            assert node.type == 'DMA_Controller'
            ext_links.append(ExtLink(link_id=link_count, ext_node=node, int_node=routers[0], latency=link_latency))
            link_count += 1
        
        network.ext_links = ext_links

        # Create the torus links
        int_links = []

        def idx(x, y, z):
            return z * (X * Y) + y * X + x

        # Weights for routing.
        WX, WY, WZ = 1, 2, 3

        # East-West links (X dimension) with wraparound
        for z in range(Z):
            for y in range(Y):
                for x in range(X):
                    left_node = idx(x, y, z)
                    right_node = idx((x + 1) % X, y, z)
                    
                    int_links.append(IntLink(link_id=link_count, src_node=routers[left_node], dst_node=routers[right_node], src_outport="East", dst_inport="West", latency=link_latency, weight=WX))
                    link_count += 1
                    int_links.append(IntLink(link_id=link_count, src_node=routers[right_node], dst_node=routers[left_node], src_outport="West", dst_inport="East", latency=link_latency, weight=WX))
                    link_count += 1

        # North-South links (Y dimension) with wraparound
        for z in range(Z):
            for x in range(X):
                for y in range(Y):
                    up_node = idx(x, y, z)
                    down_node = idx(x, (y + 1) % Y, z)
                    int_links.append(IntLink(link_id=link_count, src_node=routers[up_node], dst_node=routers[down_node], src_outport="North", dst_inport="South", latency=link_latency, weight=WY))
                    link_count += 1
                    int_links.append(IntLink(link_id=link_count, src_node=routers[down_node], dst_node=routers[up_node], src_outport="South", dst_inport="North", latency=link_latency, weight=WY))
                    link_count += 1

        # Up-Down links (Z dimension) with wraparound (TSV slower)
        for y in range(Y):
            for x in range(X):
                for z in range(Z):
                    front_node = idx(x, y, z)
                    back_node = idx(x, y, (z + 1) % Z)
                    int_links.append(IntLink(link_id=link_count, src_node=routers[front_node], dst_node=routers[back_node], src_outport="Up", dst_inport="Down", latency=vlink_latency, weight=WZ))
                    link_count += 1
                    int_links.append(IntLink(link_id=link_count, src_node=routers[back_node], dst_node=routers[front_node], src_outport="Down", dst_inport="Up", latency=vlink_latency, weight=WZ))
                    link_count += 1

        network.int_links = int_links

    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
