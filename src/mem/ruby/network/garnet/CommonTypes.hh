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


#ifndef __MEM_RUBY_NETWORK_GARNET_0_COMMONTYPES_HH__
#define __MEM_RUBY_NETWORK_GARNET_0_COMMONTYPES_HH__

#include "mem/ruby/common/NetDest.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

// All common enums and typedefs go here

// Extend flit types with control messages used by SPIN-style control flow.
// PROBE_/MOVE_/CHECK_PROBE_/KILL_MOVE_ are no-ops in Garnet3.0 by default,
// but enabled when the SPIN scheme is turned on via GarnetNetwork.
enum flit_type {
    HEAD_, BODY_, TAIL_, HEAD_TAIL_,
    // SPIN control flits (optional):
    PROBE_, MOVE_, CHECK_PROBE_, KILL_MOVE_,
    CREDIT_,
    NUM_FLIT_TYPE_
};
enum VC_state_type {IDLE_, VC_AB_, ACTIVE_, NUM_VC_STATE_TYPE_};
enum VNET_type {CTRL_VNET_, DATA_VNET_, NULL_VNET_, NUM_VNET_TYPE_};
enum flit_stage {I_, VA_, SA_, ST_, LT_, NUM_FLIT_STAGE_};
enum link_type { EXT_IN_, EXT_OUT_, INT_, NUM_LINK_TYPES_ };
enum RoutingAlgorithm {
    TABLE_   = 0,
    XY_      = 1,
    CUSTOM_  = 2,
    ADAPTIVE_ = 3,  // adaptive minimal, credit-aware (3D-ready)
    CAR3D_    = 4,  // CAR-3D: EWMA + lookahead-inspired scoring
    UGAL_     = 5,  // UGAL-L (local), single-segment non-minimal at source
    NUM_ROUTING_ALGORITHM_
};

// SPIN: per-router move registry entry
struct move_info {
    int inport;                    // input port at this router
    int vc;                        // input VC index at this router
    int outport;                   // chosen outport for the move
    int vc_at_downstream_router;   // input VC at next router (optional)
    bool tail_moved;               // mark when tail is moved
    int cur_move_count;            // number of flits moved so far
};

struct RouteInfo
{
    RouteInfo()
        : vnet(0), src_ni(0), src_router(0), dest_ni(0), dest_router(0),
          hops_traversed(0)
    {}

    // destination format for table-based routing
    int vnet;
    NetDest net_dest;

    // src and dest format for topology-specific routing
    int src_ni;
    int src_router;
    int dest_ni;
    int dest_router;
    int hops_traversed;
};

#define INFINITE_ 10000

// Lightweight counter state for SPIN-style deadlock handling. These states are
// used only when SPIN support is enabled (see GarnetNetwork::enable_spin_scheme).
enum Counter_state {
    s_off,
    s_move,
    s_frozen,
    s_deadlock_detection,
    s_forward_progress,
    s_check_probe,
    num_cntr_states
};

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif //__MEM_RUBY_NETWORK_GARNET_0_COMMONTYPES_HH__
