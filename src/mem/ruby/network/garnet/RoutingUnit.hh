/*
 * Copyright (c) 2008 Princeton University
 * Copyright (c) 2016 Georgia Institute of Technology
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */


#ifndef __MEM_RUBY_NETWORK_GARNET_0_ROUTINGUNIT_HH__
#define __MEM_RUBY_NETWORK_GARNET_0_ROUTINGUNIT_HH__

#include "mem/ruby/common/Consumer.hh"
#include "mem/ruby/common/NetDest.hh"
#include "mem/ruby/network/garnet/CommonTypes.hh"
#include "mem/ruby/network/garnet/GarnetNetwork.hh"
#include "mem/ruby/network/garnet/flit.hh"

#include <map>
#include <tuple>
#include <vector>

namespace gem5
{

namespace ruby
{

namespace garnet
{

class InputUnit;
class Router;

class RoutingUnit
{
  public:
    RoutingUnit(Router *router);
    int outportCompute(RouteInfo route,
                      int inport,
                      PortDirection inport_dirn);

    // Topology-agnostic Routing Table based routing (default)
    void addRoute(std::vector<NetDest>& routing_table_entry);
    void addWeight(int link_weight);

    // get output port from routing table
    int  lookupRoutingTable(int vnet, NetDest net_dest);

    // Topology-specific direction based routing
    void addInDirection(PortDirection inport_dirn, int inport);
    void addOutDirection(PortDirection outport_dirn, int outport);

    // Routing for Mesh
    int outportComputeXY(RouteInfo route,
                         int inport,
                         PortDirection inport_dirn);

    // Custom Routing Algorithm using Port Directions
    int outportComputeCustom(RouteInfo route,
                             int inport,
                             PortDirection inport_dirn);

    // CAR-3D: EWMA + lookahead-inspired scoring
    int outportComputeCar3D(RouteInfo route,
                            int inport,
                            PortDirection inport_dirn);

    // Adaptive Routing Algorithm using Port Directions
    int outportComputeAdaptive(RouteInfo route,
                               int inport,
                               PortDirection inport_dirn);

    // UGAL-L (local): choose between minimal and one non-minimal first hop at source
    int outportComputeUGAL(RouteInfo route,
                           int inport,
                           PortDirection inport_dirn);

    // --- add near the top of class RoutingUnit public: ---
    int outportEscapeVC(RouteInfo route, int inport, PortDirection inport_dirn);
    int outportIndex(PortDirection dir) const {
        auto it = m_outports_dirn2idx.find(dir);
        return (it == m_outports_dirn2idx.end()) ? -1 : it->second;
    }

    // Install escape-tree info per router
    void setTreeDepth(int depth) { m_treeDepth = depth; }
    int getTreeDepth() const { return m_treeDepth; }
    void setParentOutport(int outport) { m_parentOutport = outport; }

    struct ChildInfo { int outport; int tin; int tout; };
    void clearChildren() { m_children.clear(); }
    void addChild(int outport, int tin, int tout) { m_children.push_back({outport,tin,tout}); }
    std::vector<ChildInfo> getChildren() const { return m_children; }
    // Returns true if vnet is present in the vector
    // of vnets or if the vector supports all vnets.
    bool supportsVnet(int vnet, std::vector<int> sVnets);

    // Get the direction for idx
    PortDirection getDirection(int idx) const {
        auto it = m_outports_idx2dirn.find(idx);
        return (it == m_outports_idx2dirn.end()) ? "INVALID" : it->second;
    }

    int getParentOutport() const { return m_parentOutport; }
    PortDirection getParentOutportDirection() const {
        return getDirection(m_parentOutport);
    }

    // CAR-3D EWMA updater (called from OutputUnit on send)
    void updateEwma(int outport, int vnet, int observedCredits);

  private:
    Router *m_router;
    int m_treeDepth = -1;
    int m_parentOutport = -1;
    std::vector<ChildInfo> m_children;
    // Tie-breaker state for adaptive selection (per inport)
    std::map<int, unsigned> m_rr_by_inport;

    // Routing Table
    std::vector<std::vector<NetDest>> m_routing_table;
    std::vector<int> m_weight_table;

    // Inport and Outport direction to idx maps
    std::map<PortDirection, int> m_inports_dirn2idx;
    std::map<int, PortDirection> m_inports_idx2dirn;
    std::map<int, PortDirection> m_outports_idx2dirn;
    std::map<PortDirection, int> m_outports_dirn2idx;

    // CAR-3D state
    // Per-outport, per-vnet EWMA of observed free credits
    std::vector<std::vector<double>> m_outport_ewma; // [outport][vnet]
    std::map<std::tuple<int,int,int>, int> m_lastChoice; // key=(inport,vnet,dst)

    void ensureEwmaSized();
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif // __MEM_RUBY_NETWORK_GARNET_0_ROUTINGUNIT_HH__
