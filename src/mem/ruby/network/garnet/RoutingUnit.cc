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


#include "mem/ruby/network/garnet/RoutingUnit.hh"

#include "base/cast.hh"
#include "base/compiler.hh"
#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/garnet/Router.hh"
#include "mem/ruby/slicc_interface/Message.hh"

#include <tuple>

namespace gem5
{

namespace ruby
{

namespace garnet
{

RoutingUnit::RoutingUnit(Router *router)
{
    m_router = router;
    m_routing_table.clear();
    m_weight_table.clear();
}

void
RoutingUnit::addRoute(std::vector<NetDest>& routing_table_entry)
{
    if (routing_table_entry.size() > m_routing_table.size()) {
        m_routing_table.resize(routing_table_entry.size());
    }
    for (int v = 0; v < routing_table_entry.size(); v++) {
        m_routing_table[v].push_back(routing_table_entry[v]);
    }
}

void
RoutingUnit::addWeight(int link_weight)
{
    m_weight_table.push_back(link_weight);
}

bool
RoutingUnit::supportsVnet(int vnet, std::vector<int> sVnets)
{
    // If all vnets are supported, return true
    if (sVnets.size() == 0) {
        return true;
    }

    // Find the vnet in the vector, return true
    if (std::find(sVnets.begin(), sVnets.end(), vnet) != sVnets.end()) {
        return true;
    }

    // Not supported vnet
    return false;
}

/*
 * This is the default routing algorithm in garnet.
 * The routing table is populated during topology creation.
 * Routes can be biased via weight assignments in the topology file.
 * Correct weight assignments are critical to provide deadlock avoidance.
 */
int
RoutingUnit::lookupRoutingTable(int vnet, NetDest msg_destination)
{
    // First find all possible output link candidates
    // For ordered vnet, just choose the first
    // (to make sure different packets don't choose different routes)
    // For unordered vnet, randomly choose any of the links
    // To have a strict ordering between links, they should be given
    // different weights in the topology file

    int output_link = -1;
    int min_weight = INFINITE_;
    std::vector<int> output_link_candidates;
    int num_candidates = 0;

    // Identify the minimum weight among the candidate output links
    for (int link = 0; link < m_routing_table[vnet].size(); link++) {
        if (msg_destination.intersectionIsNotEmpty(
            m_routing_table[vnet][link])) {

        if (m_weight_table[link] <= min_weight)
            min_weight = m_weight_table[link];
        }
    }

    // Collect all candidate output links with this minimum weight
    for (int link = 0; link < m_routing_table[vnet].size(); link++) {
        if (msg_destination.intersectionIsNotEmpty(
            m_routing_table[vnet][link])) {

            if (m_weight_table[link] == min_weight) {
                num_candidates++;
                output_link_candidates.push_back(link);
            }
        }
    }

    if (output_link_candidates.size() == 0) {
        fatal("Fatal Error:: No Route exists from this Router.");
        exit(0);
    }

    // Randomly select any candidate output link
    int candidate = 0;
    if (!(m_router->get_net_ptr())->isVNetOrdered(vnet))
        candidate = rand() % num_candidates;

    output_link = output_link_candidates.at(candidate);
    return output_link;
}


void
RoutingUnit::addInDirection(PortDirection inport_dirn, int inport_idx)
{
    m_inports_dirn2idx[inport_dirn] = inport_idx;
    m_inports_idx2dirn[inport_idx]  = inport_dirn;
}

void
RoutingUnit::addOutDirection(PortDirection outport_dirn, int outport_idx)
{
    m_outports_dirn2idx[outport_dirn] = outport_idx;
    m_outports_idx2dirn[outport_idx]  = outport_dirn;
}

// outportCompute() is called by the InputUnit
// It calls the routing table by default.
// A template for adaptive topology-specific routing algorithm
// implementations using port directions rather than a static routing
// table is provided here.

int
RoutingUnit::outportCompute(RouteInfo route, int inport,
                            PortDirection inport_dirn)
{
    int outport = -1;

    if (route.dest_router == m_router->get_id()) {

        // Multiple NIs may be connected to this router,
        // all with output port direction = "Local"
        // Get exact outport id from table
        outport = lookupRoutingTable(route.vnet, route.net_dest);
        return outport;
    }

    // Routing Algorithm set in GarnetNetwork.py
    // Can be over-ridden from command line using --routing-algorithm = 1
    RoutingAlgorithm routing_algorithm =
        (RoutingAlgorithm) m_router->get_net_ptr()->getRoutingAlgorithm();

    switch (routing_algorithm) {
        case TABLE_:  outport =
            lookupRoutingTable(route.vnet, route.net_dest); break;
        case XY_:     outport =
            outportComputeXY(route, inport, inport_dirn); break;
        // any custom algorithm
        case CUSTOM_: outport =
            outportComputeCustom(route, inport, inport_dirn); break;
        case CAR3D_: outport =
            outportComputeCar3D(route, inport, inport_dirn); break;
        case ADAPTIVE_: outport =
            outportComputeAdaptive(route, inport, inport_dirn); break;
        default: outport =
            lookupRoutingTable(route.vnet, route.net_dest); break;
    }

    assert(outport != -1);
    return outport;
}

// XY routing implemented using port directions
// Only for reference purpose in a Mesh
// By default Garnet uses the routing table
int
RoutingUnit::outportComputeXY(RouteInfo route,
                              int inport,
                              PortDirection inport_dirn)
{
    PortDirection outport_dirn = "Unknown";

    [[maybe_unused]] int num_rows = m_router->get_net_ptr()->getNumRows();
    int num_cols = m_router->get_net_ptr()->getNumCols();
    assert(num_rows > 0 && num_cols > 0);

    int my_id = m_router->get_id();
    int my_x = my_id % num_cols;
    int my_y = my_id / num_cols;

    int dest_id = route.dest_router;
    int dest_x = dest_id % num_cols;
    int dest_y = dest_id / num_cols;

    int x_hops = abs(dest_x - my_x);
    int y_hops = abs(dest_y - my_y);

    bool x_dirn = (dest_x >= my_x);
    bool y_dirn = (dest_y >= my_y);

    // already checked that in outportCompute() function
    assert(!(x_hops == 0 && y_hops == 0));

    if (x_hops > 0) {
        if (x_dirn) {
            assert(inport_dirn == "Local" || inport_dirn == "West");
            outport_dirn = "East";
        } else {
            assert(inport_dirn == "Local" || inport_dirn == "East");
            outport_dirn = "West";
        }
    } else if (y_hops > 0) {
        if (y_dirn) {
            // "Local" or "South" or "West" or "East"
            assert(inport_dirn != "North");
            outport_dirn = "North";
        } else {
            // "Local" or "North" or "West" or "East"
            assert(inport_dirn != "South");
            outport_dirn = "South";
        }
    } else {
        // x_hops == 0 and y_hops == 0
        // this is not possible
        // already checked that in outportCompute() function
        panic("x_hops == y_hops == 0");
    }

    return m_outports_dirn2idx[outport_dirn];
}

int
RoutingUnit::outportEscapeVC(RouteInfo route, int inport, PortDirection inport_dirn)
{
    // If the destination is attached here, use the LOCAL outport selected by table
    if (route.dest_router == m_router->get_id()) {
        return lookupRoutingTable(route.vnet, route.net_dest);
    }

    // We need to know whether dest is in one of my child subtrees.
    // We encode subtree membership using Euler-tour tin/tout numbers that
    // GarnetNetwork will install via addChild(outport, tin, tout).
    const int dest = route.dest_router;
    const int destTin =
        m_router->get_net_ptr()->tinOf(dest);  // <-- get Euler time of dest
    const int destTout =
        m_router->get_net_ptr()->toutOf(dest);  // <-- get Euler time of dest

    // Prefer DOWN if the destination is in some child's subtree.
    // Use inclusive Euler-tour membership: [tin, tout)
    // i.e., tin(child) <= tin(dest) < tout(child)
    // This ensures we also match when dest is exactly the child.
    // DOWN traffic can only go DOWN - this breaks UP->DOWN cycles
    for (const auto &c : m_children) {
        if (destTin >= c.tin && destTin < c.tout) {
            DPRINTF(RubyNetwork, "RoutingUnit at Router %d "
                                 "routing DOWN to child via outport %d\n",
                    m_router->get_id(), c.outport);

            return c.outport; // DOWN edge
        }
    }

    // Otherwise, go UP toward the parent (if not root)
    // UP traffic can continue UP or go DOWN, but with priority ordering
    if (m_parentOutport != -1) {
        DPRINTF(RubyNetwork, "RoutingUnit at Router %d "
                                        "routing UP to parent via outport %d\n",
                            m_router->get_id(), m_parentOutport);

        return m_parentOutport;
    }

    // Root without a suitable child: fall back to table minimal (safe at root)
    DPRINTF(RubyNetwork, "RoutingUnit at Router %d "
                         "falling back to original routing (ROOT)\n",
            m_router->get_id());

    return lookupRoutingTable(route.vnet, route.net_dest);
}

// Template for implementing custom routing algorithm
// using port directions. (Example adaptive)
int
RoutingUnit::outportComputeCustom(RouteInfo route,
                                 int inport,
                                 PortDirection inport_dirn)
{
    panic("%s placeholder executed", __FUNCTION__);
}

int
RoutingUnit::outportComputeAdaptive(RouteInfo route,
                                 int inport,
                                 PortDirection inport_dirn)
{
    // If destination NI is attached to this router, use LOCAL outport from table
    if (route.dest_router == m_router->get_id()) {
        return lookupRoutingTable(route.vnet, route.net_dest);
    }

    // 1) Collect minimal outport candidates using the routing table
    const int vnet = route.vnet;
    if (vnet < 0 || vnet >= (int)m_routing_table.size()) {
        // Fallback: use table directly if vnet is out of range
        return lookupRoutingTable(route.vnet, route.net_dest);
    }

    int min_weight = INFINITE_;
    std::vector<int> candidates;
    candidates.reserve(m_routing_table[vnet].size());

    // Find minimum link weight among links that can reach destination
    for (int link = 0; link < (int)m_routing_table[vnet].size(); link++) {
        if (route.net_dest.intersectionIsNotEmpty(m_routing_table[vnet][link])) {
            if (m_weight_table[link] <= min_weight)
                min_weight = m_weight_table[link];
        }
    }
    // Collect links whose weight == min_weight and that reach the destination
    for (int link = 0; link < (int)m_routing_table[vnet].size(); link++) {
        if (route.net_dest.intersectionIsNotEmpty(m_routing_table[vnet][link])) {
            if (m_weight_table[link] == min_weight) {
                candidates.push_back(link);
            }
        }
    }

    if (candidates.empty()) {
        // No route exists; keep behavior consistent with TABLE_ mode
        fatal("Fatal Error:: No Route exists from this Router.");
        return -1;
    }

    // 2) If only one candidate, use it
    if (candidates.size() == 1) {
        return candidates.front();
    }

    // 3) Rank candidates by downstream free credits on this vnet (exclude escape VC)
    auto creditScore = [&](int outport) -> int {
        auto *outU = m_router->getOutputUnit(outport);
        if (!outU) return -1; // prefer any valid over invalid
        const int vcs_per_vnet = m_router->get_vc_per_vnet();
        const bool escape_en   = m_router->is_escape_vc_enabled();
        int base = vnet * vcs_per_vnet;
        int sum = 0;
        for (int off = 0; off < vcs_per_vnet; ++off) {
            if (escape_en && off == 0) continue; // exclude escape
            sum += outU->get_credit_count(base + off);
        }
        return sum;
    };

    int best = candidates.front();
    int bestScore = creditScore(best);

    for (size_t i = 1; i < candidates.size(); ++i) {
        int c = candidates[i];
        int s = creditScore(c);
        if (s > bestScore) {
            best = c; bestScore = s;
        }
    }

    // 4) Tie-break using per-inport round-robin among top-scored candidates
    // Collect equal-top candidates
    std::vector<int> top;
    top.reserve(candidates.size());
    for (int c : candidates) {
        if (creditScore(c) == bestScore) top.push_back(c);
    }
    if (top.size() == 1) return top.front();

    unsigned &rr = m_rr_by_inport[inport];
    int choice = top[rr % top.size()];
    rr++;
    return choice;
}

void RoutingUnit::ensureEwmaSized()
{
    const int num_outports = m_outports_idx2dirn.size();
    const int num_vnets = m_router->get_num_vnets();
    if ((int)m_outport_ewma.size() != num_outports) {
        m_outport_ewma.resize(num_outports);
    }
    for (int op = 0; op < num_outports; ++op) {
        if ((int)m_outport_ewma[op].size() != num_vnets) {
            m_outport_ewma[op].assign(num_vnets, 0.0);
        }
    }
}

void RoutingUnit::updateEwma(int outport, int vnet, int observedCredits)
{
    ensureEwmaSized();
    if (outport < 0 || outport >= (int)m_outport_ewma.size()) return;
    if (vnet < 0 || vnet >= (int)m_outport_ewma[outport].size()) return;
    constexpr double lambda = 0.2; // smoothing factor
    double &ew = m_outport_ewma[outport][vnet];
    ew = (1.0 - lambda) * ew + lambda * (double)observedCredits;
}

int
RoutingUnit::outportComputeCar3D(RouteInfo route,
                                 int inport,
                                 PortDirection inport_dirn)
{
    // If destination NI is attached to this router, use LOCAL outport from table
    if (route.dest_router == m_router->get_id()) {
        return lookupRoutingTable(route.vnet, route.net_dest);
    }

    const int vnet = route.vnet;
    if (vnet < 0 || vnet >= (int)m_routing_table.size()) {
        return lookupRoutingTable(route.vnet, route.net_dest);
    }

    // Build minimal candidate set using table min-weight filtering
    int min_weight = INFINITE_;
    std::vector<int> candidates;
    for (int link = 0; link < (int)m_routing_table[vnet].size(); link++) {
        if (route.net_dest.intersectionIsNotEmpty(m_routing_table[vnet][link])) {
            if (m_weight_table[link] <= min_weight)
                min_weight = m_weight_table[link];
        }
    }
    for (int link = 0; link < (int)m_routing_table[vnet].size(); link++) {
        if (route.net_dest.intersectionIsNotEmpty(m_routing_table[vnet][link]) &&
            m_weight_table[link] == min_weight) {
            candidates.push_back(link);
        }
    }
    if (candidates.empty()) {
        fatal("Fatal Error:: No Route exists from this Router.");
        return -1;
    }
    if (candidates.size() == 1) return candidates.front();

    ensureEwmaSized();

    auto localCredits = [&](int outport) -> int {
        auto *outU = m_router->getOutputUnit(outport);
        if (!outU) return -1;
        const int vcs_per_vnet = m_router->get_vc_per_vnet();
        const bool escape_en   = m_router->is_escape_vc_enabled();
        int base = vnet * vcs_per_vnet;
        int sum = 0;
        for (int off = 0; off < vcs_per_vnet; ++off) {
            if (escape_en && off == 0) continue; // exclude escape VC
            sum += outU->get_credit_count(base + off);
        }
        return sum;
    };

    constexpr double alpha = 1.0;
    constexpr double beta  = 0.5;

    // Compute best score
    double bestScore = -1e18;
    for (int c : candidates) {
        double score = alpha * (double)localCredits(c) + beta * m_outport_ewma[c][vnet];
        if (score > bestScore) bestScore = score;
    }

    // Keep only top-scored candidates (within epsilon)
    const double eps = 1e-9;
    std::vector<int> top;
    for (int c : candidates) {
        double score = alpha * (double)localCredits(c) + beta * m_outport_ewma[c][vnet];
        if (score + eps >= bestScore) top.push_back(c);
    }

    // Stickiness: prefer last choice if it is still in top set
    std::tuple<int,int,int> key{inport, vnet, route.dest_router};
    auto it = m_lastChoice.find(key);
    if (it != m_lastChoice.end()) {
        int last = it->second;
        for (int c : top) if (c == last) return last;
    }

    // Round-robin among top candidates
    unsigned &rr = m_rr_by_inport[inport];
    int choice = top[rr % top.size()];
    rr++;
    m_lastChoice[key] = choice;
    return choice;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
