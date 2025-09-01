# Copyright (c) 2010 Advanced Micro Devices, Inc.
#               2016 Georgia Institute of Technology
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from m5.params import *
from m5.objects import *

from common import FileSystemConfig

from topologies.BaseTopology import SimpleTopology

# 4x4x4 3D Mesh (no wrap). Port names follow Mesh_XY style:
# +X: East, -X: West; +Y: North, -Y: South; +Z: Up, -Z: Down.
# File name distinguishes it as the topology we use with XYZ routing.


class Mesh3D_XYZ(SimpleTopology):
    description = "Mesh3D_XYZ"

    def __init__(self, controllers):
        self.nodes = controllers

    # Build a 4x4x4 mesh (64 routers). Compatible with both TABLE_ (0) and XYZ_ (3).
    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes

        num_routers = options.num_cpus
        num_rows = options.mesh_rows  # keep the same interface as Mesh_XY

        # default values for link latency and router latency.
        link_latency = options.link_latency  # used by simple and garnet
        router_latency = options.router_latency  # only used by garnet

        # Fixed geometry: 4x4x4
        X, Y, Z = 4, 4, 4
        assert num_rows > 0
        assert num_routers == X * Y * Z, "Mesh3D_XYZ_ requires --num-cpus=64"
        assert num_rows == Y, "Mesh3D_XYZ_ requires --mesh-rows=4"

        # There must be an evenly divisible number of cntrls to routers
        cntrls_per_router, remainder = divmod(len(nodes), num_routers)

        # Create the routers in the 3D mesh
        routers = [
            Router(router_id=i, latency=router_latency)
            for i in range(num_routers)
        ]
        network.routers = routers

        # link counter to set unique link ids
        link_count = 0

        # Distribute controllers uniformly across routers (same as Mesh_XY)
        network_nodes = []
        remainder_nodes = []
        for node_index in range(len(nodes)):
            if node_index < (len(nodes) - remainder):
                network_nodes.append(nodes[node_index])
            else:
                remainder_nodes.append(nodes[node_index])

        # Connect each node to the appropriate router (uniform)
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

        # Create the 3D mesh internal links (bidirectional)
        int_links = []

        # Helper: linear index z*(X*Y) + y*X + x (must match C++ routing's decode)
        def idx(x, y, z):
            return z * (X * Y) + y * X + x

        # Weights: follow Mesh_XY style. X=1, Y=2; use Z=3.
        WX, WY, WZ = 1, 2, 3

        # +X / -X (weight = 1)
        for z in range(Z):
            for y in range(Y):
                for x in range(X - 1):
                    a = idx(x, y, z)
                    b = idx(x + 1, y, z)
                    # a -> b : East, b -> a : West
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="East",
                            dst_inport="West",
                            latency=link_latency,
                            weight=WX,
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
                            weight=WX,
                        )
                    )
                    link_count += 1

        # +Y / -Y (weight = 2) — +Y uses "North", -Y uses "South" (Mesh_XY convention)
        for z in range(Z):
            for x in range(X):
                for y in range(Y - 1):
                    a = idx(x, y, z)
                    b = idx(x, y + 1, z)
                    # a -> b : North, b -> a : South
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="North",
                            dst_inport="South",
                            latency=link_latency,
                            weight=WY,
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
                            weight=WY,
                        )
                    )
                    link_count += 1

        # +Z / -Z (weight = 3)
        for y in range(Y):
            for x in range(X):
                for z in range(Z - 1):
                    a = idx(x, y, z)
                    b = idx(x, y, z + 1)
                    # a -> b : Up, b -> a : Down
                    int_links.append(
                        IntLink(
                            link_id=link_count,
                            src_node=routers[a],
                            dst_node=routers[b],
                            src_outport="Up",
                            dst_inport="Down",
                            latency=link_latency,
                            weight=WZ,
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
                            weight=WZ,
                        )
                    )
                    link_count += 1

        network.int_links = int_links

    # Register nodes with filesystem (same as Mesh_XY)
    def registerTopology(self, options):
        for i in range(options.num_cpus):
            FileSystemConfig.register_node(
                [i], MemorySize(options.mem_size) // options.num_cpus, i
            )
